"""Demo: replay TCP streams from a pcap file through the TCP plugin.

Usage:
    sudo python3 demo/tcp_plugin/tcp_pcap_replay.py <iface> <src_ip> <pcap_file> [--ip-map old=new,...]

Examples:
    sudo python3 demo/tcp_plugin/tcp_pcap_replay.py eth0 10.0.0.1 capture.pcap
    sudo python3 demo/tcp_plugin/tcp_pcap_replay.py enx28107b9f2017 120.120.120.1 trace.pcap --ip-map 192.168.1.1=120.120.120.1
"""
from __future__ import annotations

import os
import sys
import time

SDK_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "sdk", "python")
sys.path.insert(0, os.path.abspath(SDK_PATH))

from boat.trace_replay import EthernetPcapReader
from boat.tcp import TcpHandle


def parse_ip_map(text: str | None) -> dict[str, str]:
    """Parse --ip-map old1=new1,old2=new2 into a dict."""
    result: dict[str, str] = {}
    if not text:
        return result
    for pair in text.split(","):
        pair = pair.strip()
        if "=" in pair:
            k, v = pair.split("=", 1)
            result[k.strip()] = v.strip()
    return result


def main() -> None:
    if len(sys.argv) < 4:
        print(f"Usage: {sys.argv[0]} <iface> <src_ip> <pcap_file> [--ip-map old=new,...]")
        sys.exit(1)

    iface = sys.argv[1]
    src_ip = sys.argv[2]
    pcap_path = sys.argv[3]
    ip_map = parse_ip_map(
        sys.argv[sys.argv.index("--ip-map") + 1] if "--ip-map" in sys.argv else None
    )

    if not os.path.exists(pcap_path):
        print(f"Error: pcap file not found: {pcap_path}")
        sys.exit(1)

    repo_root = os.path.join(os.path.dirname(__file__), "..", "..")
    so_path = os.path.join(repo_root, "build", "debug", "src", "plugins", "tcp", "tcp.so")
    if not os.path.exists(so_path):
        print(f"Error: TCP plugin not found at {so_path}")
        sys.exit(1)

    config = '{{"iface": "{}"}}'.format(iface)
    tcp = TcpHandle(so_path, config.encode())

    # ── Read pcap and group TCP streams ───────────────────────────────────
    import struct, ipaddress

    streams: dict[tuple, dict] = {}
    total_frames = 0

    with EthernetPcapReader(pcap_path) as reader:
        for frame in reader:
            payload = frame.payload
            if frame.ethertype == 0x0800:
                if len(payload) < 20:
                    continue
                protocol = payload[9]
                if protocol != 6:
                    continue
                ihl = (payload[0] & 0x0F) * 4
                tcp_start = ihl
                src_ip_bytes = payload[12:16]
                dst_ip_bytes = payload[16:20]
            elif frame.ethertype == 0x86DD:
                if len(payload) < 40:
                    continue
                protocol = payload[6]
                if protocol != 6:
                    continue
                tcp_start = 40
                src_ip_bytes = payload[8:24]
                dst_ip_bytes = payload[24:40]
            else:
                continue

            if len(payload) < tcp_start + 20:
                continue
            tcp_hdr = payload[tcp_start:]
            src_port = (tcp_hdr[0] << 8) | tcp_hdr[1]
            dst_port = (tcp_hdr[2] << 8) | tcp_hdr[3]
            flags = tcp_hdr[13]
            data_off = ((tcp_hdr[12] >> 4) & 0x0F) * 4
            tcp_data = tcp_hdr[data_off:]
            is_syn = bool(flags & 0x02)
            is_fin = bool(flags & 0x01)

            orig_src = str(ipaddress.ip_address(bytes(src_ip_bytes)))
            orig_dst = str(ipaddress.ip_address(bytes(dst_ip_bytes)))
            mapped_src = ip_map.get(orig_src, orig_src)
            mapped_dst = ip_map.get(orig_dst, orig_dst)

            key = (mapped_src, src_port, mapped_dst, dst_port)
            if key not in streams:
                streams[key] = {
                    "src_ip": mapped_src,
                    "dst_ip": mapped_dst,
                    "src_port": src_port,
                    "dst_port": dst_port,
                    "payloads": [],
                    "syn": is_syn,
                    "fin": is_fin,
                }
            if tcp_data:
                streams[key]["payloads"].append(tcp_data)

    if not streams:
        print("[PCAP] No TCP streams found")
        sys.exit(1)

    print(f"[PCAP] Found {len(streams)} TCP stream(s) from {total_frames} frames", flush=True)

    # ── Replay each stream ────────────────────────────────────────────────
    total_sent = 0
    for key, stream in streams.items():
        print(f"[PCAP] Stream: {stream['src_ip']}:{stream['src_port']} → "
              f"{stream['dst_ip']}:{stream['dst_port']}  "
              f"({len(stream['payloads'])} segments, syn={stream['syn']})", flush=True)

        conn_id = tcp.connect(
            src_ip, stream["src_port"],
            stream["dst_ip"], stream["dst_port"],
        )
        if conn_id < 0:
            print(f"[PCAP]   Failed to connect", flush=True)
            continue

        for i, data in enumerate(stream["payloads"]):
            ret = tcp.send(conn_id, data)
            if ret > 0:
                total_sent += ret

        if stream["fin"]:
            tcp.close(conn_id)
            print(f"[PCAP]   Sent {len(stream['payloads'])} segments, closed", flush=True)
        else:
            print(f"[PCAP]   Sent {len(stream['payloads'])} segments (no FIN)", flush=True)

        time.sleep(0.1)  # small gap between streams

    print(f"[PCAP] Done — {total_sent} bytes sent across {len(streams)} stream(s)", flush=True)


if __name__ == "__main__":
    main()
