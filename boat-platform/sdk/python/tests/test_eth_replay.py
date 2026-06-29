"""Tests for Ethernet pcap replay conversion and IP packet reconstruction."""
from __future__ import annotations

import struct
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sdk" / "python"))


def _make_pcap(frames: list[bytes]) -> bytes:
    """Build a valid pcap file (DLT_EN10MB) with the given Ethernet frames."""
    buf = io.BytesIO()
    # Global header
    buf.write(struct.pack("<IHHiIII",
        0xa1b2c3d4,  # magic
        2, 3,         # version
        0,            # thiszone
        0,            # sigfigs
        65535,        # snaplen
        1,            # DLT_EN10MB
    ))
    ts = 0.0
    for frame in frames:
        ts_sec = int(ts)
        ts_usec = int((ts - ts_sec) * 1_000_000)
        buf.write(struct.pack("<IIII", ts_sec, ts_usec, len(frame), len(frame)))
        buf.write(frame)
        ts += 0.1
    return buf.getvalue()


def _udp_packet(src_ip: bytes, dst_ip: bytes, src_port: int, dst_port: int,
                payload: bytes, ttl: int = 64) -> bytes:
    """Build a raw Ethernet frame containing an IPv4+UDP packet."""
    # UDP header
    udp_len = 8 + len(payload)
    udp_hdr = struct.pack("!HHHH", src_port, dst_port, udp_len, 0)
    # IP header — total_len must include IP header + UDP header + payload
    total_len = 20 + udp_len
    ip_hdr = struct.pack("!BBHHHBBH4s4s",
        0x45, 0, total_len, 0x1234, 0, ttl, 17, 0, src_ip, dst_ip)
    # IP checksum
    s = 0
    for i in range(0, len(ip_hdr), 2):
        word = struct.unpack("!H", ip_hdr[i:i+2])[0]
        s += word
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    ip_csum = (~s) & 0xFFFF
    ip_hdr = ip_hdr[:10] + struct.pack("!H", ip_csum) + ip_hdr[12:]
    # Ethernet frame
    eth = (
        b"\x00\x01\x02\x03\x04\x05"  # dst_mac
        b"\x06\x07\x08\x09\x0a\x0b"  # src_mac
        b"\x08\x00"                    # ethertype IPv4
        + ip_hdr + udp_hdr + payload
    )
    return eth


def _icmp_packet(src_ip: bytes, dst_ip: bytes, payload: bytes, ttl: int = 64) -> bytes:
    """Build a raw Ethernet frame containing an IPv4+ICMP echo packet."""
    icmp_type = 8  # echo request
    icmp_code = 0
    icmp_ident = 0x1234
    icmp_seq = 1
    icmp_hdr = struct.pack("!BBHHH", icmp_type, icmp_code, 0, icmp_ident, icmp_seq)
    icmp_payload = payload
    icmp_data = icmp_hdr + icmp_payload
    # ICMP checksum
    s = 0
    for i in range(0, len(icmp_data), 2):
        word = struct.unpack("!H", icmp_data[i:i+2])[0]
        s += word
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    icmp_csum = (~s) & 0xFFFF
    icmp_data = icmp_hdr[:2] + struct.pack("!H", icmp_csum) + icmp_data[4:]
    # IP header
    total_len = 20 + len(icmp_data)
    ip_hdr = struct.pack("!BBHHHBBH4s4s",
        0x45, 0, total_len, 0x5678, 0, ttl, 1, 0, src_ip, dst_ip)
    s = 0
    for i in range(0, len(ip_hdr), 2):
        word = struct.unpack("!H", ip_hdr[i:i+2])[0]
        s += word
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    ip_csum = (~s) & 0xFFFF
    ip_hdr = ip_hdr[:10] + struct.pack("!H", ip_csum) + ip_hdr[12:]
    # Ethernet frame
    eth = (
        b"\x00\x01\x02\x03\x04\x05"
        b"\x06\x07\x08\x09\x0a\x0b"
        b"\x08\x00"
        + ip_hdr + icmp_data
    )
    return eth


def _checksum(data: bytes) -> int:
    """Compute the Internet checksum over *data*."""
    s = 0
    for i in range(0, len(data), 2):
        word = (data[i] << 8) | (data[i + 1] if i + 1 < len(data) else 0)
        s += word
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return (~s) & 0xFFFF


def _udp6_packet(src_ip: bytes, dst_ip: bytes, src_port: int, dst_port: int,
                 payload: bytes, hop_limit: int = 64) -> bytes:
    """Build a raw Ethernet frame containing an IPv6+UDP packet."""
    udp_len = 8 + len(payload)
    udp_hdr = struct.pack("!HHHH", src_port, dst_port, udp_len, 0)
    udp_data = udp_hdr + payload
    # UDP checksum with IPv6 pseudo-header (mandatory)
    pseudo = src_ip + dst_ip + struct.pack("!I", udp_len)
    pseudo += b"\x00\x00\x00" + struct.pack("!B", 17)
    udp_csum = _checksum(pseudo + udp_data)
    if udp_csum == 0:
        udp_csum = 0xFFFF
    udp_hdr = struct.pack("!HHHH", src_port, dst_port, udp_len, udp_csum)
    udp_data = udp_hdr + payload
    # IPv6 header
    v_tc_flow = 0x60000000  # version=6, tc=0, flow=0
    ip6_hdr = struct.pack("!IHBB", v_tc_flow, len(udp_data), 17, hop_limit)
    ip6_hdr += src_ip + dst_ip
    # Ethernet frame
    eth = (
        b"\x00\x01\x02\x03\x04\x05"
        b"\x06\x07\x08\x09\x0a\x0b"
        b"\x86\xdd"
        + ip6_hdr + udp_data
    )
    return eth


