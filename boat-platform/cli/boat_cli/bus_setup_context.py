"""System prompt builder for `boat ai bus-setup`.

Injects bus configuration commands, environment variables, and hardware
detection workflows.
"""
from __future__ import annotations

_BUS_REFERENCE = """\
## CAN Bus Setup

### Virtual CAN (vcan) — for development and CI
  sudo modprobe vcan
  sudo ip link add vcan0 type vcan && sudo ip link set vcan0 up
  sudo ip link add vcan1 type vcan && sudo ip link set vcan1 up

### Physical CAN (e.g. PEAK PCAN-USB Pro FD)
  # Check hardware detection
  boat can detect

  # Bring up with standard bitrate
  sudo ip link set can0 up type can bitrate 500000
  sudo ip link set can1 up type can bitrate 500000

  # CAN FD (requires FD-capable hardware + drivers)
  sudo ip link set can0 up type can bitrate 500000 dbitrate 2000000 fd on

  # List available CAN interfaces
  ip link show type can

  # Bring down
  sudo ip link set can0 down

### Hardware detection (sysfs)
  The `boat can detect` command (no gateway needed) scans /sys/class/net/ for
  CAN interfaces and identifies:
    - Physical hardware via USB ID (e.g. PEAK PCAN-USB Pro FD = 0c72:0011)
    - Virtual CAN interfaces (vcan*)
    - Driver name, FD capability, link state

## Ethernet Bus Setup

### Virtual Ethernet (veth) — paired virtual interfaces
  sudo ip link add veth0 type veth peer name veth1
  sudo ip link set veth0 up
  sudo ip link set veth1 up

### Physical Ethernet
  # Interfaces appear as e.g. eth0, enp3s0, enx...
  ip link show

  # Bring up
  sudo ip link set eth0 up

## Gateway Environment Variables

  BOAT_CAN_INTERFACES=vcan0,vcan1,can0   # CAN interfaces the gateway manages
  BOAT_ETH_INTERFACES=veth0              # Ethernet interfaces
  BOAT_NODE_TICK_MS=10                   # Node plugin tick interval (ms)
  BOAT_NODE_TICK_US=100                  # Node plugin tick interval (us, high-precision)
  BOAT_NODE_PLUGINS=/path/to/plugin.so   # Colon-separated plugins to load at startup

### Timer backends (auto-selected by tick interval)
  - >= 1ms : SleepTickTimer (std::this_thread::sleep_for)
  - < 1ms  : TimerfdTickTimer (Linux timerfd, absolute-time, no drift)

## Gateway Startup Examples

  # Virtual CAN only
  BOAT_CAN_INTERFACES=vcan0 ./build/debug/src/gateway/grpc_gateway/boat_gateway

  # Physical CAN + virtual CAN
  BOAT_CAN_INTERFACES=can0,can1,vcan0 ./build/debug/.../boat_gateway

  # With plugins
  BOAT_CAN_INTERFACES=vcan0 \\
    BOAT_NODE_PLUGINS=./build/debug/src/plugins/can_tp/can_tp.so \\
    ./build/debug/.../boat_gateway

  # With Ethernet
  BOAT_CAN_INTERFACES=vcan0 \\
    BOAT_ETH_INTERFACES=veth0 \\
    ./build/debug/.../boat_gateway

## Prerequisites

  sudo apt install cmake ninja-build g++ libacl1-dev
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

  # Build
  cmake --preset debug && cmake --build --preset debug

## CLI Commands for Bus Inspection

  boat can list-buses       # List registered interfaces (requires gateway)
  boat can detect           # Detect local CAN hardware (no gateway needed)
  eth list-ifaces           # List Ethernet interfaces (requires gateway)
"""

_SYSTEM_INTRO = """\
You are a BoAt bus configuration assistant.  You help users set up CAN and
Ethernet interfaces for the BoAt simulation platform.

Rules:
1. Output the exact shell commands needed — the user will copy-paste them.
2. Explain which commands need sudo.
3. Distinguish between virtual (vcan*) and physical (can*, en*, eth*) interfaces.
4. When the user mentions specific hardware (PEAK, Kvaser, etc.), check `boat can detect` output.
5. For gateway startup, provide the full command with environment variables.
6. Keep output concise — focus on what the user needs to run.
"""


def build_system_prompt() -> str:
    return _SYSTEM_INTRO + "\n\n" + _BUS_REFERENCE
