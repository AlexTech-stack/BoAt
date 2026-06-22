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