def _icmp6_packet(src_ip: bytes, dst_ip: bytes, payload: bytes, hop_limit: int = 64) -> bytes:
    """Build a raw Ethernet frame containing an IPv6+ICMPv6 echo packet."""
    icmp6_type = 128  # echo request
    icmp6_code = 0
    icmp6_ident = 0x1234
    icmp6_seq = 1
    icmp6_hdr = struct.pack("!BBHHH", icmp6_type, icmp6_code, 0, icmp6_ident, icmp6_seq)
    icmp6_data = icmp6_hdr + payload
    # ICMPv6 checksum with IPv6 pseudo-header (mandatory — unlike ICMPv4)
    pseudo = src_ip + dst_ip + struct.pack("!I", len(icmp6_data))
    pseudo += b"\x00\x00\x00" + struct.pack("!B", 58)
    icmp6_csum = _checksum(pseudo + icmp6_data)
    icmp6_data = icmp6_hdr[:2] + struct.pack("!H", icmp6_csum) + icmp6_data[4:]
    # IPv6 header
    v_tc_flow = 0x60000000
    ip6_hdr = struct.pack("!IHBB", v_tc_flow, len(icmp6_data), 58, hop_limit)
    ip6_hdr += src_ip + dst_ip
    # Ethernet frame
    eth = (
        b"\x00\x01\x02\x03\x04\x05"
        b"\x06\x07\x08\x09\x0a\x0b"
        b"\x86\xdd"
        + ip6_hdr + icmp6_data
    )
    return eth


class TestEthernetPcapReader:
    def test_reads_pcap_global_header(self, tmp_path):
        from boat.trace_replay import EthernetPcapReader

        data = _make_pcap([])
        p = tmp_path / "empty.pcap"
        p.write_bytes(data)
        frames = list(EthernetPcapReader(str(p)))
        assert frames == []

    def test_reads_single_udp_frame(self, tmp_path):
        from boat.trace_replay import EthernetPcapReader

        payload = b"HELLO"
        eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02",
            12345, 30490, payload,
        )
        data = _make_pcap([eth])
        p = tmp_path / "single.pcap"
        p.write_bytes(data)
        frames = list(EthernetPcapReader(str(p)))

        assert len(frames) == 1
        assert frames[0].ethertype == 0x0800
        assert frames[0].timestamp == 0.0
        # Ethernet payload = IP+UDP+payload without L2 header
        assert len(frames[0].payload) == 20 + 8 + len(payload)

    def test_reads_multiple_frames(self, tmp_path):
        from boat.trace_replay import EthernetPcapReader

        frames_data = []
        for i in range(3):
            eth = _udp_packet(
                b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02",
                12345, 30490, b"data" + bytes([i]),
            )
            frames_data.append(eth)
        data = _make_pcap(frames_data)
        p = tmp_path / "multi.pcap"
        p.write_bytes(data)
        frames = list(EthernetPcapReader(str(p)))

        assert len(frames) == 3
        for i in range(3):
            assert frames[i].ethertype == 0x0800
            assert abs(frames[i].timestamp - 0.1 * i) < 0.001

    def test_rejects_non_en10mb(self, tmp_path):
        from boat.trace_replay import EthernetPcapReader, TraceReplayError

        p = tmp_path / "bad.pcap"
        hdr = struct.pack("<IHHiIII", 0xa1b2c3d4, 2, 3, 0, 0, 65535, 0)  # DLT=0
        p.write_bytes(hdr)
        try:
            list(EthernetPcapReader(str(p)))
        except TraceReplayError as e:
            assert "DLT" in str(e)


