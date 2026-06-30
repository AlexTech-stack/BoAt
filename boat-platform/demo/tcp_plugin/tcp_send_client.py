"""Demo: send a TCP payload to a remote server using the BoAt TCP plugin.

Usage:
    sudo python3 demo/tcp_plugin/tcp_send_client.py <server_ip> <port> [hex_payload]

Examples:
    sudo python3 demo/tcp_plugin/tcp_send_client.py 192.168.1.100 8080
    sudo python3 demo/tcp_plugin/tcp_send_client.py 192.168.1.100 8080 AABBCCDDEEFF

Requirements:
    - Gateway must be built: cmake --build --preset debug
    - Plugin must be built (tcp.so available)
    - Raw socket requires root (sudo)
"""
from __future__ import annotations

import sys
import os
import random
import threading
import time

# Ensure we can import the boat SDK
SDK_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "sdk", "python")
sys.path.insert(0, os.path.abspath(SDK_PATH))

from boat.tcp import TcpHandle


def main() -> None:
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <server_ip> <port> [hex_payload]")
        sys.exit(1)

    server_ip = sys.argv[1]
    server_port = int(sys.argv[2])
    payload_hex = sys.argv[3] if len(sys.argv) > 3 else "AABBCCDDEEFF"
    payload = bytes.fromhex(payload_hex)

    # TCP plugin path (relative to the repo root)
    repo_root = os.path.join(os.path.dirname(__file__), "..", "..")
    so_path = os.path.join(
        repo_root, "build", "debug", "src", "plugins", "tcp", "tcp.so"
    )
    if not os.path.exists(so_path):
        print(f"Error: TCP plugin not found at {so_path}")
        print("Build it with: cmake --build --preset debug")
        sys.exit(1)

    # Find the source interface with an IP on the same subnet as the server
    # Default to the first interface with a matching subnet
    sock_candidate = None
    with open("/proc/net/route") as f:
        for line in f.readlines()[1:]:
            parts = line.strip().split()
            if len(parts) >= 8 and parts[7] == "00000000":
                sock_candidate = parts[0]
                break
    iface = sock_candidate or "eth0"

    # Also determine source IP
    src_ip = "0.0.0.0"
    try:
        import socket as sock_mod
        s = sock_mod.socket(sock_mod.AF_INET, sock_mod.SOCK_DGRAM)
        s.connect((server_ip, server_port))
        src_ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass

    # Start the TCP plugin
    config = '{{"iface": "{}"}}'.format(iface)
    tcp = TcpHandle(so_path, config.encode())

    connected = threading.Event()

    def on_event(cid: int, event: int) -> None:
        if event == 0:
            print(f"[+] Connected to {server_ip}:{server_port}")
            connected.set()
        elif event == 4:
            print("[-] Connection error/timeout")

    src_port = random.randint(40000, 60000)
    cid = tcp.connect(
        src_ip, src_port,
        server_ip, server_port,
        on_event=on_event,
    )
    print(f"[~] {src_ip}:{src_port} → {server_ip}:{server_port} ...")

    if connected.wait(timeout=5):
        time.sleep(0.3)
        ret = tcp.send(cid, payload)
        if ret > 0:
            print(f"[+] Sent {ret} bytes: {payload.hex()}")
        else:
            print(f"[-] Send returned {ret}")
        time.sleep(0.5)
        tcp.close(cid)
        time.sleep(0.3)
        print("[+] Connection closed")
    else:
        print("[-] Connection timed out")


if __name__ == "__main__":
    main()
