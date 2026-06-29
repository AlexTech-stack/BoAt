"""Python SDK for replaying CAN/Ethernet trace files through the BoAt gateway.

Supported formats:
  - CAN: .asc, .blf (via python-can)
   - Ethernet: .pcap (DLT_EN10MB, IPv4/IPv6+UDP/ICMP)

Two replay modes are available:

1. **Direct mode** (default): Sends each CAN frame one-by-one via gRPC CanService.
2. **Server-side mode**: Converts the trace to the gateway's internal binary format,
   uploads it via ReplayService.ImportTraceData, then plays back using
   ReplayService.StartReplay + StreamReplay.

Ethernet traces always use server-side mode.

Quick example::

    from boat.trace_replay import TraceReplayer

    # CAN replay
    replayer = TraceReplayer(
        gateway="localhost:50051",
        buses=["vcan0", "vcan1"],
        speed=1.0,
    )
    replayer.replay("recording.asc")

    # Ethernet pcap replay (always server-side)
    replayer = TraceReplayer(
        gateway="localhost:50051",
        buses=["eth0"],
        replay_src_ip="192.168.1.1",
        replay_dst_ip="192.168.1.100",
    )
    replayer.replay("capture.pcap")
"""
from __future__ import annotations

from collections import namedtuple
import ipaddress
import struct
import time
from pathlib import Path
from typing import Callable, List, Optional

# CAN FD flags (matches gateway constants)
_CANFD_BRS = 0x01   # bit-rate switch
_CANFD_FDF = 0x04   # FD frame

# Internal trace format magic number
_TRACE_MAGIC = 0xB0A7B0A7

# Replay Ethernet event type base (matches kReplayEthEventBase in C++)
_REPLAY_ETH_EVENT_BASE = 0xEE000000


EthernetPcapFrame = namedtuple("EthernetPcapFrame", [
    "timestamp", "dst_mac", "src_mac", "ethertype", "payload",
])


class EthernetPcapReader:
    """Iterate Ethernet frames from a standard pcap file (DLT_EN10MB).

    Yields ``EthernetPcapFrame`` per packet record.
    Context-manager compatible.
    """

    def __init__(self, path: str) -> None:
        self._f = open(path, "rb")
        try:
            hdr = self._f.read(24)
            if len(hdr) < 24:
                raise TraceReplayError("Truncated pcap global header")
            _, _, _, _, _, _, dlt = struct.unpack("<IHHiIII", hdr)
            if dlt != 1:
                raise TraceReplayError(
                    f"Unsupported pcap DLT {dlt}, expected DLT_EN10MB (1)"
                )
        except TraceReplayError:
            self._f.close()
            raise
        except Exception as e:
            self._f.close()
            raise TraceReplayError(f"Invalid pcap file: {e}") from e

    def __enter__(self) -> "EthernetPcapReader":
        return self

    def __exit__(self, *args) -> None:
        self._f.close()

    def __iter__(self) -> "EthernetPcapReader":
        return self

    def __next__(self) -> EthernetPcapFrame:
        hdr = self._f.read(16)
        if len(hdr) < 16:
            self._f.close()
            raise StopIteration
        ts_sec, ts_usec, incl_len, _ = struct.unpack("<IIII", hdr)
        frame = self._f.read(incl_len)
        if len(frame) < 14:
            self._f.close()
            raise StopIteration
        ts = ts_sec + ts_usec / 1_000_000
        return EthernetPcapFrame(
            timestamp=ts,
            dst_mac=frame[0:6],
            src_mac=frame[6:12],
            ethertype=(frame[12] << 8) | frame[13],
            payload=frame[14:incl_len],
        )


class TraceReplayError(RuntimeError):
    pass