class TestReconstructIpPacket:
    def _make_replayer(self, replay_src_ip="192.168.1.1",
                       replay_dst_ip="192.168.1.100"):
        from boat.trace_replay import TraceReplayer
        return TraceReplayer(
            buses=["eth0"],
            speed=1.0,
            replay_src_ip=replay_src_ip,
            replay_dst_ip=replay_dst_ip,
        )

    def test_udp_packet_reconstructed_with_new_ips(self):
        replayer = self._make_replayer("10.0.0.1", "10.0.0.2")

        app_data = b"Hello from UDP!"
        eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02",
            12345, 30490, app_data,
        )

        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(
            timestamp=1.0,
            dst_mac=eth[0:6],
            src_mac=eth[6:12],
            ethertype=0x0800,
            payload=eth[14:],
        )

        result = replayer._reconstruct_ip_packet(frame)

        # Verify IP header
        assert result[0] == 0x45  # version=4, ihl=5
        assert result[9] == 17    # protocol = UDP
        # Source IP should be rewritten
        assert result[12:16] == b"\x0a\x00\x00\x01"  # 10.0.0.1
        # Dest IP should be rewritten
        assert result[16:20] == b"\x0a\x00\x00\x02"  # 10.0.0.2

        # Verify IP checksum is valid
        s = 0
        ip_hdr = result[:20]
        for i in range(0, 20, 2):
            word = struct.unpack("!H", ip_hdr[i:i+2])[0]
            s += word
        while s >> 16:
            s = (s & 0xFFFF) + (s >> 16)
        assert s == 0xFFFF, f"IP checksum invalid: {s:#x}"

        # Verify UDP header
        assert result[20:22] == struct.pack("!H", 12345)  # src_port preserved
        assert result[22:24] == struct.pack("!H", 30490)  # dst_port preserved
        udp_len = struct.unpack("!H", result[24:26])[0]
        assert udp_len == 8 + len(app_data)

        # Verify UDP checksum is valid (non-zero since we calculate it)
        udp_csum = struct.unpack("!H", result[26:28])[0]
        assert udp_csum != 0

        # Verify payload
        assert result[28:] == app_data

    def test_icmp_packet_reconstructed_with_new_ips(self):
        replayer = self._make_replayer("10.0.0.1", "10.0.0.2")

        app_data = b"Ping payload"
        eth = _icmp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02",
            app_data,
        )

        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(
            timestamp=1.0,
            dst_mac=eth[0:6],
            src_mac=eth[6:12],
            ethertype=0x0800,
            payload=eth[14:],
        )

        result = replayer._reconstruct_ip_packet(frame)

        # Verify IP header
        assert result[0] == 0x45
        assert result[9] == 1  # protocol = ICMP
        assert result[12:16] == b"\x0a\x00\x00\x01"
        assert result[16:20] == b"\x0a\x00\x00\x02"

        # Verify ICMP type/code preserved (echo request)
        assert result[20] == 8   # type
        assert result[21] == 0   # code

        # Verify ICMP checksum is valid
        s = 0
        icmp_data = result[20:]
        for i in range(0, len(icmp_data), 2):
            if i + 1 < len(icmp_data):
                word = struct.unpack("!H", icmp_data[i:i+2])[0]
            else:
                word = icmp_data[i] << 8
            s += word
        while s >> 16:
            s = (s & 0xFFFF) + (s >> 16)
        assert s == 0xFFFF, f"ICMP checksum invalid: {s:#x}"

    def test_ttl_preserved(self):
        replayer = self._make_replayer("10.0.0.1", "10.0.0.2")

        eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02",
            12345, 30490, b"data", ttl=128,
        )

        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(
            timestamp=1.0,
            dst_mac=eth[0:6],
            src_mac=eth[6:12],
            ethertype=0x0800,
            payload=eth[14:],
        )

        result = replayer._reconstruct_ip_packet(frame)
        assert result[8] == 128  # TTL preserved

    def test_unknown_protocol_passthrough(self):
        replayer = self._make_replayer("10.0.0.1", "10.0.0.2")

        # Build a frame with protocol 0xFD (experimental)
        payload_bytes = b"\x01\x02\x03\x04"
        total_len = 20 + len(payload_bytes)
        ip_hdr = struct.pack("!BBHHHBBH4s4s",
            0x45, 0, total_len, 0xAAAA, 0, 64, 0xFD, 0,
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02")
        s = 0
        for i in range(0, 20, 2):
            word = struct.unpack("!H", ip_hdr[i:i+2])[0]
            s += word
        while s >> 16:
            s = (s & 0xFFFF) + (s >> 16)
        ip_hdr = ip_hdr[:10] + struct.pack("!H", (~s) & 0xFFFF) + ip_hdr[12:]

        eth = b"\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x08\x00" + ip_hdr + payload_bytes

        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(
            timestamp=1.0,
            dst_mac=eth[0:6],
            src_mac=eth[6:12],
            ethertype=0x0800,
            payload=eth[14:],
        )

        result = replayer._reconstruct_ip_packet(frame)
        assert result[9] == 0xFD
        assert result[20:] == payload_bytes


class TestConvertToBinary:
    def _make_replayer(self, **kwargs):
        from boat.trace_replay import TraceReplayer
        params = {
            "buses": ["eth0"],
            "speed": 1.0,
            "replay_src_ip": "192.168.1.1",
            "replay_dst_ip": "192.168.1.100",
        }
        params.update(kwargs)
        return TraceReplayer(**params)

    def _pcap_bytes(self, eth_frames: list[bytes]) -> Path:
        import tempfile
        f = tempfile.NamedTemporaryFile(suffix=".pcap", delete=False)
        f.write(_make_pcap(eth_frames))
        f.close()
        return Path(f.name)

    def test_converts_pcap_to_binary_format(self, tmp_path):
        replayer = self._make_replayer()
        eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02",
            12345, 30490, b"SOMEIP_DATA",
        )
        p = self._pcap_bytes([eth])
        binary = replayer._convert_to_binary(p)
        p.unlink()

        # Each record: 28-byte header + payload
        assert len(binary) >= 28
        magic, event_type, tick, _, payload_size = struct.unpack_from("<IIQqI", binary, 0)

        assert magic == 0xB0A7B0A7
        assert event_type == 0xEE000000 | 0x0800  # eth base | ethertype
        assert tick == 0
        assert payload_size > 0

        # Payload should be the reconstructed IP packet
        payload = binary[28:28 + payload_size]
        assert len(payload) == payload_size
        # Verify it's a valid IP packet with rewritten IPs
        assert payload[12:16] == b"\xc0\xa8\x01\x01"  # 192.168.1.1
        assert payload[16:20] == b"\xc0\xa8\x01\x64"  # 192.168.1.100

    def test_converts_multiple_pcap_frames(self, tmp_path):
        replayer = self._make_replayer()
        frames = [
            _udp_packet(b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"a"),
            _udp_packet(b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12346, 30491, b"bb"),
        ]
        p = self._pcap_bytes(frames)
        binary = replayer._convert_to_binary(p)
        p.unlink()

        # Should have 2 trace records
        offset = 0
        records = 0
        while offset < len(binary):
            if offset + 28 > len(binary):
                break
            magic = struct.unpack_from("<I", binary, offset)[0]
            if magic != 0xB0A7B0A7:
                break
            _, _, _, _, payload_size = struct.unpack_from("<IIQqI", binary, offset)
            records += 1
            offset += 28 + payload_size

        assert records == 2


