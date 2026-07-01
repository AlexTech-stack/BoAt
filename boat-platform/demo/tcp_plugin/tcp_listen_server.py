"""Demo: TCP server that accepts connections and prints received data.

Usage:
    sudo python3 demo/tcp_plugin/tcp_listen_server.py <iface> <bind_ip> <port>

Examples:
    sudo python3 demo/tcp_plugin/tcp_listen_server.py eth0 0.0.0.0 8080
    sudo python3 demo/tcp_plugin/tcp_listen_server.py enx28107b9f2017 120.120.120.1 9999
"""
from __future__ import annotations

import sys
import os
import signal
import threading
import time

SDK_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "sdk", "python")
sys.path.insert(0, os.path.abspath(SDK_PATH))

from boat.tcp import TcpHandle


def main() -> None:
    if len(sys.argv) < 4:
        print("Usage:")
        print(f"  {sys.argv[0]} <iface> <bind_ip> <port>")
        print()
        print("Examples:")
        print(f"  sudo {sys.argv[0]} eth0 0.0.0.0 8080")
        print(f"  sudo {sys.argv[0]} enx28107b9f2017 120.120.120.1 9999")
        sys.exit(1)

    iface = sys.argv[1]
    bind_ip = sys.argv[2]
    bind_port = int(sys.argv[3])

    repo_root = os.path.join(os.path.dirname(__file__), "..", "..")
    so_path = os.path.join(repo_root, "build", "debug", "src", "plugins", "tcp", "tcp.so")
    if not os.path.exists(so_path):
        print(f"Error: TCP plugin not found at {so_path}")
        sys.exit(1)

    config = '{{"iface": "{}"}}'.format(iface)
    tcp = TcpHandle(so_path, config.encode())

    stop = threading.Event()

    # Per-connection data tracking
    conn_data: dict[int, bytearray] = {}

    def on_data(cid: int, data: bytes) -> None:
        if cid not in conn_data:
            conn_data[cid] = bytearray()
        conn_data[cid].extend(data)
        # Print hex as data arrives
        print(f"[SRV] conn={cid}  {len(data)} bytes: {data.hex()}")

    def on_event(cid: int, event: int) -> None:
        if event == 0:  # TCP_EVENT_CONNECTED
            print(f"[SRV] conn={cid} ACCEPTED")
        elif event == 1:  # TCP_EVENT_CLOSED
            total = len(conn_data.pop(cid, bytearray()))
            print(f"[SRV] conn={cid} CLOSED (received {total} bytes)")
        elif event == 4:  # TCP_EVENT_ERROR
            print(f"[SRV] conn={cid} ERROR")

    lid = tcp.listen(bind_ip, bind_port, on_data=on_data, on_event=on_event)
    print(f"[SRV] Listening on {bind_ip}:{bind_port} (iface={iface}, lid={lid})")
    print("[SRV] Press Ctrl+C to stop")

    # Run until SIGINT
    signal.signal(signal.SIGINT, lambda s, f: stop.set())
    while not stop.is_set():
        time.sleep(0.5)

    print("[SRV] Stopped")


if __name__ == "__main__":
    main()
