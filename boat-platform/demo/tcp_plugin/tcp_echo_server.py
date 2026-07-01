"""Demo: TCP echo server — echoes back all received data.

Usage:
    sudo python3 demo/tcp_plugin/tcp_echo_server.py <iface> <bind_ip> <port>

Examples:
    sudo python3 demo/tcp_plugin/tcp_echo_server.py eth0 0.0.0.0 8080
    sudo python3 demo/tcp_plugin/tcp_echo_server.py enx28107b9f2017 120.120.120.1 9999
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time

SDK_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "sdk", "python")
sys.path.insert(0, os.path.abspath(SDK_PATH))

from boat.tcp import TcpHandle


def main() -> None:
    if len(sys.argv) < 4:
        print(f"Usage: {sys.argv[0]} <iface> <bind_ip> <port>")
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

    # Block incoming SYN at iptables INPUT so the kernel doesn't see it
    # (our raw socket still receives the frame pre-netfilter)
    rule = f"INPUT -p tcp --dport {bind_port} --syn -j DROP"
    if subprocess.run(["iptables", "-C"] + rule.split(), capture_output=True).returncode != 0:
        subprocess.run(["iptables", "-A"] + rule.split(), check=True)
        print(f"[ECHO] iptables: blocking SYN for port {bind_port}", flush=True)

    def cleanup() -> None:
        subprocess.run(["iptables", "-D"] + rule.split(), capture_output=True)

    echo_count: dict[int, int] = {}

    def on_data(cid: int, data: bytes) -> None:
        echo_count[cid] = echo_count.get(cid, 0) + 1
        print(f"[ECHO] cid={cid}  RX {len(data)} bytes → echoing back", flush=True)
        tcp.send(cid, data)

    def on_event(cid: int, event: int) -> None:
        if event == 0:
            print(f"[ECHO] cid={cid} ACCEPTED", flush=True)
        elif event == 1:
            cnt = echo_count.pop(cid, 0)
            print(f"[ECHO] cid={cid} CLOSED (echoed {cnt} chunks)", flush=True)
        elif event == 4:
            print(f"[ECHO] cid={cid} ERROR", flush=True)

    lid = tcp.listen(bind_ip, bind_port, on_data=on_data, on_event=on_event)
    print(f"[ECHO] Listening on {bind_ip}:{bind_port} (lid={lid})", flush=True)
    print("[ECHO] Press Ctrl+C to stop", flush=True)

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda s, f: stop.set())
    while not stop.is_set():
        time.sleep(0.5)

    subprocess.run(["iptables", "-D"] + rule.split(), capture_output=True)
    print("[ECHO] Stopped")


if __name__ == "__main__":
    main()