class TestReconstructIp6Packet:
    def _make_replayer(self, replay_src_ip="2001:db8::1",
                       replay_dst_ip="2001:db8::100"):
        from boat.trace_replay import TraceReplayer
        return TraceReplayer(
            buses=["eth0"],
            speed=1.0,
            replay_src_ip=replay_src_ip,
            replay_dst_ip=replay_dst_ip,
        )

    def test_udp6_packet_reconstructed_with_new_ips(self):
        replayer = self._make_replayer("2001:db8::ff00:42:8329",
                                       "2001:db8::ff00:42:9300")

        app_data = b"Hello from IPv6 UDP!"
        eth = _udp6_packet(
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01",
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02",
            12345, 30490, app_data,
        )

        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(
            timestamp=1.0,
            dst_mac=eth[0:6],
            src_mac=eth[6:12],
            ethertype=0x86DD,
            payload=eth[14:],
        )

        result = replayer._reconstruct_ip_packet(frame)

        # Verify IPv6 header
        assert result[0] >> 4 == 6  # version
        assert result[6] == 17     # next header = UDP
        # Source IP should be rewritten
        assert result[8:24] == b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\xff\x00\x00\x42\x83\x29"
        # Dest IP should be rewritten
        assert result[24:40] == b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\xff\x00\x00\x42\x93\x00"

        # Verify UDP header
        assert result[40:42] == struct.pack("!H", 12345)  # src_port preserved
        assert result[42:44] == struct.pack("!H", 30490)  # dst_port preserved
        udp_len = struct.unpack("!H", result[44:46])[0]
        assert udp_len == 8 + len(app_data)

        # Verify UDP checksum is valid (mandatory for IPv6, non-zero)
        udp_csum = struct.unpack("!H", result[46:48])[0]
        assert udp_csum != 0
        # Verify UDP checksum correctness — one's complement sum should be 0xFFFF
        pseudo = result[8:24] + result[24:40] + struct.pack("!I", udp_len)
        pseudo += b"\x00\x00\x00" + struct.pack("!B", 17)
        s = 0
        data = pseudo + result[40:]
        for i in range(0, len(data), 2):
            word = struct.unpack("!H", data[i:i+2])[0]
            s += word
        while s >> 16:
            s = (s & 0xFFFF) + (s >> 16)
        assert s == 0xFFFF

        # Verify payload
        assert result[48:] == app_data

    def test_icmp6_packet_reconstructed_with_new_ips(self):
        replayer = self._make_replayer("2001:db8::1", "2001:db8::100")

        app_data = b"IPv6 ping payload"
        eth = _icmp6_packet(
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01",
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02",
            app_data,
        )

        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(
            timestamp=1.0,
            dst_mac=eth[0:6],
            src_mac=eth[6:12],
            ethertype=0x86DD,
            payload=eth[14:],
        )

        result = replayer._reconstruct_ip_packet(frame)

        # Verify IPv6 header
        assert result[0] >> 4 == 6
        assert result[6] == 58     # next header = ICMPv6
        assert result[8:24] == b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01"
        assert result[24:40] == b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01\x00"

        # Verify ICMPv6 type/code preserved (echo request)
        assert result[40] == 128  # type
        assert result[41] == 0    # code

        # Verify ICMPv6 checksum is valid (mandatory with pseudo-header)
        pseudo = result[8:24] + result[24:40] + struct.pack("!I", len(result[40:]))
        pseudo += b"\x00\x00\x00" + struct.pack("!B", 58)
        assert _checksum(pseudo + result[40:]) == 0

    def test_ipv6_hop_limit_preserved(self):
        replayer = self._make_replayer("2001:db8::1", "2001:db8::100")

        eth = _udp6_packet(
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01",
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02",
            12345, 30490, b"data", hop_limit=128,
        )

        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(
            timestamp=1.0,
            dst_mac=eth[0:6],
            src_mac=eth[6:12],
            ethertype=0x86DD,
            payload=eth[14:],
        )

        result = replayer._reconstruct_ip_packet(frame)
        assert result[7] == 128  # hop limit preserved

    def test_ipv6_unknown_next_header_passthrough(self):
        replayer = self._make_replayer("2001:db8::1", "2001:db8::100")

        payload_bytes = b"\x01\x02\x03\x04"
        # Build IPv6+unknown (next header 0xFD)
        v_tc_flow = 0x60000000
        ip6_hdr = struct.pack("!IHBB", v_tc_flow, len(payload_bytes), 0xFD, 64)
        ip6_hdr += (
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01" +
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02"
        )
        eth = b"\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x86\xdd" + ip6_hdr + payload_bytes

        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(
            timestamp=1.0,
            dst_mac=eth[0:6],
            src_mac=eth[6:12],
            ethertype=0x86DD,
            payload=eth[14:],
        )

        result = replayer._reconstruct_ip_packet(frame)
        assert result[6] == 0xFD  # next header preserved
        assert result[40:] == payload_bytes

    def test_short_ipv6_payload_returns_empty(self):
        replayer = self._make_replayer()

        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(
            timestamp=1.0,
            dst_mac=b"\x00" * 6,
            src_mac=b"\x00" * 6,
            ethertype=0x86DD,
            payload=b"\x00" * 10,  # too short for IPv6 header
        )

        result = replayer._reconstruct_ip_packet(frame)
        assert result == b""


