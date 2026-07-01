"""Demo: send an HTTP GET request and print the response.

Usage:
    sudo python3 demo/tcp_plugin/tcp_http_get.py <iface> <src_ip> <dst_ip> <dst_port> [path]

Examples:
    sudo python3 demo/tcp_plugin/tcp_http_get.py eth0 10.0.0.1 93.184.216.34 80
    sudo python3 demo/tcp_plugin/tcp_http_get.py eth0 10.0.0.1 93.184.216.34 80 /index.html
"""
from __future__ import annotations

import os
import sys
import threading
import time

SDK_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "sdk", "python")
sys.path.insert(0, os.path.abspath(SDK_PATH))

from boat.tcp import TcpHandle


def main() -> None:
    if len(sys.argv) < 5:
        print(f"Usage: {sys.argv[0]} <iface> <src_ip> <dst_ip> <dst_port> [path]")
        sys.exit(1)

    iface = sys.argv[1]
    src_ip = sys.argv[2]
    dst_ip = sys.argv[3]
    dst_port = int(sys.argv[4])
    path = sys.argv[5] if len(sys.argv) > 5 else "/"

    repo_root = os.path.join(os.path.dirname(__file__), "..", "..")
    so_path = os.path.join(repo_root, "build", "debug", "src", "plugins", "tcp", "tcp.so")
    if not os.path.exists(so_path):
        print(f"Error: TCP plugin not found at {so_path}")
        sys.exit(1)

    config = '{{"iface": "{}"}}'.format(iface)
    tcp = TcpHandle(so_path, config.encode())

    response = bytearray()
    connected = threading.Event()
    done = threading.Event()

    def on_data(cid: int, data: bytes) -> None:
        response.extend(data)

    def on_event(cid: int, event: int) -> None:
        if event == 0:
            connected.set()
        elif event == 1:
            done.set()
        elif event == 4:
            done.set()

    cid = tcp.connect(src_ip, 0, dst_ip, dst_port, on_data=on_data, on_event=on_event)
    print(f"[HTTP] Connecting {src_ip}:{cid} → {dst_ip}:{dst_port} ...", flush=True)

    if not connected.wait(timeout=5):
        print("[HTTP] Connection timed out")
        sys.exit(1)

    # Build and send HTTP GET request
    request = (
        f"GET {path} HTTP/1.0\r\n"
        f"Host: {dst_ip}\r\n"
        f"Connection: close\r\n"
        f"\r\n"
    ).encode()
    ret = tcp.send(cid, request)
    print(f"[HTTP] Sent {ret}/{len(request)} bytes", flush=True)

    if not done.wait(timeout=10):
        print("[HTTP] Timeout waiting for response close")
        # Force close
        tcp.close(cid)
        time.sleep(0.3)

    print(f"[HTTP] Received {len(response)} bytes total", flush=True)
    # Print response headers and body
    try:
        text = response.decode("utf-8", errors="replace")
        print(f"[HTTP] Response body ({len(text)} chars):")
        print(text[:2000])
        if len(text) > 2000:
            print(f"... ({len(text) - 2000} more chars)")
    except Exception:
        print(f"[HTTP] Response (binary): {response.hex()}")

    print("[HTTP] Done")


if __name__ == "__main__":
    main()
