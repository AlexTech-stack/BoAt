"""Bulk statistics engine for Ethernet (.pcap) trace analysis.

Mirrors :mod:`boat.trace_analyzer`'s CAN-side design -- one pass over the
capture produces per-flow and per-node statistics instead of attempting
per-signal reverse engineering (that's a CAN-specific problem; Ethernet
payloads already carry their own structure via DoIP/SOME/IP/etc.). What
this *does* produce, analogous to CAN's per-ID cycle time and Cyclic/
Spontaneous classification:

- Protocol identification: EtherType/VLAN histograms, well-known-port
  recognition (DoIP), and payload-shape recognition (SOME/IP header).
- Node/topology inventory: MAC and IP address inventories, which VLAN(s)
  each flow was observed on.
- TCP session reconstruction: SYN/SYN-ACK-based client/server role
  detection, merging both directions of a connection into one session
  record instead of two independent flows.
- Cyclic vs. event-driven classification per UDP flow, using the same
  underlying idea as CAN cycle-time detection (inter-frame gap
  consistency), generalized with a coefficient-of-variation threshold
  instead of a canonical-raster snap (Ethernet flows don't have an
  equivalent of CAN's small set of standard bus cycle times).

Usage::

    from boat.eth_trace_analyzer import EthTraceAnalyzer

    analyzer = EthTraceAnalyzer("capture.pcap")
    analysis = analyzer.analyze()
    summary = analyzer.to_summary()
"""

from __future__ import annotations

import socket
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Optional

from boat.trace_replay import EthernetPcapReader, TraceReplayError

# ── well-known automotive-Ethernet protocol signatures ──────────────────

DOIP_PORT = 13400
SOMEIP_SD_PORT = 30490

# SOME/IP MessageType values (ISO/PRS SOME/IP protocol spec) -- REQUEST,
# REQUEST_NO_RETURN, NOTIFICATION, REQUEST_ACK, their TP (segmented)
# variants (0x20-range), and RESPONSE/ERROR plus their TP variants.
_SOMEIP_MSG_TYPES = {0x00, 0x01, 0x02, 0x04, 0x20, 0x21, 0x22, 0x24, 0x80, 0x81, 0xA0, 0xA1}

ETHERTYPE_NAMES = {
    0x0800: "IPv4",
    0x0806: "ARP",
    0x86DD: "IPv6",
    0x8100: "VLAN",
    0x88A8: "QinQ",
    0x88A4: "EtherCAT",
    0x88F7: "gPTP",
    0x22F0: "AVTP",
    0x8917: "AVDECC",
    0x8892: "PROFINET",
}

IP_PROTO_NAMES = {1: "ICMP", 2: "IGMP", 6: "TCP", 17: "UDP", 58: "ICMPv6"}

# How many inter-frame gaps to keep per flow for cycle-time/jitter
# statistics -- bounded so a huge flow (hundreds of thousands of frames)
# doesn't blow up memory; a few hundred samples is plenty to judge
# periodicity.
_GAP_SAMPLE_LIMIT = 500

# How many frames per flow to run the SOME/IP header-shape check against --
# the header is either fixed-shape or it isn't, so a small sample is
# enough to decide, and it keeps the single analysis pass cheap even on
# flows with hundreds of thousands of frames.
_SOMEIP_CHECK_LIMIT = 20


def _looks_like_someip(payload: bytes) -> bool:
    """Check the fixed 16-byte SOME/IP header shape: MessageID(4) +
    Length(4) + RequestID(4) + ProtocolVersion(1) + InterfaceVersion(1) +
    MessageType(1) + ReturnCode(1). The Length field must exactly match
    the remaining payload size (everything after the Length field itself),
    ProtocolVersion is always 1, and MessageType is drawn from a small
    fixed set -- three independent, cheap checks unlikely to all pass by
    chance on a non-SOME/IP payload of the same size.
    """
    if len(payload) < 16:
        return False
    length_field = int.from_bytes(payload[4:8], "big")
    if length_field != len(payload) - 8:
        return False
    if payload[12] != 1:
        return False
    return payload[14] in _SOMEIP_MSG_TYPES