class TestConvertToBinaryIp6:
    def _make_replayer(self, **kwargs):
        from boat.trace_replay import TraceReplayer
        params = {
            "buses": ["eth0"],
            "speed": 1.0,
            "replay_src_ip": "2001:db8::1",
            "replay_dst_ip": "2001:db8::100",
        }
        params.update(kwargs)
        return TraceReplayer(**params)

    def _pcap_bytes(self, eth_frames: list[bytes]) -> Path:
        import tempfile
        f = tempfile.NamedTemporaryFile(suffix=".pcap", delete=False)
        f.write(_make_pcap(eth_frames))
        f.close()
        return Path(f.name)

    def test_converts_ipv6_pcap_to_binary_format(self, tmp_path):
        replayer = self._make_replayer()
        eth = _udp6_packet(
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01",
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02",
            12345, 30490, b"IPV6_DATA",
        )
        p = self._pcap_bytes([eth])
        binary = replayer._convert_to_binary(p)
        p.unlink()

        assert len(binary) >= 28
        magic, event_type, tick, _, payload_size = struct.unpack_from("<IIQqI", binary, 0)
        assert magic == 0xB0A7B0A7
        assert event_type == 0xEE000000 | 0x86DD  # eth base | ethertype IPv6
        assert tick == 0
        assert payload_size > 0

        payload = binary[28:28 + payload_size]
        assert len(payload) == payload_size
        assert payload[0] >> 4 == 6  # IPv6 version
        assert payload[6] == 17      # UDP

    def test_converts_mixed_ipv4_ipv6_pcap(self, tmp_path):
        replayer = self._make_replayer(
            replay_src_ip="2001:db8::1",
            replay_dst_ip="2001:db8::100",
        )
        ipv4_eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02",
            11111, 22222, b"v4data",
        )
        ipv6_eth = _udp6_packet(
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01",
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02",
            33333, 44444, b"v6data",
        )
        p = self._pcap_bytes([ipv4_eth, ipv6_eth])
        binary = replayer._convert_to_binary(p)
        p.unlink()

        offset = 0
        records = []
        while offset < len(binary):
            if offset + 28 > len(binary):
                break
            magic = struct.unpack_from("<I", binary, offset)[0]
            if magic != 0xB0A7B0A7:
                break
            _, event_type, _, _, payload_size = struct.unpack_from("<IIQqI", binary, offset)
            records.append((event_type, payload_size))
            offset += 28 + payload_size

        assert len(records) == 2
        assert records[0][0] == 0xEE000000 | 0x0800  # IPv4
        assert records[1][0] == 0xEE000000 | 0x86DD  # IPv6
        # Record layout: [header(28)] [payload(N)] [header(28)] [payload(M)]
        payload4 = binary[28:28 + records[0][1]]
        assert payload4[0] >> 4 == 4
        off = 28 + records[0][1] + 28  # skip rec0 header+payload + rec1 header
        payload6 = binary[off:off + records[1][1]]
        assert payload6[0] >> 4 == 6
        assert payload6[6] == 17  # UDP over IPv6


class TestIpFilter:
    """Tests for the ip_filter parameter (post-rewrite filtering)."""

    def _make_replayer(self, ip_filter: set[str] | None = None, **kwargs):
        from boat.trace_replay import TraceReplayer
        params = {"buses": ["eth0"], "speed": 1.0, "replay_src_ip": "192.168.0.100"}
        params.update(kwargs)
        params["ip_filter"] = ip_filter
        return TraceReplayer(**params)

    def test_filter_matches_src(self):
        """Packet whose rewritten src is in the filter set is replayed."""
        replayer = self._make_replayer(ip_filter={"192.168.0.100"})
        eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"data",
        )
        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x0800, eth[14:])
        result = replayer._reconstruct_ip_packet(frame)
        assert len(result) > 0
        assert result[12:16] == b"\xc0\xa8\x00\x64"  # 192.168.0.100

    def test_filter_matches_dst(self):
        """Packet whose rewritten dst is in the filter set is replayed."""
        replayer = self._make_replayer(
            replay_src_ip=None, replay_dst_ip="192.168.0.101",
            ip_filter={"192.168.0.101"},
        )
        eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"data",
        )
        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x0800, eth[14:])
        result = replayer._reconstruct_ip_packet(frame)
        assert len(result) > 0
        assert result[16:20] == b"\xc0\xa8\x00\x65"  # 192.168.0.101

    def test_filter_no_match_returns_empty(self):
        """Packet whose rewritten IPs do not match the filter is dropped."""
        replayer = self._make_replayer(ip_filter={"10.0.0.99"})
        eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"data",
        )
        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x0800, eth[14:])
        result = replayer._reconstruct_ip_packet(frame)
        assert result == b""

    def test_filter_empty_set_no_filtering(self):
        """Empty filter set = no filtering, all packets pass through."""
        replayer = self._make_replayer(ip_filter=set())
        eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"data",
        )
        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x0800, eth[14:])
        result = replayer._reconstruct_ip_packet(frame)
        assert len(result) > 0

    def test_filter_preserves_original_ips_when_no_rewrite(self):
        """Filter still works when no global rewrite is set — checks original IPs."""
        replayer = self._make_replayer(
            replay_src_ip=None, replay_dst_ip=None,
            ip_filter={"10.0.0.1"},
        )
        eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"data",
        )
        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x0800, eth[14:])
        result = replayer._reconstruct_ip_packet(frame)
        assert len(result) > 0
        assert result[12:16] == b"\x0a\x00\x00\x01"  # original IP preserved

    def test_filter_icmp_packet(self):
        """ICMP packets are also filtered by post-rewrite IP."""
        replayer = self._make_replayer(ip_filter={"192.168.0.100"})
        eth = _icmp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", b"ping",
        )
        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x0800, eth[14:])
        result = replayer._reconstruct_ip_packet(frame)
        assert len(result) > 0
        assert result[12:16] == b"\xc0\xa8\x00\x64"


