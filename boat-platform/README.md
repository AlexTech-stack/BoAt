# BoAt Platform

Vehicle simulation gateway for CAN (FD) and Ethernet networks.

## Supported platforms

BoAt is split by *which machine a component runs on*:

| Where it runs | OS | What |
|---|---|---|
| **Gateway host** | **Linux only**, x86_64 or aarch64 — see [Prebuilt binaries](#prebuilt-binaries) for distributions | `boat_gateway`, its `.so` plugins, the launcher agent (`ui/launcher_agent.py`) and local launcher (`ui/launcher.py`), `boat test run` when it starts its own gateway, and the `start_*.sh`/`stop_*.sh` scripts |
| **Clients** | Any OS with Python 3.11+ | The `boat-py` SDK, the `boat` CLI pointed at a gateway (`--host` / `BOAT_HOST`), `admin_gui/`, the other web UIs and `tools/` services, node scripts, and offline tools such as `boat trace score` and `tools/dbc2boatjson.py` |

The gateway is Linux-only by design, not by omission: it is built on SocketCAN and `vcan`,
raw `AF_PACKET` sockets for Ethernet, `timerfd` for drift-free ticks, and `.so` plugins loaded
with `dlopen`. None of these exist on Windows or macOS, and **no port is planned** — CMake
refuses to configure on any other OS. The client side is pure Python with no OS-specific
code paths, but CI only exercises it on Linux.

Working from Windows: run the gateway on a Linux machine and point the SDK, CLI or Admin GUI
at it over gRPC. WSL2 or a Linux VM may be enough for virtual buses (`vcan`) but is untested;
physical CAN hardware needs a native Linux host.

### Prebuilt binaries

Each release ships `boat_gateway` and its plugins as `.tar.gz`, `.deb` and `.rpm` for
**amd64 (x86_64)** and **arm64 (aarch64)**, plus a multi-arch container image
`ghcr.io/alextech-stack/boat:<tag>`. All of them carry the same binaries, built natively per
architecture in [`Dockerfile.build`](Dockerfile.build) (AlmaLinux 8). They need **glibc 2.28
and GCC 8's libstdc++ or newer**, and nothing beyond the C/C++ runtime and `libacl`, so one
build covers:

| Family | Releases |
|---|---|
| Debian / Ubuntu (incl. Raspberry Pi OS 64-bit) | Debian 10+, Ubuntu 20.04+ — use the `.deb` |
| RHEL / Rocky / AlmaLinux (incl. CentOS AutoSD) | 8, 9, 10 — use the `.rpm` |
| Fedora, openSUSE Leap / SLES 15 | current releases — `.rpm`, or the `.tar.gz` |
| Anything else with glibc ≥ 2.28 | the `.tar.gz` |

CI starts the gateway on AlmaLinux 8/9/10, Debian 11/12, Ubuntu 20.04/22.04/24.04, Fedora and
openSUSE Leap 15 on both architectures before anything is published (`portable-build.yml`).
musl-based distributions (Alpine) are not supported.

**SocketCAN on RHEL-family kernels:** `can`, `can-raw`, `can-bcm`, `can-gw` and the USB
adapter drivers are in the default `kernel-modules` package, but **`vcan` is in
`kernel-modules-extra`**, which is not installed by default:

```bash
sudo dnf install kernel-modules-extra-$(uname -r) && sudo modprobe vcan
```

Building from source is tested on Ubuntu 22.04 and 24.04 (the prerequisites below are for
those); to produce binaries for other distributions, build in `Dockerfile.build` instead.

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

The gRPC server listens on `0.0.0.0:50051` by default. Set `BOAT_GRPC_PORT` to
run more than one gateway instance on one host (each on its own port) — the
gateway refuses to start if the port is already taken, rather than silently
sharing it via gRPC's `SO_REUSEPORT` (see `backlog/gateway_backlog.md`).

On the client side, `boat --host`, `BoAtClient`, and every SDK `*Node` class
(`FrameNode`, `PduNode`, `BusNode`, `PduMessageNode`) resolve which gateway to
talk to as: explicit
address/`--host` argument > `BOAT_HOST` env var (`host:port`) >
`localhost:50051`. A node script/CLI invocation written with no address
hardcoded is therefore portable across any gateway on any device — point it
elsewhere with `BOAT_HOST=<ip>:<port>` in the environment it runs in, without
touching the script.

## CAN Hardware

Physical CAN interfaces (e.g. PEAK, Kvaser, gs_usb) are supported via `PhysicalCanDriver` which reads hardware metadata from sysfs. The gateway auto-selects the driver: `vcan*` interfaces use `VirtualCanDriver`, all others use `PhysicalCanDriver`.

```bash
# Bring up a physical CAN interface (adjust bitrate to your setup)
sudo ip link set can0 up type can bitrate 500000

# No CLI hardware-detection command; inspect CAN hardware directly
ip -d link show type can

# List interfaces the gateway has access to, with metadata (requires active gateway)
boat frame list-ifaces
boat --json frame list-ifaces
```

## Architecture

### Dual PluginManager

The gateway runs **two independent `PluginManager` instances**, each with a separate tick domain:

| Manager | Lifecycle | Tick phase | Plugin set |
|---|---|---|---|
| `node_manager` | Always-on (gateway lifetime) | `node_plugins` | Plugins from `BOAT_NODE_PLUGINS` env var (e.g. CanTp, SOME/IP) |
| `plugin_manager` | Per-simulation (loaded/unloaded via gRPC) | `sim_plugins`, via `TickScheduler::TickIfRunning` | Scenario-declared plugins from `StartSimulation` RPC |

Both are ordered phases of one `TickAuthority`, so the interval is the same for
both (`BOAT_NODE_TICK_MS`/`_US`) and the order is fixed:
`node_plugins` → `sim_plugins` → `replay`. This replaced an earlier "double-tick"
design in which each manager had its own wall-clock thread and the two never
agreed; the split now serves two distinct **lifetimes**, not two clocks:

- **Node plugins** (e.g. CAN Transport Protocol) must stay alive and responsive on the bus regardless of whether any simulation is running. If a CAN diagnostic request arrives, the CanTp plugin needs to react even with no active scenario.
- **Simulation plugins** are loaded per-scenario, run the deterministic tick pipeline (reseed → dispatch → plugin ticks → advance `SimClock`), and are fully torn down when the simulation stops. They are part of the reproducible simulation state.

Both managers use the same C ABI (`BoatPluginVTable`) and can load the same `.so` files. A plugin can be loaded into both managers simultaneously (e.g. a vehicle dynamics node that runs persistently while a test simulation loads additional signal-processing plugins).

The `node_plugins` phase also ticks the `pdu_router` plugin (via its `on_tick`), driving the PDU transmission engine so scheduled CAN/Ethernet frames are sent on time even outside simulation. The PDU router is a node plugin (`pdu_router.so`), not gateway-core logic.

**gRPC surface follows the same split.** `PluginService` (`boat plugin register`) only ever talks to `plugin_manager` (the simulation-scoped one) — node plugins loaded via `BOAT_NODE_PLUGINS` are invisible to it. `NodePluginService` (`ListNodePlugins`/`GetNodePluginInfo`/`UnloadNodePlugin`) exposes `node_manager` instead — no register RPC, since node plugins are only ever loaded at gateway startup today. `boat plugin list` queries both and merges them into one table with a `scope` column so you don't need to know which manager a given plugin lives in; `boat plugin unload <id> --scope {sim,node}` requires an explicit scope, and `--scope node` additionally requires `--yes` since it's immediate and gateway-wide, not scoped to any simulation.

## Known issues

- **Ubuntu 22.04** ships cmake 3.22 (too old). Use the [Kitware binary release](https://github.com/Kitware/CMake/releases) or the official APT repo.
- **System cargo** (apt package `cargo`) may be too old. Always use `rustup`.
- <code>build/debug/_deps/iceoryx-src/iceoryx_hoofs/posix/filesystem/source/posix_acl.cpp</code> requires `sys/acl.h` — install `libacl1-dev`.

## License

Apache License 2.0 — see [LICENSE](LICENSE) (identical to the copy at the
repository root), plus [NOTICE](../NOTICE) and
[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) for the components fetched
at build time.
