# BoAt Platform

Vehicle simulation gateway for CAN (FD) and Ethernet networks.

## Prerequisites

| Dependency | Minimum version | Install |
|---|---|---|
| CMake | 3.24 | [Download binary](https://github.com/Kitware/CMake/releases) or use Kitware APT repo |
| Ninja | any | `apt install ninja-build` |
| GCC / G++ | 11 | `apt install build-essential` |
| libacl1-dev | any | `apt install libacl1-dev` |
| Rust (rustup) | 1.78+ | `curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \| sh` |

Rust/cargo is **not** used by the boat codebase itself — it is a transitive build dependency of [iceoryx2](https://github.com/eclipse-iceoryx/iceoryx2), which provides zero-copy shared-memory IPC for large payloads. If you cannot install Rust, see [Building without SHM](#building-without-shm).

## Build

```bash
cmake --preset debug
cmake --build --preset debug
```

Release build:

```bash
cmake --preset release
cmake --build --preset release
```

The gateway binary is at `build/{preset}/src/gateway/grpc_gateway/boat_gateway`.

## Run

```bash
# Create virtual CAN buses
sudo modprobe vcan
sudo ip link add vcan0 type vcan && sudo ip link set vcan0 up

# Start the gateway with virtual CAN
BOAT_CAN_INTERFACES=vcan0 \
  BOAT_ETH_INTERFACES=veth0 \
  ./build/debug/src/gateway/grpc_gateway/boat_gateway

# Start the gateway with physical CAN (e.g. PEAK PCAN-USB Pro FD)
# Interfaces must be brought up manually (see CAN Hardware section)
BOAT_CAN_INTERFACES=can0,can1,vcan0 \
  ./build/debug/src/gateway/grpc_gateway/boat_gateway
```

The gRPC server listens on `0.0.0.0:50051`.

## CAN Hardware

Physical CAN interfaces (e.g. PEAK, Kvaser, gs_usb) are supported via `PhysicalCanDriver` which reads hardware metadata from sysfs. The gateway auto-selects the driver: `vcan*` interfaces use `VirtualCanDriver`, all others use `PhysicalCanDriver`.

```bash
# Bring up a physical CAN interface (adjust bitrate to your setup)
sudo ip link set can0 up type can bitrate 500000

# The `boat can detect` command scans available CAN hardware (no gateway needed)
boat can detect

# List registered interfaces with metadata (requires active gateway)
boat can list-buses
boat --json can list-buses
```

## Known issues

- **Ubuntu 22.04** ships cmake 3.22 (too old). Use the [Kitware binary release](https://github.com/Kitware/CMake/releases) or the official APT repo.
- **System cargo** (apt package `cargo`) may be too old. Always use `rustup`.
- <code>build/debug/_deps/iceoryx-src/iceoryx_hoofs/posix/filesystem/source/posix_acl.cpp</code> requires `sys/acl.h` — install `libacl1-dev`.