class TestIpMap:
    """Tests for the ip_map parameter (per-IP rewriting)."""

    def _make_replayer(self, ip_map: dict[str, str] | None = None, **kwargs):
        from boat.trace_replay import TraceReplayer
        params = {"buses": ["eth0"], "speed": 1.0}
        params.update(kwargs)
        params["ip_map"] = ip_map
        return TraceReplayer(**params)

    def test_map_src_ip(self):
        """Source IP from the map is rewritten; dest IP is preserved."""
        replayer = self._make_replayer(ip_map={"10.0.0.1": "192.168.0.100"})
        eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"data",
        )
        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x0800, eth[14:])
        result = replayer._reconstruct_ip_packet(frame)
        assert result[12:16] == b"\xc0\xa8\x00\x64"  # mapped src
        assert result[16:20] == b"\x0a\x00\x00\x02"  # original dst

    def test_map_dst_ip(self):
        """Dest IP from the map is rewritten; source IP is preserved."""
        replayer = self._make_replayer(ip_map={"10.0.0.2": "192.168.0.101"})
        eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"data",
        )
        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x0800, eth[14:])
        result = replayer._reconstruct_ip_packet(frame)
        assert result[12:16] == b"\x0a\x00\x00\x01"  # original src
        assert result[16:20] == b"\xc0\xa8\x00\x65"  # mapped dst

    def test_map_both_ips(self):
        """Both src and dst are independently rewritten via the map."""
        replayer = self._make_replayer(ip_map={
            "10.0.0.1": "192.168.0.100",
            "10.0.0.2": "192.168.0.101",
        })
        eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"data",
        )
        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x0800, eth[14:])
        result = replayer._reconstruct_ip_packet(frame)
        assert result[12:16] == b"\xc0\xa8\x00\x64"
        assert result[16:20] == b"\xc0\xa8\x00\x65"

    def test_map_unknown_ip_preserved(self):
        """IPs not in the map keep their original value."""
        replayer = self._make_replayer(ip_map={"99.99.99.99": "1.2.3.4"})
        eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"data",
        )
        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x0800, eth[14:])
        result = replayer._reconstruct_ip_packet(frame)
        assert result[12:16] == b"\x0a\x00\x00\x01"  # original preserved
        assert result[16:20] == b"\x0a\x00\x00\x02"

    def test_map_unknown_ip_falls_back_to_global(self):
        """IPs not in the map fall back to replay_src_ip / replay_dst_ip if set."""
        replayer = self._make_replayer(
            replay_src_ip="10.10.10.10", replay_dst_ip="10.10.10.11",
            ip_map={"99.99.99.99": "1.2.3.4"},  # doesn't match src or dst
        )
        eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"data",
        )
        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x0800, eth[14:])
        result = replayer._reconstruct_ip_packet(frame)
        assert result[12:16] == b"\x0a\x0a\x0a\x0a"  # global fallback
        assert result[16:20] == b"\x0a\x0a\x0a\x0b"

    def test_map_with_icmp(self):
        """ICMP packets are properly rewritten via the map."""
        replayer = self._make_replayer(ip_map={"10.0.0.1": "192.168.0.100"})
        eth = _icmp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", b"ping",
        )
        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x0800, eth[14:])
        result = replayer._reconstruct_ip_packet(frame)
        assert result[12:16] == b"\xc0\xa8\x00\x64"
        assert result[9] == 1  # protocol ICMP
        # ICMP checksum valid
        assert _checksum(result[20:]) == 0

    def test_map_ipv6(self):
        """IPv6 addresses are properly rewritten via the map."""
        replayer = self._make_replayer(ip_map={
            "2001:db8::1": "2001:db8::ff00:42:8329",
        })
        eth = _udp6_packet(
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01",
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02",
            12345, 30490, b"data",
        )
        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x86DD, eth[14:])
        result = replayer._reconstruct_ip_packet(frame)
        assert result[8:24] == b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\xff\x00\x00\x42\x83\x29"
        assert result[24:40] == b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02"


class TestIpFilterAndMap:
    """Combined ip_map + ip_filter: map first, then filter on the result."""

    def _make_replayer(self, ip_map=None, ip_filter=None, **kwargs):
        from boat.trace_replay import TraceReplayer
        params = {"buses": ["eth0"], "speed": 1.0}
        params.update(kwargs)
        params["ip_map"] = ip_map
        params["ip_filter"] = ip_filter
        return TraceReplayer(**params)

    def test_map_then_filter_match(self):
        """Packet mapped to an IP in the filter set is replayed."""
        replayer = self._make_replayer(
            ip_map={"10.0.0.1": "192.168.0.100"},
            ip_filter={"192.168.0.100"},
        )
        eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"data",
        )
        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x0800, eth[14:])
        result = replayer._reconstruct_ip_packet(frame)
        assert len(result) > 0
        assert result[12:16] == b"\xc0\xa8\x00\x64"

    def test_map_then_filter_no_match(self):
        """Packet mapped to an IP NOT in the filter set is dropped."""
        replayer = self._make_replayer(
            ip_map={"10.0.0.1": "192.168.0.100"},
            ip_filter={"10.0.0.99"},
        )
        eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"data",
        )
        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x0800, eth[14:])
        result = replayer._reconstruct_ip_packet(frame)
        assert result == b""

    def test_map_then_filter_multi_conversation(self):
        """Multiple conversations in the same pcap: only matching ones pass."""
        replayer = self._make_replayer(
            ip_map={
                "10.0.0.1": "192.168.0.100",
                "10.0.0.2": "192.168.0.101",
            },
            ip_filter={"192.168.0.100"},
        )
        eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"data",
        )
        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x0800, eth[14:])
        result = replayer._reconstruct_ip_packet(frame)
        assert len(result) > 0  # src maps to 192.168.0.100 -> matches filter

        # Different conversation: src=10.0.0.3, dst=10.0.0.4 (not in map)
        eth2 = _udp_packet(
            b"\x0a\x00\x00\x03", b"\x0a\x00\x00\x04", 11111, 22222, b"other",
        )
        frame2 = EthernetPcapFrame(2.0, eth2[0:6], eth2[6:12], 0x0800, eth2[14:])
        result2 = replayer._reconstruct_ip_packet(frame2)
        assert result2 == b""  # rewritten IPs (original) don't match filter

    def test_map_then_filter_convert_to_binary(self):
        """End-to-end: filtered-out packets produce no binary trace records."""
        replayer = self._make_replayer(
            ip_map={"10.0.0.1": "192.168.0.100"},
            ip_filter={"192.168.0.100"},
        )
        # Frame 1: matches (src maps to 192.168.0.100)
        eth1 = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"keep",
        )
        # Frame 2: does not match (original IPs, no map entry -> preserved -> not in filter)
        eth2 = _udp_packet(
            b"\x0a\x00\x00\x03", b"\x0a\x00\x00\x04", 11111, 22222, b"drop",
        )
        import tempfile
        data = _make_pcap([eth1, eth2])
        p = Path(tempfile.NamedTemporaryFile(suffix=".pcap", delete=False).name)
        p.write_bytes(data)
        binary = replayer._convert_to_binary(p)
        p.unlink()

        # Should have exactly 1 record (frame 2 was filtered out)
        offset = 0
        records = 0
        while offset < len(binary):
            if offset + 28 > len(binary):
                break
            magic = struct.unpack_from("<I", binary, offset)[0]
            if magic != 0xB0A7B0A7:
                break
            records += 1
            _, _, _, _, payload_size = struct.unpack_from("<IIQqI", binary, offset)
            offset += 28 + payload_size
        assert records == 1


