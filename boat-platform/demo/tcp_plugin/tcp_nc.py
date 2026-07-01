"""Demo: netcat-like tool over the BoAt TCP plugin.

Usage:
    # Client mode: connect to a server
    echo "hello" | sudo python3 demo/tcp_plugin/tcp_nc.py <iface> <src_ip> <dst_ip> <port>

    # Server mode (listen)
    sudo python3 demo/tcp_plugin/tcp_nc.py -l <iface> <bind_ip> <port>

Examples:
    sudo python3 demo/tcp_plugin/tcp_nc.py enx28107b9f2017 120.120.120.1 120.120.120.3 1234
    sudo python3 demo/tcp_plugin/tcp_nc.py -l enx28107b9f2017 0.0.0.0 9999
"""
from __future__ import annotations

import os
import random
import select
import signal
import subprocess
import sys
import threading
import time

SDK_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "sdk", "python")
sys.path.insert(0, os.path.abspath(SDK_PATH))

from boat.tcp import TcpHandle


def main() -> None:
    args = list(sys.argv)
    server_mode = "-l" in args
    if server_mode:
        args.remove("-l")

    if server_mode:
        if len(args) < 4:
            print("Server mode usage: <iface> <bind_ip> <port>")
            sys.exit(1)
        iface = args[1]
        bind_ip = args[2]
        bind_port = int(args[3])
    else:
        if len(args) < 5:
            print("Client mode usage: <iface> <src_ip> <dst_ip> <port>")
            sys.exit(1)
        iface = args[1]
        src_ip = args[2]
        target_ip = args[3]
        target_port = int(args[4])

    repo_root = os.path.join(os.path.dirname(__file__), "..", "..")
    so_path = os.path.join(repo_root, "build", "debug", "src", "plugins", "tcp", "tcp.so")
    if not os.path.exists(so_path):
        print(f"Error: TCP plugin not found at {so_path}", file=sys.stderr)
        sys.exit(1)

    config = '{{"iface": "{}"}}'.format(iface)
    tcp = TcpHandle(so_path, config.encode())

    stop = threading.Event()

    # ── Server mode ───────────────────────────────────────────────────────
    if server_mode:
        rule = f"INPUT -p tcp --dport {bind_port} --syn -j DROP"
        if subprocess.run(["iptables", "-C"] + rule.split(), capture_output=True).returncode != 0:
            subprocess.run(["iptables", "-A"] + rule.split(), check=True)

        def on_data(cid: int, data: bytes) -> None:
            sys.stdout.buffer.write(data)
            sys.stdout.flush()

        lid = tcp.listen(bind_ip, bind_port, on_data=on_data)
        print(f"[NC] Listening on {bind_ip}:{bind_port} (lid={lid})", flush=True)

        signal.signal(signal.SIGINT, lambda s, f: stop.set())
        while not stop.is_set():
            time.sleep(0.5)

        subprocess.run(["iptables", "-D"] + rule.split(), capture_output=True)
        print("[NC] Stopped")
        return

    # ── Client mode ───────────────────────────────────────────────────────
    connected = threading.Event()

    def on_event(cid: int, event: int) -> None:
        if event == 0:
            connected.set()
        elif event == 1:
            stop.set()
        elif event == 4:
            print("[NC] Connection error", file=sys.stderr, flush=True)
            stop.set()

    def on_data(cid: int, data: bytes) -> None:
        sys.stdout.buffer.write(data)
        sys.stdout.flush()

    cid = tcp.connect(src_ip, random.randint(40000, 60000), target_ip, target_port, on_data=on_data, on_event=on_event)
    if not connected.wait(timeout=10):
        print("[NC] Connection timed out", file=sys.stderr, flush=True)
        sys.exit(1)

    # Read stdin and send
    def stdin_reader():
        while not stop.is_set():
            r, _, _ = select.select([sys.stdin], [], [], 0.2)
            if r:
                data = os.read(sys.stdin.fileno(), 4096)
                if not data:
                    break
                tcp.send(cid, data)
        tcp.close(cid)

    reader = threading.Thread(target=stdin_reader, daemon=True)
    reader.start()

    signal.signal(signal.SIGINT, lambda s, f: stop.set())
    while not stop.is_set():
        time.sleep(0.5)

    print("[NC] Done")


if __name__ == "__main__":
    main()