def someip_header_fields(payload: bytes) -> dict[str, int] | None:
    """Parse SOME/IP header fields if `payload` matches the header shape,
    else None."""
    if not _looks_like_someip(payload):
        return None
    return {
        "service_id": int.from_bytes(payload[0:2], "big"),
        "method_id": int.from_bytes(payload[2:4], "big"),
        "length": int.from_bytes(payload[4:8], "big"),
        "request_id": int.from_bytes(payload[8:12], "big"),
        "protocol_version": payload[12],
        "interface_version": payload[13],
        "message_type": payload[14],
        "return_code": payload[15],
    }


def _is_multicast_ip(ip: str) -> bool:
    return ip.startswith("ff") or ip.startswith("224.") or ip.startswith("23") and "." in ip and int(ip.split(".")[0]) in range(224, 240)


# ── per-flow / per-session statistics ────────────────────────────────────

@dataclass
class FlowStats:
    """Statistics for one UDP flow -- a (src_ip, dst_ip, src_port, dst_port)
    4-tuple. UDP is connectionless, so unlike TCP there's no session
    merging: each 4-tuple is its own logical channel (this matches how
    SOME/IP/DoIP-UDP endpoints actually behave -- a sender's port is
    typically fixed per logical channel, not a fresh ephemeral one per
    "connection" the way a new TCP socket would be)."""
    src_ip: str
    dst_ip: str
    src_port: int
    dst_port: int
    vlan_ids: set[int] = field(default_factory=set)
    frame_count: int = 0
    byte_count: int = 0
    first_ts: float = 0.0
    last_ts: float = 0.0
    someip_like_count: int = 0
    someip_checked_count: int = 0
    _last_ts_for_gap: float = field(default=0.0, repr=False)
    _gap_samples: list[float] = field(default_factory=list, repr=False)

    @property
    def is_multicast_dst(self) -> bool:
        return _is_multicast_ip(self.dst_ip)

    @property
    def is_doip_port(self) -> bool:
        return self.src_port == DOIP_PORT or self.dst_port == DOIP_PORT

    @property
    def is_someip_sd(self) -> bool:
        return self.src_port == SOMEIP_SD_PORT or self.dst_port == SOMEIP_SD_PORT

    @property
    def is_someip_like(self) -> bool:
        """True if most sampled payloads on this flow match the SOME/IP
        header shape -- "most" rather than "all" since a flow can carry a
        handful of malformed/truncated frames without that meaning the
        protocol guess is wrong."""
        return self.someip_checked_count > 0 and (
            self.someip_like_count / self.someip_checked_count > 0.8
        )

    @property
    def duration_s(self) -> float:
        return max(0.0, self.last_ts - self.first_ts)

    @property
    def cycle_time_ms(self) -> float:
        if len(self._gap_samples) < 3:
            return 0.0
        return statistics.mean(self._gap_samples) * 1000

    @property
    def cycle_jitter_cv(self) -> float:
        """Coefficient of variation of inter-frame gaps: near 0 means
        strictly periodic (a "Cyclic" CAN-style signal), high means
        bursty/multiplexed/event-driven traffic."""
        if len(self._gap_samples) < 3:
            return 0.0
        mean = statistics.mean(self._gap_samples)
        if mean <= 0:
            return 0.0
        return statistics.stdev(self._gap_samples) / mean

    @property
    def send_type(self) -> str:
        if self.frame_count < 5:
            return "Spontaneous"
        return "Cyclic" if self.cycle_jitter_cv < 0.3 else "Bursty"


@dataclass
class TcpSession:
    """A reconstructed TCP connection -- both directions merged via
    SYN/SYN-ACK role detection into one record, rather than left as two
    independent, direction-specific flows."""
    endpoint_a: tuple[str, int]
    endpoint_b: tuple[str, int]
    vlan_ids: set[int] = field(default_factory=set)
    frames_a_to_b: int = 0
    frames_b_to_a: int = 0
    bytes_a_to_b: int = 0
    bytes_b_to_a: int = 0
    first_ts: float = 0.0
    last_ts: float = 0.0
    client_endpoint: tuple[str, int] | None = None   # sender of the bare SYN
    server_endpoint: tuple[str, int] | None = None   # sender of the SYN-ACK
    saw_fin_or_rst: bool = False

    @property
    def role_confidence(self) -> str:
        return "confirmed" if self.client_endpoint and self.server_endpoint else "unknown"

    @property
    def is_doip(self) -> bool:
        return self.endpoint_a[1] == DOIP_PORT or self.endpoint_b[1] == DOIP_PORT

    @property
    def total_frames(self) -> int:
        return self.frames_a_to_b + self.frames_b_to_a