class TestEthertypeFilter:
    """Tests for the ethertype_filter parameter (pre-rewrite L2 filter)."""

    def _make_replayer(self, ethertype_filter=None, **kwargs):
        from boat.trace_replay import TraceReplayer
        params = {"buses": ["eth0"], "speed": 1.0}
        params.update(kwargs)
        params["ethertype_filter"] = ethertype_filter
        return TraceReplayer(**params)

    def _pcap_bytes(self, eth_frames: list[bytes]):
        import tempfile
        p = Path(tempfile.NamedTemporaryFile(suffix=".pcap", delete=False).name)
        p.write_bytes(_make_pcap(eth_frames))
        return p

    def test_filter_ipv4_only(self):
        """Only IPv4 frames pass through when filter is {0x0800}."""
        replayer = self._make_replayer(ethertype_filter={0x0800})
        ipv4_eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"v4",
        )
        ipv6_eth = _udp6_packet(
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01",
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02",
            12345, 30490, b"v6",
        )
        p = self._pcap_bytes([ipv4_eth, ipv6_eth])
        binary = replayer._convert_to_binary(p)
        p.unlink()

        offset = 0
        ethertypes = []
        while offset < len(binary):
            if offset + 28 > len(binary):
                break
            magic = struct.unpack_from("<I", binary, offset)[0]
            if magic != 0xB0A7B0A7:
                break
            _, event_type, _, _, payload_size = struct.unpack_from("<IIQqI", binary, offset)
            ethertypes.append(event_type & 0xFFFF)
            offset += 28 + payload_size
        assert ethertypes == [0x0800]  # only IPv4

    def test_filter_ipv6_only(self):
        """Only IPv6 frames pass through when filter is {0x86DD}."""
        replayer = self._make_replayer(ethertype_filter={0x86DD})
        ipv4_eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"v4",
        )
        ipv6_eth = _udp6_packet(
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01",
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02",
            12345, 30490, b"v6",
        )
        p = self._pcap_bytes([ipv4_eth, ipv6_eth])
        binary = replayer._convert_to_binary(p)
        p.unlink()

        offset = 0
        ethertypes = []
        while offset < len(binary):
            if offset + 28 > len(binary):
                break
            magic = struct.unpack_from("<I", binary, offset)[0]
            if magic != 0xB0A7B0A7:
                break
            _, event_type, _, _, payload_size = struct.unpack_from("<IIQqI", binary, offset)
            ethertypes.append(event_type & 0xFFFF)
            offset += 28 + payload_size
        assert ethertypes == [0x86DD]  # only IPv6

    def test_filter_empty_set_no_filtering(self):
        """Empty ethertype_filter set passes all frames."""
        replayer = self._make_replayer(ethertype_filter=set())
        ipv4_eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"v4",
        )
        ipv6_eth = _udp6_packet(
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01",
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02",
            12345, 30490, b"v6",
        )
        p = self._pcap_bytes([ipv4_eth, ipv6_eth])
        binary = replayer._convert_to_binary(p)
        p.unlink()

        offset = 0
        count = 0
        while offset < len(binary):
            if offset + 28 > len(binary):
                break
            magic = struct.unpack_from("<I", binary, offset)[0]
            if magic != 0xB0A7B0A7:
                break
            count += 1
            _, _, _, _, payload_size = struct.unpack_from("<IIQqI", binary, offset)
            offset += 28 + payload_size
        assert count == 2  # both pass


