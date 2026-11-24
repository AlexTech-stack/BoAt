#!/usr/bin/env python3
# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

"""Deterministically regenerate the system-test binary fixtures.

Everything here is synthetic, with fixed timestamps and payloads, so the
committed files and a fresh generation are byte-for-byte reproducible (except
where python-can's container metadata embeds a wall-clock start time; the
*frames* are identical and the tests assert counts/IDs/data, not file bytes).

Dependencies: ``python-can`` (already a CLI/SDK dependency) and the repo's own
``boat.pcapng`` / generated protobuf stubs.

Usage:
    python3 test/fixtures/make_fixtures.py            # everything except big.asc
    python3 test/fixtures/make_fixtures.py --big      # also the 12.8 MB big.asc
    python3 test/fixtures/make_fixtures.py --check    # report existing file counts
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import struct
import sys
from pathlib import Path

import can

FIXTURES = Path(__file__).resolve().parent
REPO_ROOT = FIXTURES.parents[1]
SDK = REPO_ROOT / "boat-platform" / "sdk" / "python"
sys.path.insert(0, str(SDK))

from boat.pcapng import (  # noqa: E402
    DLT_CAN_SOCKETCAN,
    DLT_EN10MB,
    PcapngWriter,
)
from boat.v1 import frame_pb2  # noqa: E402

MAC_A = bytes.fromhex("020000000001")
MAC_B = bytes.fromhex("020000000002")
MAC_C = bytes.fromhex("020000000003")

# ── classic pcap (Ethernet link type) ────────────────────────────────────────


def _pcap(path: Path, frames: list[tuple[float, bytes]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        # magic, major, minor, thiszone, sigfigs, snaplen, network=DLT_EN10MB
        f.write(struct.pack("<IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, DLT_EN10MB))
        for ts, data in frames:
            sec = int(ts)
            usec = int(round((ts - sec) * 1_000_000))
            f.write(struct.pack("<IIII", sec, usec, len(data), len(data)))
            f.write(data)


def _checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\x00"
    total = sum(struct.unpack(">%dH" % (len(data) // 2), data))
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


def _eth(dst: bytes, src: bytes, ethertype: int, payload: bytes, vlan: int | None = None) -> bytes:
    if vlan is None:
        return dst + src + struct.pack(">H", ethertype) + payload
    return dst + src + struct.pack(">HHH", 0x8100, vlan, ethertype) + payload


def _ipv4(src: str, dst: str, proto: int, payload: bytes, ttl: int = 64) -> bytes:
    src_b = ipaddress.IPv4Address(src).packed
    dst_b = ipaddress.IPv4Address(dst).packed
    header = struct.pack(
        ">BBHHHBBH4s4s", 0x45, 0, 20 + len(payload), 0, 0x4000, ttl, proto, 0, src_b, dst_b
    )
    csum = _checksum(header)
    header = header[:10] + struct.pack(">H", csum) + header[12:]
    return header + payload


def _ipv6(src: str, dst: str, next_header: int, payload: bytes, hop_limit: int = 64) -> bytes:
    src_b = ipaddress.IPv6Address(src).packed
    dst_b = ipaddress.IPv6Address(dst).packed
    header = struct.pack(">IHBB16s16s", 6 << 28, len(payload), next_header, hop_limit, src_b, dst_b)
    return header + payload


def _udp4(src: str, dst: str, sport: int, dport: int, payload: bytes) -> bytes:
    length = 8 + len(payload)
    pseudo = ipaddress.IPv4Address(src).packed + ipaddress.IPv4Address(dst).packed
    pseudo += struct.pack(">BBH", 0, 17, length)
    header = struct.pack(">HHHH", sport, dport, length, 0)
    csum = _checksum(pseudo + header + payload)
    if csum == 0:
        csum = 0xFFFF
    return struct.pack(">HHHH", sport, dport, length, csum) + payload


def _udp6(src: str, dst: str, sport: int, dport: int, payload: bytes) -> bytes:
    length = 8 + len(payload)
    pseudo = ipaddress.IPv6Address(src).packed + ipaddress.IPv6Address(dst).packed
    pseudo += struct.pack(">IxxxB", length, 17)
    header = struct.pack(">HHHH", sport, dport, length, 0)
    csum = _checksum(pseudo + header + payload)
    if csum == 0:
        csum = 0xFFFF
    return struct.pack(">HHHH", sport, dport, length, csum) + payload


def _tcp4(src: str, dst: str, sport: int, dport: int, seq: int, ack: int, flags: int, payload: bytes) -> bytes:
    header = struct.pack(">HHIIBBHHH", sport, dport, seq, ack, 5 << 4, flags, 8192, 0, 0)
    pseudo = ipaddress.IPv4Address(src).packed + ipaddress.IPv4Address(dst).packed
    pseudo += struct.pack(">BBH", 0, 6, len(header) + len(payload))
    csum = _checksum(pseudo + header + payload)
    header = header[:16] + struct.pack(">H", csum) + header[18:]
    return header + payload


def _icmp_echo(ident: int, seq: int, payload: bytes) -> bytes:
    body = struct.pack(">BBHHH", 8, 0, 0, ident, seq) + payload
    csum = _checksum(body)
    return struct.pack(">BBHHH", 8, 0, csum, ident, seq) + payload


def _icmpv6_echo(ident: int, seq: int, payload: bytes) -> bytes:
    body = struct.pack(">BBHHH", 128, 0, 0, ident, seq) + payload
    # ICMPv6 checksum includes the IPv6 pseudo-header; filled in by caller helper.
    return body


def _icmpv6_echo_checked(src: str, dst: str, ident: int, seq: int, payload: bytes) -> bytes:
    body = _icmpv6_echo(ident, seq, payload)
    pseudo = ipaddress.IPv6Address(src).packed + ipaddress.IPv6Address(dst).packed
    pseudo += struct.pack(">IxxxB", len(body), 58)
    csum = _checksum(pseudo + body)
    return struct.pack(">BBHHH", 128, 0, csum, ident, seq) + payload


# ── CAN fixtures ─────────────────────────────────────────────────────────────


def _can_message(ts: float, channel: int, can_id: int, data: bytes, *, fd: bool = False,
                  extended: bool = False) -> can.Message:
    return can.Message(
        timestamp=ts,
        arbitration_id=can_id,
        data=data,
        channel=channel,
        is_extended_id=extended,
        is_fd=fd,
        bitrate_switch=fd,
    )


def make_can() -> dict[str, int]:
    out = FIXTURES / "can"
    out.mkdir(parents=True, exist_ok=True)

    frames: list[can.Message] = []
    # Channel 1: 0x100/0x101 every 100 ms for 10 s (100 each).
    for i in range(100):
        ts = i * 0.1
        frames.append(_can_message(ts, 1, 0x100, bytes([(200 + i) & 0xFF, 0x11, 0x22, 0x33])))
        frames.append(_can_message(ts + 0.05, 1, 0x101, bytes([i & 0xFF, 0x44, 0x55, 0x66])))
    # Channel 2: 0x200 every 100 ms (100).
    for i in range(100):
        frames.append(_can_message(i * 0.1 + 0.02, 2, 0x200, bytes([i & 0xFF, 0xAA])))
    # Channel 3: 0x300 every 200 ms (50).
    for i in range(50):
        frames.append(_can_message(i * 0.2 + 0.03, 3, 0x300, bytes([i & 0xFF])))
    # Channel 4: 0x040 and 0x0C0 every 250 ms (40 each) -- the IDs/channel that
    # TC_Replay_003 filters on.
    for i in range(40):
        frames.append(_can_message(i * 0.25 + 0.01, 4, 0x040, bytes([i & 0xFF, 0x40])))
        frames.append(_can_message(i * 0.25 + 0.11, 4, 0x0C0, bytes([i & 0xFF, 0xC0])))
    # One 29-bit frame and one CAN-FD frame.
    frames.append(_can_message(5.0, 1, 0x18DAF110, b"\x01\x02\x03", extended=True))
    frames.append(_can_message(5.5, 1, 0x1F0, bytes(range(16)), fd=True))

    frames.sort(key=lambda m: m.timestamp)
    with can.BLFWriter(str(out / "recording.blf")) as writer:
        for message in frames:
            writer.on_message_received(message)

    short = [_can_message(i * 0.05, 1, 0x100 + i, bytes([i, 0x5A])) for i in range(20)]
    with can.BLFWriter(str(out / "recording_short.blf")) as writer:
        for message in short:
            writer.on_message_received(message)

    return {"recording.blf": len(frames), "recording_short.blf": len(short)}


def make_asc(big: bool) -> dict[str, int]:
    out = FIXTURES / "can"
    out.mkdir(parents=True, exist_ok=True)
    header = (
        "date Mon Nov 23 12:00:00.000 am 2026\n"
        "base hex  timestamps absolute\n"
        "no internal events logged\n"
    )
    small_path = out / "small.asc"
    lines = [
        "%11.6f 1  %X             Rx   d 8 %s 00 00 00 00\n"
        % (
            i * 0.001,
            0x100 + i % 16,
            " ".join("%02X" % ((i >> s) & 0xFF) for s in (0, 8, 16, 24)),
        )
        for i in range(200_000)
    ]
    small_path.write_text(header + "".join(lines[:47]), encoding="utf-8")
    result = {"small.asc": 47}
    if big:
        big_path = out / "big.asc"
        big_path.write_text(header + "".join(lines), encoding="utf-8")
        result["big.asc"] = 200_000
    return result


# ── Ethernet fixtures ────────────────────────────────────────────────────────


def _someip(service: int, method: int, payload: bytes) -> bytes:
    # SOME/IP header: message id, length, request id, protocol/interface/type/return
    return struct.pack(">HHIHHBBBB", service, method, 8 + len(payload), 1, 1, 1, 1, 0, 0) + payload


def make_eth() -> dict[str, int]:
    out = FIXTURES / "eth"
    out.mkdir(parents=True, exist_ok=True)

    ipv4_frames: list[tuple[float, bytes]] = []
    conversations = [
        ("10.10.10.10", "10.10.10.11", 50000, 30490),
        ("10.10.10.11", "10.10.10.10", 30490, 50000),
        ("10.10.10.12", "10.10.10.10", 40000, 50000),
    ]
    ts = 0.0
    for src, dst, sport, dport in conversations:
        for seq in range(4):
            payload = struct.pack(">I", seq) + b"payload"
            frame = _eth(
                MAC_B if dst.endswith(".11") else MAC_C,
                MAC_A if src.endswith(".10") else MAC_B,
                0x0800,
                _ipv4(src, dst, 17, _udp4(src, dst, sport, dport, payload)),
            )
            ipv4_frames.append((ts, frame))
            ts += 0.01
    _pcap(out / "capture.pcap", ipv4_frames)

    rich: list[tuple[float, bytes]] = []
    ts = 0.0

    # SOME/IP service discovery on UDP 30490 (IPv4).
    someip = _someip(0xFFFF, 0x8100, b"\x00" * 12)
    rich.append((ts, _eth(MAC_B, MAC_A, 0x0800, _ipv4("10.10.10.10", "10.10.10.11", 17,
                                                      _udp4("10.10.10.10", "10.10.10.11", 40001, 30490, someip)))))
    ts += 0.01
    # DoIP TCP session (13400): SYN, SYN-ACK, ACK, data.
    rich.append((ts, _eth(MAC_B, MAC_A, 0x0800, _ipv4("10.10.10.10", "10.10.10.11", 6,
                                                      _tcp4("10.10.10.10", "10.10.10.11", 40002, 13400, 1000, 0, 0x02, b"")))))
    ts += 0.01
    rich.append((ts, _eth(MAC_A, MAC_B, 0x0800, _ipv4("10.10.10.11", "10.10.10.10", 6,
                                                      _tcp4("10.10.10.11", "10.10.10.10", 13400, 40002, 2000, 1001, 0x12, b"")))))
    ts += 0.01
    rich.append((ts, _eth(MAC_B, MAC_A, 0x0800, _ipv4("10.10.10.10", "10.10.10.11", 6,
                                                      _tcp4("10.10.10.10", "10.10.10.11", 40002, 13400, 1001, 2001, 0x10, b"")))))
    ts += 0.01
    rich.append((ts, _eth(MAC_B, MAC_A, 0x0800, _ipv4("10.10.10.10", "10.10.10.11", 6,
                                                      _tcp4("10.10.10.10", "10.10.10.11", 40002, 13400, 1001, 2001, 0x18,
                                                            b"\x02\xfd\x00\x05\x00\x00\x00\x04\x0e\x80\x01\x00")))))
    ts += 0.01
    # ICMPv4 echo.
    rich.append((ts, _eth(MAC_B, MAC_A, 0x0800, _ipv4("10.10.10.10", "10.10.10.11", 1,
                                                      _icmp_echo(1, 1, b"abcdefgh")))))
    ts += 0.01
    # IPv6 UDP.
    rich.append((ts, _eth(MAC_B, MAC_A, 0x86DD, _ipv6("fe80::1", "fe80::2", 17,
                                                      _udp6("fe80::1", "fe80::2", 50000, 50001, b"ipv6")))))
    ts += 0.01
    # ICMPv6 echo.
    rich.append((ts, _eth(MAC_B, MAC_A, 0x86DD, _ipv6("fe80::1", "fe80::2", 58,
                                                      _icmpv6_echo_checked("fe80::1", "fe80::2", 2, 1, b"v6echo")))))
    ts += 0.01
    # IPv6 with a Hop-by-Hop extension header, then UDP.
    hop_by_hop = struct.pack(">BB6x", 17, 0)
    rich.append((ts, _eth(MAC_B, MAC_A, 0x86DD, _ipv6("fe80::1", "fe80::2", 0,
                                                      hop_by_hop + _udp6("fe80::1", "fe80::2", 50002, 50003, b"exthdr")))))
    ts += 0.01
    # VLAN-tagged IPv4 UDP.
    rich.append((ts, _eth(MAC_B, MAC_A, 0x0800, _ipv4("10.10.10.10", "10.10.10.11", 17,
                                                      _udp4("10.10.10.10", "10.10.10.11", 40003, 30490, someip)), vlan=42)))
    ts += 0.01
    # AUTOSAR PDU-multiplexed flow: 4-byte Header ID + 4-byte length + payload.
    for header_id in (0x00010001, 0x00010002):
        ipdum = struct.pack(">II", header_id, 8) + bytes(8)
        rich.append((ts, _eth(MAC_B, MAC_A, 0x0800, _ipv4("10.10.10.20", "10.10.10.21", 17,
                                                          _udp4("10.10.10.20", "10.10.10.21", 40004, 30501, ipdum)))))
        ts += 0.01
    _pcap(out / "capture_rich.pcap", rich)

    inject = _eth(MAC_B, MAC_A, 0x88B5, b"\xDE\xAD\xBE\xEF")
    _pcap(out / "eth_inject.pcap", [(0.0, inject)])

    (out / "notes.txt").write_text("This is not a trace file.\n", encoding="utf-8")
    return {"capture.pcap": len(ipv4_frames), "capture_rich.pcap": len(rich), "eth_inject.pcap": 1}


# ── Mixed pcapng + native .trace ─────────────────────────────────────────────


def make_mixed() -> dict[str, int]:
    out = FIXTURES / "mixed"
    out.mkdir(parents=True, exist_ok=True)

    writer = PcapngWriter(str(out / "mixed.pcapng"))
    can_if = writer.add_interface("vcan0", DLT_CAN_SOCKETCAN)
    eth_if = writer.add_interface("veth0", DLT_EN10MB)

    can_count = 20
    eth_count = 10
    events: list[tuple[float, str, int]] = []
    for i in range(can_count):
        events.append((i * 0.02, "can", i))
    for i in range(eth_count):
        events.append((0.01 + i * 0.04, "eth", i))
    events.sort()
    for ts, kind, i in events:
        if kind == "can":
            writer.write_can(can_if, ts, 0x100 + (i % 16), 8, bytes([i] * 8), 0)
        else:
            payload = _udp4("10.0.0.1", "10.0.0.2", 40000, 30490, struct.pack(">I", i) + b"eth")
            writer.write_eth(eth_if, ts, MAC_B, MAC_A, 0x0800,
                             _ipv4("10.0.0.1", "10.0.0.2", 17, payload))
    writer.close()

    raw = (out / "mixed.pcapng").read_bytes()
    (out / "corrupt.pcapng").write_bytes(raw[:-3])

    # Native length-delimited boat.v1.Frame records: CAN + Ethernet + TCP + PDU.
    records: list[bytes] = []
    for i in range(3):
        frame = frame_pb2.Frame(
            bus_type=frame_pb2.Frame.CAN,
            iface="vcan0",
            timestamp_ns=(i + 1) * 1_000_000,
            payload=bytes([i, 1, 2, 3]),
        )
        frame.can.can_id = 0x100 + i
        frame.can.dlc = 4
        frame.can.channel = 1
        records.append(frame.SerializeToString())
    eth = frame_pb2.Frame(
        bus_type=frame_pb2.Frame.ETHERNET,
        iface="veth0",
        timestamp_ns=5_000_000,
        payload=b"\x00" * 18,
    )
    eth.eth.dst_mac = MAC_B
    eth.eth.src_mac = MAC_A
    eth.eth.ethertype = 0x0800
    eth.eth.ip_version = 4
    eth.eth.src_ip = ipaddress.IPv4Address("10.0.0.1").packed
    eth.eth.dst_ip = ipaddress.IPv4Address("10.0.0.2").packed
    records.append(eth.SerializeToString())

    eth2 = frame_pb2.Frame(
        bus_type=frame_pb2.Frame.ETHERNET, iface="veth0", timestamp_ns=6_000_000, payload=b"\x00" * 18
    )
    eth2.eth.dst_mac = MAC_C
    eth2.eth.src_mac = MAC_B
    eth2.eth.ethertype = 0x0800
    eth2.eth.ip_version = 4
    eth2.eth.src_ip = ipaddress.IPv4Address("10.0.0.2").packed
    eth2.eth.dst_ip = ipaddress.IPv4Address("10.0.0.1").packed
    records.append(eth2.SerializeToString())

    tcp = frame_pb2.Frame(
        bus_type=frame_pb2.Frame.TCP, iface="veth0", timestamp_ns=7_000_000, payload=b"hello"
    )
    tcp.tcp.src_ip = ipaddress.IPv4Address("10.0.0.1").packed
    tcp.tcp.dst_ip = ipaddress.IPv4Address("10.0.0.2").packed
    tcp.tcp.ip_version = 4
    tcp.tcp.src_port = 40000
    tcp.tcp.dst_port = 30490
    tcp.tcp.conn_id = 1
    records.append(tcp.SerializeToString())

    pdu = frame_pb2.Frame(
        bus_type=frame_pb2.Frame.PDU, iface="vcan0", timestamp_ns=8_000_000, payload=b"\xAA\xBB"
    )
    pdu.pdu.pdu_id = 0x7E0
    records.append(pdu.SerializeToString())

    with open(out / "edited.trace", "wb") as f:
        for record in records:
            f.write(struct.pack("<I", len(record)))
            f.write(record)

    return {"mixed.pcapng": can_count + eth_count, "corrupt.pcapng": can_count + eth_count,
            "edited.trace": len(records)}


# ── CLI ──────────────────────────────────────────────────────────────────────


def _existing_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    blf = FIXTURES / "can" / "recording.blf"
    if blf.is_file():
        counts[blf.name] = sum(1 for m in can.BLFReader(str(blf)) if hasattr(m, "arbitration_id"))
    short = FIXTURES / "can" / "recording_short.blf"
    if short.is_file():
        counts[short.name] = sum(1 for m in can.BLFReader(str(short)) if hasattr(m, "arbitration_id"))
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--big", action="store_true", help="also generate the 12.8 MB big.asc")
    parser.add_argument("--check", action="store_true", help="report existing frame counts and exit")
    args = parser.parse_args()

    if args.check:
        print(json.dumps(_existing_counts(), indent=2))
        return 0

    counts: dict[str, dict[str, int]] = {}
    counts["can"] = make_can()
    counts["asc"] = make_asc(args.big)
    counts["eth"] = make_eth()
    counts["mixed"] = make_mixed()
    print(json.dumps(counts, indent=2))
    print("fixtures generated under", FIXTURES)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