class TraceReplayer:
    """Replay a CAN/Ethernet trace file through the BoAt gateway via gRPC.

    Args:
        gateway:     gRPC address of the BoAt gateway (host:port).
        buses:       Ordered list of interface names.  For CAN: channel *N*
                     maps to ``buses[N-1]`` (1-based).  For Ethernet: the
                     first bus is the target interface for reconstructed frames.
        speed:       Playback speed multiplier.  ``1.0`` = real-time,
                     ``2.0`` = twice as fast, ``0.5`` = half speed.
                     ``0`` means send as fast as possible (no delay).
        simulation_id: Simulation ID forwarded to the gateway (usually ``""``).
        on_frame:    Optional callback ``(index, msg) -> None`` called for
                     every frame just before it is sent.
        channel_filter: If set, only replay CAN frames from this channel.
        id_filter:   If set, only replay CAN frames with these arbitration IDs.
        eth_iface:   Target Ethernet interface for pcap replay (overrides ``buses[0]``).
        replay_src_ip: Source IP address for reconstructed IP header (Ethernet replay).
        replay_dst_ip: Destination IP address for reconstructed IP header.
        replay_src_mac: Override source MAC (auto-detected from interface if not set).
        replay_dst_mac: Override destination MAC (default: broadcast for IPv4/IPv6 UDP/ICMP).
        ip_filter:      Set of IP addresses to filter by (applied post-rewrite).
                        Only packets whose rewritten src or dst is in this set
                        are replayed. Empty set = no filtering.
        ip_map:         Mapping of original IP → rewritten IP (e.g.
                        ``{"10.10.10.10": "192.168.0.100"}``).  IPs not in the
                        map keep their original value (or ``replay_src_ip`` /
                        ``replay_dst_ip`` fallback).
        ethertype_filter: Set of EtherType values to filter by (pre-rewrite).
                          Only packets whose EtherType is in this set are
                          replayed.  Empty set = no filtering.
        protocol_filter:  Set of IP protocol / IPv6 next-header numbers to
                          filter by (pre-rewrite).  Only packets whose L4
                          protocol is in this set are replayed.  Empty set = no
                          filtering.
    """

    def __init__(
        self,
        gateway: str = "localhost:50051",
        buses: Optional[List[str]] = None,
        speed: float = 1.0,
        simulation_id: str = "",
        on_frame: Optional[Callable] = None,
        channel_filter: Optional[int] = None,
        id_filter: Optional[set[int]] = None,
        eth_iface: Optional[str] = None,
        replay_src_ip: Optional[str] = None,
        replay_dst_ip: Optional[str] = None,
        replay_src_mac: Optional[str] = None,
        replay_dst_mac: Optional[str] = None,
        ip_filter: Optional[set[str]] = None,
        ip_map: Optional[dict[str, str]] = None,
        ethertype_filter: Optional[set[int]] = None,
        protocol_filter: Optional[set[int]] = None,
    ) -> None:
        self.gateway          = gateway
        self.buses            = buses or []
        self.speed            = speed
        self.simulation_id    = simulation_id
        self.on_frame         = on_frame
        self.channel_filter   = channel_filter
        self.id_filter        = id_filter or set()
        self.eth_iface        = eth_iface
        self.replay_src_ip    = replay_src_ip
        self.replay_dst_ip    = replay_dst_ip
        self.replay_src_mac   = replay_src_mac
        self.replay_dst_mac   = replay_dst_mac
        self.ip_filter        = ip_filter or set()
        self.ip_map           = ip_map or {}
        self.ethertype_filter = ethertype_filter or set()
        self.protocol_filter  = protocol_filter or set()
        self._stub            = None
        self._replay_stub     = None

    # ── Public API ─────────────────────────────────────────────────────────────

    def replay(self, path: str | Path, loop: bool = False, server_side: bool = False) -> int:
        """Replay a trace file.

        Args:
            path: Path to a ``.asc``, ``.blf``, or ``.pcap`` file.
            loop: If *True* replay the file indefinitely until the process is
                  interrupted.
            server_side: If *True*, upload the trace and use the gateway's
                         ReplayService for server-side playback.  Automatically
                         enabled for ``.pcap`` files.

        Returns:
            Total number of frames sent (across all loop iterations).

        Raises:
            TraceReplayError: If the file cannot be opened or a gRPC error
            occurs.
        """
        path = Path(path)
        if path.suffix.lower() == ".pcap" and not server_side:
            server_side = True
        if server_side:
            return self.replay_server_side(path, loop=loop)

        stub = self._get_stub()
        total = 0
        while True:
            total += self._replay_once(stub, path, total == 0)
            if not loop:
                break
        return total

    def replay_server_side(self, path: str | Path, loop: bool = False) -> int:
        """Replay a trace file using the gateway's ReplayService.

        Converts the trace to the internal binary format, uploads it via
        ImportTraceData, then plays back using StartReplay + StreamReplay.

        Args:
            path: Path to an ``.asc`` or ``.blf`` file.
            loop: If *True* replay the file indefinitely.

        Returns:
            Total number of frames replayed.

        Raises:
            TraceReplayError: On conversion, upload, or playback errors.
        """
        from boat.v1 import replay_pb2

        path = Path(path)
        trace_id = path.stem

        binary_data = self._convert_to_binary(path)

        replay_stub = self._get_replay_stub()

        try:
            upload_resp = replay_stub.ImportTraceData(
                replay_pb2.ImportTraceDataRequest(
                    trace_id=trace_id,
                    format=path.suffix.lstrip(".").upper(),
                    data=binary_data,
                )
            )
        except Exception as e:
            raise TraceReplayError(f"ImportTraceData failed: {e}") from e

        if not upload_resp.accepted:
            raise TraceReplayError(f"ImportTraceData rejected: {upload_resp.error.message}")

        speed_mult = 1_000_000.0 if self.speed == 0 else self.speed
        eth_iface = self.eth_iface or (self.buses[0] if self.buses else "")
        start_resp = replay_stub.StartReplay(
            replay_pb2.StartReplayRequest(
                trace_id=trace_id,
                simulation_id=self.simulation_id,
                speed=replay_pb2.REPLAY_SPEED_ACCELERATED,
                speed_multiplier=speed_mult,
                eth_iface=eth_iface,
            )
        )

        if not start_resp.accepted:
            raise TraceReplayError(f"StartReplay rejected: {start_resp.error.message}")

        replay_id = start_resp.replay_id
        total = 0
        while True:
            stream = replay_stub.StreamReplay(
                replay_pb2.StreamReplayRequest(replay_id=replay_id)
            )
            pass_total = 0
            try:
                for event in stream:
                    pass_total += 1
                    total += 1
                    if self.on_frame is not None:
                        self.on_frame(pass_total, event)
            except Exception:
                pass
            if not loop:
                break

        return total

    # ── Internals ──────────────────────────────────────────────────────────────

    def _convert_to_binary(self, path: Path) -> bytes:
        """Convert a trace file to the gateway's internal binary trace format.

        Handles both CAN (ASC/BLF) and Ethernet (pcap) sources.
        """
        reader = self._open_reader(path)
        result = bytearray()
        first_ts: Optional[float] = None
        is_eth = isinstance(reader, EthernetPcapReader)

        with reader:
            for msg in reader:
                ts = msg.timestamp
                if first_ts is None:
                    first_ts = ts
                relative_ts = ts - first_ts

                tick = int(relative_ts * 1000)
                wall_time_ns = int(relative_ts * 1_000_000_000)

                if is_eth:
                    if self.ethertype_filter and msg.ethertype not in self.ethertype_filter:
                        continue
                    raw = self._reconstruct_ip_packet(msg)
                    if not raw:
                        continue
                    event_type = _REPLAY_ETH_EVENT_BASE | (msg.ethertype & 0xFFFF)
                else:
                    ch = getattr(msg, "channel", None)
                    if self.channel_filter is not None and ch != self.channel_filter:
                        continue
                    if self.id_filter and msg.arbitration_id not in self.id_filter:
                        continue
                    raw = bytes(msg.data)
                    event_type = msg.arbitration_id

                header = struct.pack(
                    "<IIQqI",
                    _TRACE_MAGIC,
                    event_type,
                    tick,
                    wall_time_ns,
                    len(raw),
                )
                result.extend(header)
                result.extend(raw)

        return bytes(result)

    def _reconstruct_ip_packet(self, frame: EthernetPcapFrame) -> bytes:
        """Reconstruct an IP packet with user-specified addresses.

        Applies protocol filter before dispatching to version-specific handler.
        """
        payload = frame.payload
        if frame.ethertype == 0x86DD:
            if len(payload) < 40:
                return b""
            protocol = payload[6]
        else:
            if len(payload) < 20:
                return b""
            protocol = payload[9]

        if self.protocol_filter and protocol not in self.protocol_filter:
            return b""

        if frame.ethertype == 0x86DD:
            return self._reconstruct_ip6_packet(frame)
        return self._reconstruct_ip4_packet(frame)

    def _reconstruct_ip4_packet(self, frame: EthernetPcapFrame) -> bytes:
        """Reconstruct an IPv4 packet with user-specified addresses."""
        payload = frame.payload
        if len(payload) < 20:
            return b""

        # Parse IPv4 header
        version_ihl = payload[0]
        ihl = (version_ihl & 0x0F) * 4
        if ihl < 20 or ihl > len(payload):
            return b""
        total_len = (payload[2] << 8) | payload[3]
        identification = (payload[4] << 8) | payload[5]
        flags_frag = (payload[6] << 8) | payload[7]
        ttl = payload[8]
        protocol = payload[9]
        orig_src = payload[12:16]
        orig_dst = payload[16:20]

        header_end = ihl
        transport_payload = payload[header_end:total_len]

        orig_src_str = str(ipaddress.IPv4Address(orig_src))
        orig_dst_str = str(ipaddress.IPv4Address(orig_dst))
        mapped_src = self.ip_map.get(orig_src_str, self.replay_src_ip or orig_src_str)
        mapped_dst = self.ip_map.get(orig_dst_str, self.replay_dst_ip or orig_dst_str)
        if self.ip_filter and mapped_src not in self.ip_filter and mapped_dst not in self.ip_filter:
            return b""
        src_ip_bytes = self._parse_ip(mapped_src)
        dst_ip_bytes = self._parse_ip(mapped_dst)

        if protocol == 17 and len(transport_payload) >= 8:
            # UDP: preserve ports, rebuild header
            src_port = struct.unpack("!H", transport_payload[0:2])[0]
            dst_port = struct.unpack("!H", transport_payload[2:4])[0]
            udp_len = len(transport_payload[8:]) + 8
            udp_data = transport_payload[8:]
            new_udp = struct.pack("!HHHH", src_port, dst_port, udp_len, 0)
            new_udp += udp_data
            transport = new_udp
            total_len = 20 + len(new_udp)
        elif protocol == 1 and len(transport_payload) >= 4:
            # ICMP: preserve type/code/rest, rebuild
            icmp_type = transport_payload[0:1]
            icmp_code = transport_payload[1:2]
            icmp_rest = transport_payload[4:]
            new_icmp = icmp_type + icmp_code + b"\x00\x00"  # checksum placeholder
            new_icmp += icmp_rest
            transport = new_icmp
            total_len = 20 + len(new_icmp)
        else:
            # Unknown protocol — pass through as-is
            transport = transport_payload
            total_len = 20 + len(transport)

        # Build IP header (no options)
        ip_header = struct.pack("!BBHHHBBH4s4s",
            0x45,                     # version=4, ihl=5
            0,                        # DSCP/ECN
            total_len,                # total length
            identification,           # identification
            flags_frag,               # flags + fragment offset
            ttl,                      # time to live
            protocol,                 # protocol
            0,                        # header checksum (placeholder)
            src_ip_bytes,             # source address
            dst_ip_bytes,             # destination address
        )

        # Calculate IP checksum
        ip_csum = self._checksum(ip_header)
        ip_header = ip_header[:10] + struct.pack("!H", ip_csum) + ip_header[12:]

        result = ip_header + transport

        # Calculate UDP checksum if UDP
        if protocol == 17:
            pseudo = src_ip_bytes + dst_ip_bytes + b"\x00" + struct.pack("!BH", 17, len(transport))
            udp_offset = 20
            udp_hdr = result[udp_offset:udp_offset + 8]
            udp_csum = self._checksum(pseudo + udp_hdr + transport[8:])
            if udp_csum == 0:
                udp_csum = 0xFFFF
            result = result[:udp_offset + 6] + struct.pack("!H", udp_csum) + result[udp_offset + 8:]

        # Calculate ICMP checksum if ICMP
        if protocol == 1:
            icmp_offset = 20
            icmp_csum = self._checksum(result[icmp_offset:])
            result = result[:icmp_offset + 2] + struct.pack("!H", icmp_csum) + result[icmp_offset + 4:]

        return result

    def _reconstruct_ip6_packet(self, frame: EthernetPcapFrame) -> bytes:
        """Reconstruct an IPv6 packet with user-specified addresses.

        Handles UDP (next header 17) and ICMPv6 (next header 58).
        Both require mandatory checksums with IPv6 pseudo-header.
        Extension headers beyond the fixed 40-byte header are not parsed;
        unknown next headers pass through as-is.
        """
        payload = frame.payload
        if len(payload) < 40:
            return b""

        orig_src = payload[8:24]
        orig_dst = payload[24:40]
        next_header = payload[6]
        hop_limit = payload[7]
        payload_len = (payload[4] << 8) | payload[5]

        transport_payload = payload[40:40 + payload_len]

        orig_src_str = str(ipaddress.IPv6Address(orig_src))
        orig_dst_str = str(ipaddress.IPv6Address(orig_dst))
        mapped_src = self.ip_map.get(orig_src_str, self.replay_src_ip or orig_src_str)
        mapped_dst = self.ip_map.get(orig_dst_str, self.replay_dst_ip or orig_dst_str)
        if self.ip_filter and mapped_src not in self.ip_filter and mapped_dst not in self.ip_filter:
            return b""
        src_ip_bytes = self._parse_ip(mapped_src)
        dst_ip_bytes = self._parse_ip(mapped_dst)

        if next_header == 17 and len(transport_payload) >= 8:
            # UDP over IPv6: port preservation, mandatory checksum
            src_port = struct.unpack("!H", transport_payload[0:2])[0]
            dst_port = struct.unpack("!H", transport_payload[2:4])[0]
            udp_data = transport_payload[8:]
            udp_len = len(udp_data) + 8
            new_udp = struct.pack("!HHHH", src_port, dst_port, udp_len, 0)
            new_udp += udp_data
            transport = new_udp
        elif next_header == 58 and len(transport_payload) >= 4:
            # ICMPv6: preserve type/code/rest, mandatory checksum
            icmp6_type = transport_payload[0:1]
            icmp6_code = transport_payload[1:2]
            icmp6_rest = transport_payload[4:]
            new_icmp6 = icmp6_type + icmp6_code + b"\x00\x00"
            new_icmp6 += icmp6_rest
            transport = new_icmp6
        else:
            transport = transport_payload

        # Build IPv6 fixed header (40 bytes, no options)
        v_tc_flow = payload[0:4]  # preserve original version/tc/flow label
        ip6_header = v_tc_flow + struct.pack("!HBB",
            len(transport),        # payload length
            next_header,           # next header
            hop_limit,             # hop limit
        ) + src_ip_bytes + dst_ip_bytes

        result = ip6_header + transport

        # UDP checksum (mandatory for IPv6)
        if next_header == 17:
            pseudo = src_ip_bytes + dst_ip_bytes + struct.pack("!I", len(transport))
            pseudo += b"\x00\x00\x00" + struct.pack("!B", 17)
            udp_offset = 40
            udp_hdr = result[udp_offset:udp_offset + 8]
            udp_csum = self._checksum(pseudo + udp_hdr + transport[8:])
            if udp_csum == 0:
                udp_csum = 0xFFFF
            result = result[:udp_offset + 6] + struct.pack("!H", udp_csum) + result[udp_offset + 8:]

        # ICMPv6 checksum (mandatory, includes pseudo-header — unlike ICMPv4)
        if next_header == 58:
            pseudo = src_ip_bytes + dst_ip_bytes + struct.pack("!I", len(transport))
            pseudo += b"\x00\x00\x00" + struct.pack("!B", 58)
            icmp6_offset = 40
            icmp6_csum = self._checksum(pseudo + result[icmp6_offset:])
            result = result[:icmp6_offset + 2] + struct.pack("!H", icmp6_csum) + result[icmp6_offset + 4:]

        return result

    @staticmethod
    def _parse_ip(ip_str: str) -> bytes:
        return ipaddress.ip_address(ip_str).packed

    @staticmethod
    def _checksum(data: bytes) -> int:
        s = 0
        for i in range(0, len(data), 2):
            word = (data[i] << 8) | (data[i + 1] if i + 1 < len(data) else 0)
            s += word
        while s >> 16:
            s = (s & 0xFFFF) + (s >> 16)
        return (~s) & 0xFFFF

    def _get_replay_stub(self):
        if self._replay_stub is not None:
            return self._replay_stub
        try:
            import grpc
            from boat.v1 import replay_pb2_grpc
        except ImportError as e:
            raise TraceReplayError(f"Cannot import boat gRPC stubs: {e}") from e
        channel = grpc.insecure_channel(self.gateway)
        self._replay_stub = replay_pb2_grpc.ReplayServiceStub(channel)
        return self._replay_stub

    def _get_stub(self):
        if self._stub is not None:
            return self._stub
        try:
            import grpc
            from boat.v1 import can_pb2_grpc
        except ImportError as e:
            raise TraceReplayError(f"Cannot import boat gRPC stubs: {e}") from e
        channel     = grpc.insecure_channel(self.gateway)
        self._stub  = can_pb2_grpc.CanServiceStub(channel)
        return self._stub

    def _iface_for_channel(self, channel: int) -> str:
        """Map a 1-based trace channel number to a bus interface name."""
        if not self.buses:
            return "vcan0"
        idx = max(0, channel - 1)
        return self.buses[min(idx, len(self.buses) - 1)]

    def _open_reader(self, path: Path):
        """Return a reader appropriate for *path*.

        Supports:
          - ``.pcap`` (DLT_EN10MB) → ``EthernetPcapReader``
          - ``.asc`` / ``.blf`` → python-can reader
        """
        suffix = path.suffix.lower()
        if suffix == ".pcap":
            return EthernetPcapReader(str(path))

        try:
            import can
        except ImportError as e:
            raise TraceReplayError(
                "python-can is required for CAN trace replay: "
                "pip install python-can"
            ) from e
        if suffix == ".asc":
            return can.ASCReader(str(path))
        if suffix == ".blf":
            return can.BLFReader(str(path))
        raise TraceReplayError(
            f"Unsupported trace format '{suffix}'. "
            "Supported: .pcap, .asc, .blf"
        )

    def _replay_once(self, stub, path: Path, is_first: bool) -> int:
        """Stream one pass through *path*, return frame count."""
        from boat.v1 import can_pb2

        sent            = 0
        prev_trace_ts: Optional[float] = None
        prev_wall_ts:  Optional[float] = None

        try:
            reader = self._open_reader(path)
        except Exception as e:
            raise TraceReplayError(f"Cannot open trace file '{path}': {e}") from e

        with reader:
            for msg in reader:
                # ── filters ───────────────────────────────────────────────────
                ch = getattr(msg, "channel", None)
                if self.channel_filter is not None and ch != self.channel_filter:
                    continue
                if self.id_filter and msg.arbitration_id not in self.id_filter:
                    continue

                # ── timing ────────────────────────────────────────────────────
                if self.speed > 0 and prev_trace_ts is not None:
                    delta_trace = msg.timestamp - prev_trace_ts
                    delta_wall  = time.monotonic() - prev_wall_ts  # type: ignore[operator]
                    wait        = delta_trace / self.speed - delta_wall
                    if wait > 0:
                        time.sleep(wait)

                prev_trace_ts = msg.timestamp
                prev_wall_ts  = time.monotonic()

                # ── build CAN frame ───────────────────────────────────────────
                iface  = self._iface_for_channel(getattr(msg, "channel", 1) or 1)
                flags  = 0
                if getattr(msg, "is_fd", False):
                    flags |= _CANFD_FDF
                if getattr(msg, "bitrate_switch", False):
                    flags |= _CANFD_BRS

                frame = can_pb2.CanFrame(
                    can_id       = msg.arbitration_id,
                    dlc          = len(msg.data),
                    data         = bytes(msg.data),
                    timestamp_ns = int(msg.timestamp * 1_000_000_000),
                    iface        = iface,
                    flags        = flags,
                )

                if self.on_frame is not None:
                    self.on_frame(sent, msg)

                # ── send ──────────────────────────────────────────────────────
                try:
                    stub.SendCanFrame(
                        can_pb2.SendCanFrameRequest(
                            simulation_id = self.simulation_id,
                            frame         = frame,
                        )
                    )
                except Exception as e:
                    raise TraceReplayError(
                        f"gRPC error sending frame {sent}: {e}"
                    ) from e

                sent += 1

        return sent