class TestProtocolFilter:
    """Tests for the protocol_filter parameter (pre-rewrite L4 filter)."""

    def _make_replayer(self, protocol_filter=None, **kwargs):
        from boat.trace_replay import TraceReplayer
        params = {"buses": ["eth0"], "speed": 1.0}
        params.update(kwargs)
        params["protocol_filter"] = protocol_filter
        return TraceReplayer(**params)

    def test_filter_udp_only(self):
        """Only UDP packets pass through when filter is {17}."""
        replayer = self._make_replayer(protocol_filter={17})
        eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"data",
        )
        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x0800, eth[14:])
        assert len(replayer._reconstruct_ip_packet(frame)) > 0

    def test_filter_udp_icmpv4(self):
        """Both UDP and ICMPv4 pass when filter is {1, 17}."""
        replayer = self._make_replayer(protocol_filter={1, 17})
        udp_eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"data",
        )
        icmp_eth = _icmp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", b"ping",
        )
        from boat.trace_replay import EthernetPcapFrame
        udp_result = replayer._reconstruct_ip_packet(
            EthernetPcapFrame(1.0, udp_eth[0:6], udp_eth[6:12], 0x0800, udp_eth[14:])
        )
        assert len(udp_result) > 0
        icmp_result = replayer._reconstruct_ip_packet(
            EthernetPcapFrame(2.0, icmp_eth[0:6], icmp_eth[6:12], 0x0800, icmp_eth[14:])
        )
        assert len(icmp_result) > 0

    def test_filter_udp_rejects_icmp(self):
        """UDP-only filter drops ICMP packets (returns empty)."""
        replayer = self._make_replayer(protocol_filter={17})
        eth = _icmp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", b"ping",
        )
        from boat.trace_replay import EthernetPcapFrame
        result = replayer._reconstruct_ip_packet(
            EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x0800, eth[14:])
        )
        assert result == b""

    def test_filter_udp_rejects_unknown(self):
        """UDP-only filter drops unknown protocol packets."""
        replayer = self._make_replayer(protocol_filter={17})
        eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"data",
        )
        from boat.trace_replay import EthernetPcapFrame
        # Modify the protocol byte to 0xFD (unknown)
        payload = eth[14:]
        modified = payload[:9] + bytes([0xFD]) + payload[10:]
        eth_mod = eth[:14] + modified
        result = replayer._reconstruct_ip_packet(
            EthernetPcapFrame(1.0, eth_mod[0:6], eth_mod[6:12], 0x0800, eth_mod[14:])
        )
        assert result == b""

    def test_filter_tcp_blocked(self):
        """TCP packets (protocol 6) are blocked when filter is {17}."""
        replayer = self._make_replayer(protocol_filter={17})
        # Build a minimal TCP-like frame (protocol=6)
        total_len = 20 + 20  # IP header + TCP header (no payload)
        ip_hdr = struct.pack("!BBHHHBBH4s4s",
            0x45, 0, total_len, 0x9ABC, 0, 64, 6, 0,
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02")
        s = 0
        for i in range(0, 20, 2):
            word = struct.unpack("!H", ip_hdr[i:i+2])[0]
            s += word
        while s >> 16:
            s = (s & 0xFFFF) + (s >> 16)
        ip_hdr = ip_hdr[:10] + struct.pack("!H", (~s) & 0xFFFF) + ip_hdr[12:]
        tcp_hdr = b"\x00\x50\x00\x50" + b"\x00" * 16  # minimal TCP header
        eth = b"\x00" * 12 + b"\x08\x00" + ip_hdr + tcp_hdr
        from boat.trace_replay import EthernetPcapFrame
        result = replayer._reconstruct_ip_packet(
            EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x0800, eth[14:])
        )
        assert result == b""

    def test_filter_udp_over_ipv6(self):
        """UDP filter works for IPv6 (protocol 17) by number, not IP version."""
        replayer = self._make_replayer(protocol_filter={17})
        eth = _udp6_packet(
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01",
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02",
            12345, 30490, b"v6data",
        )
        from boat.trace_replay import EthernetPcapFrame
        result = replayer._reconstruct_ip_packet(
            EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x86DD, eth[14:])
        )
        assert len(result) > 0
        assert result[6] == 17  # UDP over IPv6

    def test_filter_icmpv6_over_ipv6(self):
        """ICMPv6 (protocol 58) is allowed when in the filter set."""
        replayer = self._make_replayer(protocol_filter={58})
        eth = _icmp6_packet(
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01",
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02",
            b"ping6",
        )
        from boat.trace_replay import EthernetPcapFrame
        result = replayer._reconstruct_ip_packet(
            EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x86DD, eth[14:])
        )
        assert len(result) > 0
        assert result[6] == 58  # ICMPv6

    def test_filter_empty_set_passes_all(self):
        """Empty protocol_filter set passes all protocols."""
        replayer = self._make_replayer(protocol_filter=set())
        eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"data",
        )
        from boat.trace_replay import EthernetPcapFrame
        result = replayer._reconstruct_ip_packet(
            EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x0800, eth[14:])
        )
        assert len(result) > 0


class TestEthertypeAndProtocolFilter:
    """Combined ethertype + protocol filter (both pre-rewrite)."""

    def test_ipv4_udp_only(self):
        """Only IPv4+UDP packets pass through (end-to-end via _convert_to_binary)."""
        from boat.trace_replay import TraceReplayer
        import tempfile

        replayer = TraceReplayer(
            buses=["eth0"], speed=1.0,
            ethertype_filter={0x0800},
            protocol_filter={17},
        )
        udp_v4 = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"keep",
        )
        icmp_v4 = _icmp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", b"drop",
        )
        udp_v6 = _udp6_packet(
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01",
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02",
            12345, 30490, b"drop",
        )
        data = _make_pcap([udp_v4, icmp_v4, udp_v6])
        p = Path(tempfile.NamedTemporaryFile(suffix=".pcap", delete=False).name)
        p.write_bytes(data)
        binary = replayer._convert_to_binary(p)
        p.unlink()

        offset = 0
        records = 0
        while offset < len(binary):
            if offset + 28 > len(binary):
                break
            magic = struct.unpack_from("<I", binary, offset)[0]
            if magic != 0xB0A7B0A7:
                break
            records += 1
            _, _, _, _, payload_size = struct.unpack_from("<IIQqI", binary, offset)
            offset += 28 + payload_size
        assert records == 1  # only UDPv4 passes both filters