@dataclass
class EthTraceAnalysis:
    """Result of analyzing an Ethernet .pcap trace file."""
    path: str
    total_frames: int = 0
    first_ts: float = 0.0
    last_ts: float = 0.0
    ethertype_counts: Counter = field(default_factory=Counter)
    vlan_counts: Counter = field(default_factory=Counter)
    mac_src_counts: Counter = field(default_factory=Counter)
    mac_dst_counts: Counter = field(default_factory=Counter)
    ip_proto_counts: Counter = field(default_factory=Counter)
    mac_to_ips: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    ip_frames_sent: Counter = field(default_factory=Counter)
    ip_frames_received: Counter = field(default_factory=Counter)
    ip_vlan_seen: dict[str, set[int]] = field(default_factory=lambda: defaultdict(set))
    udp_flows: dict[tuple, FlowStats] = field(default_factory=dict)
    tcp_sessions: dict[tuple, TcpSession] = field(default_factory=dict)
    someip_catalog: Counter = field(default_factory=Counter)  # (service_id, method_id) -> count
    errors: list[str] = field(default_factory=list)

    @property
    def duration_s(self) -> float:
        return max(0.0, self.last_ts - self.first_ts)


# ── analyzer ──────────────────────────────────────────────────────────────

class EthTraceAnalyzer:
    """Bulk-analyze an Ethernet .pcap capture.

    Args:
        path: Path to a DLT_EN10MB pcap file.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        self._analysis: EthTraceAnalysis | None = None

    def analyze(self) -> EthTraceAnalysis:
        """Single pass over the capture. Builds protocol/VLAN histograms,
        node inventories, UDP flow stats (with cyclic/event
        classification and SOME/IP recognition), and TCP session
        reconstruction (with client/server role detection)."""
        result = EthTraceAnalysis(path=self._path)

        try:
            with EthernetPcapReader(self._path) as reader:
                for frame in reader:
                    self._process_frame(frame, result)
        except TraceReplayError as e:
            result.errors.append(str(e))

        self._analysis = result
        return result

    def _process_frame(self, frame, result: EthTraceAnalysis) -> None:
        result.total_frames += 1
        if result.first_ts == 0.0:
            result.first_ts = frame.timestamp
        result.last_ts = frame.timestamp

        result.mac_src_counts[frame.src_mac] += 1
        result.mac_dst_counts[frame.dst_mac] += 1

        payload, ethertype = frame.payload, frame.ethertype
        vlan_id: int | None = None
        # Peel up to two stacked VLAN tags (QinQ); track the outermost
        # ethertype seen for the histogram, and the last (innermost) VLAN
        # ID as "the" VLAN this frame was observed on.
        for _ in range(2):
            if ethertype == 0x8100 and len(payload) >= 4:
                tci = (payload[0] << 8) | payload[1]
                vlan_id = tci & 0x0FFF
                result.vlan_counts[vlan_id] += 1
                ethertype = (payload[2] << 8) | payload[3]
                payload = payload[4:]
            else:
                break

        result.ethertype_counts[ethertype] += 1

        if ethertype == 0x0800:
            self._process_ipv4(payload, vlan_id, frame.timestamp, result)
        elif ethertype == 0x86DD:
            self._process_ipv6(payload, vlan_id, frame.timestamp, result)

    def _process_ipv4(self, payload: bytes, vlan_id: int | None, ts: float, result: EthTraceAnalysis) -> None:
        if len(payload) < 20:
            return
        ihl = (payload[0] & 0x0F) * 4
        if ihl < 20 or len(payload) < ihl:
            return
        proto = payload[9]
        src_ip = socket.inet_ntoa(payload[12:16])
        dst_ip = socket.inet_ntoa(payload[16:20])
        self._process_ip_common(payload[ihl:], proto, src_ip, dst_ip, vlan_id, ts, result)

    def _process_ipv6(self, payload: bytes, vlan_id: int | None, ts: float, result: EthTraceAnalysis) -> None:
        if len(payload) < 40:
            return
        proto = payload[6]
        src_ip = socket.inet_ntop(socket.AF_INET6, payload[8:24])
        dst_ip = socket.inet_ntop(socket.AF_INET6, payload[24:40])
        self._process_ip_common(payload[40:], proto, src_ip, dst_ip, vlan_id, ts, result)

    def _process_ip_common(
        self, l4: bytes, proto: int, src_ip: str, dst_ip: str, vlan_id: int | None, ts: float,
        result: EthTraceAnalysis,
    ) -> None:
        result.ip_proto_counts[proto] += 1
        result.ip_frames_sent[src_ip] += 1
        result.ip_frames_received[dst_ip] += 1
        if vlan_id is not None:
            result.ip_vlan_seen[src_ip].add(vlan_id)
            result.ip_vlan_seen[dst_ip].add(vlan_id)

        if proto == 17 and len(l4) >= 8:
            self._process_udp(l4, src_ip, dst_ip, vlan_id, ts, result)
        elif proto == 6 and len(l4) >= 14:
            self._process_tcp(l4, src_ip, dst_ip, vlan_id, ts, result)

    def _process_udp(
        self, l4: bytes, src_ip: str, dst_ip: str, vlan_id: int | None, ts: float,
        result: EthTraceAnalysis,
    ) -> None:
        sport = (l4[0] << 8) | l4[1]
        dport = (l4[2] << 8) | l4[3]
        key = (src_ip, dst_ip, sport, dport)
        flow = result.udp_flows.get(key)
        if flow is None:
            flow = FlowStats(src_ip=src_ip, dst_ip=dst_ip, src_port=sport, dst_port=dport, first_ts=ts)
            result.udp_flows[key] = flow

        if flow.frame_count > 0 and len(flow._gap_samples) < _GAP_SAMPLE_LIMIT:
            gap = ts - flow._last_ts_for_gap
            if gap >= 0:
                flow._gap_samples.append(gap)
        flow._last_ts_for_gap = ts
        flow.frame_count += 1
        flow.byte_count += len(l4)
        flow.last_ts = ts
        if vlan_id is not None:
            flow.vlan_ids.add(vlan_id)

        udp_payload = l4[8:]
        if flow.someip_checked_count < _SOMEIP_CHECK_LIMIT:
            flow.someip_checked_count += 1
            fields = someip_header_fields(udp_payload)
            if fields is not None:
                flow.someip_like_count += 1
                result.someip_catalog[(fields["service_id"], fields["method_id"])] += 1

    def _process_tcp(
        self, l4: bytes, src_ip: str, dst_ip: str, vlan_id: int | None, ts: float,
        result: EthTraceAnalysis,
    ) -> None:
        sport = (l4[0] << 8) | l4[1]
        dport = (l4[2] << 8) | l4[3]
        flags = l4[13]
        payload_len = len(l4) - ((l4[12] >> 4) * 4)

        a, b = (src_ip, sport), (dst_ip, dport)
        key = tuple(sorted((a, b)))
        sess = result.tcp_sessions.get(key)
        if sess is None:
            sess = TcpSession(endpoint_a=key[0], endpoint_b=key[1], first_ts=ts)
            result.tcp_sessions[key] = sess

        sess.last_ts = ts
        if vlan_id is not None:
            sess.vlan_ids.add(vlan_id)
        if a == sess.endpoint_a:
            sess.frames_a_to_b += 1
            sess.bytes_a_to_b += max(0, payload_len)
        else:
            sess.frames_b_to_a += 1
            sess.bytes_b_to_a += max(0, payload_len)

        syn = bool(flags & 0x02)
        ack = bool(flags & 0x10)
        fin = bool(flags & 0x01)
        rst = bool(flags & 0x04)
        if syn and not ack and sess.client_endpoint is None:
            sess.client_endpoint = a
        elif syn and ack and sess.server_endpoint is None:
            sess.server_endpoint = a
        if fin or rst:
            sess.saw_fin_or_rst = True

    # ── node inventory (derived view, not tracked during the pass) ──────

    def node_inventory(self, result: EthTraceAnalysis | None = None) -> list[dict[str, Any]]:
        """Per-IP node summary: frames sent/received, VLANs observed on."""
        result = result or self._analysis
        if result is None:
            raise RuntimeError("Call analyze() first")
        ips = set(result.ip_frames_sent) | set(result.ip_frames_received)
        nodes = []
        for ip in ips:
            nodes.append({
                "ip": ip,
                "frames_sent": result.ip_frames_sent.get(ip, 0),
                "frames_received": result.ip_frames_received.get(ip, 0),
                "vlan_ids": sorted(result.ip_vlan_seen.get(ip, set())),
                "is_multicast": _is_multicast_ip(ip),
            })
        nodes.sort(key=lambda n: -(n["frames_sent"] + n["frames_received"]))
        return nodes

    def doip_servers(self, result: EthTraceAnalysis | None = None) -> list[str]:
        """IPs that responded to a DoIP (port 13400) SYN with a SYN-ACK --
        i.e. confirmed DoIP servers, not just anything that ever touched
        the port."""
        result = result or self._analysis
        if result is None:
            raise RuntimeError("Call analyze() first")
        servers = set()
        for sess in result.tcp_sessions.values():
            if sess.server_endpoint and sess.server_endpoint[1] == DOIP_PORT:
                servers.add(sess.server_endpoint[0])
        return sorted(servers)

    def to_summary(self, result: EthTraceAnalysis | None = None) -> dict[str, Any]:
        """JSON-serializable summary for the web UI / export."""
        result = result or self._analysis
        if result is None:
            raise RuntimeError("Call analyze() first")

        udp_flows = []
        for flow in result.udp_flows.values():
            udp_flows.append({
                "proto": "UDP",
                "src_ip": flow.src_ip,
                "dst_ip": flow.dst_ip,
                "src_port": flow.src_port,
                "dst_port": flow.dst_port,
                "vlan_ids": sorted(flow.vlan_ids),
                "frame_count": flow.frame_count,
                "byte_count": flow.byte_count,
                "duration_s": round(flow.duration_s, 3),
                "cycle_time_ms": round(flow.cycle_time_ms, 3),
                "send_type": flow.send_type,
                "is_multicast_dst": flow.is_multicast_dst,
                "is_doip_port": flow.is_doip_port,
                "is_someip_sd": flow.is_someip_sd,
                "is_someip_like": flow.is_someip_like,
            })
        udp_flows.sort(key=lambda f: -f["frame_count"])

        tcp_sessions = []
        for sess in result.tcp_sessions.values():
            tcp_sessions.append({
                "proto": "TCP",
                "endpoint_a": f"{sess.endpoint_a[0]}:{sess.endpoint_a[1]}",
                "endpoint_b": f"{sess.endpoint_b[0]}:{sess.endpoint_b[1]}",
                "client": f"{sess.client_endpoint[0]}:{sess.client_endpoint[1]}" if sess.client_endpoint else None,
                "server": f"{sess.server_endpoint[0]}:{sess.server_endpoint[1]}" if sess.server_endpoint else None,
                "role_confidence": sess.role_confidence,
                "vlan_ids": sorted(sess.vlan_ids),
                "total_frames": sess.total_frames,
                "bytes_a_to_b": sess.bytes_a_to_b,
                "bytes_b_to_a": sess.bytes_b_to_a,
                "duration_s": round(max(0.0, sess.last_ts - sess.first_ts), 3),
                "is_doip": sess.is_doip,
                "saw_fin_or_rst": sess.saw_fin_or_rst,
            })
        tcp_sessions.sort(key=lambda s: -s["total_frames"])

        someip_catalog = [
            {"service_id": f"0x{sid:04X}", "method_id": f"0x{mid:04X}", "count": count}
            for (sid, mid), count in result.someip_catalog.most_common()
        ]

        return {
            "schema_version": "1.0",
            "path": result.path,
            "total_frames": result.total_frames,
            "duration_s": round(result.duration_s, 3),
            "ethertypes": [
                {"ethertype": f"0x{et:04X}", "name": ETHERTYPE_NAMES.get(et, "unknown"), "count": c}
                for et, c in result.ethertype_counts.most_common()
            ],
            "vlans": [{"vlan_id": v, "count": c} for v, c in result.vlan_counts.most_common()],
            "ip_protocols": [
                {"proto": IP_PROTO_NAMES.get(p, f"proto_{p}"), "count": c}
                for p, c in result.ip_proto_counts.most_common()
            ],
            "nodes": self.node_inventory(result),
            "doip_servers": self.doip_servers(result),
            "udp_flows": udp_flows,
            "tcp_sessions": tcp_sessions,
            "someip_catalog": someip_catalog,
            "warnings": result.errors,
        }
