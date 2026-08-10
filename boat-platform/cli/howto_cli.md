# BoAt CLI — Installation & Usage

## Prerequisites

- Python >= 3.11
- A running `boat_gateway` (see top-level build instructions in AGENTS.md)
- Virtual CAN interface (for CAN commands):

```bash
sudo modprobe vcan
sudo ip link add vcan0 type vcan && sudo ip link set vcan0 up
```

## Installation

Install the Python SDK first, then the CLI (both as editable installs):

```bash
pip install -e ./boat-platform/sdk/python[dev]
pip install -e ./boat-platform/cli
```

Verify:

```bash
boat --help
```

## Connecting to the Gateway

By default the CLI connects to `localhost:50051`. Override with `--host`:

```bash
boat --host 192.168.1.100:50051 sim status
```

## Global Flags

| Flag | Description |
|------|-------------|
| `--host ADDRESS` | Gateway address (`host:port`, default `localhost:50051`) |
| `--json` | Output raw JSON arrays instead of Rich tables |

Place them before the subcommand. Every subcommand below accepts these flags.

## Subcommands Overview

```
boat sim          Simulation lifecycle (create, start, pause, step, stop, state, list, watch)
boat scenario     Scenario management (create, get, list, delete, validate)
boat replay       Trace replay (start, seek, stream, pause, resume, stop, from-events)
boat frame        Unified frame send/subscribe, list-ifaces (CAN, CANFD, Ethernet, TCP, PDU)
boat can-tp       CAN Transport Protocol (configure, send, remove, subscribe, subscribe-errors, list-sessions) — ISO 15765-2
boat pdu          PDU routing (send, route, remove-route, container, group, list-routes, subscribe)
boat plugin       Plugin management across sim+node scopes (register, list, info, unload)
boat db           PDU database inspection (list, show, signal-routes)
boat trace        Trace recording (start, stop, status)
boat test         System test runner (list-environments, run)
boat ai           AI assistants (scenario, bus-setup, cli, plugin, config)
```

## Workflows

### 1. Simulation Lifecycle

```bash
# List active simulations
boat sim list

# Create and start a simulation from a scenario
boat sim create --scenario-id my_scenario
boat sim start --simulation-id <id>

# Pause, step, resume
boat sim pause --simulation-id <id>
boat sim step --simulation-id <id> --ticks 500
boat sim start --simulation-id <id>

# Stop and clean up
boat sim stop --simulation-id <id>
```

### 2. Listing Available Interfaces

```bash
# Show all CAN and Ethernet interfaces registered on the gateway
boat frame list-ifaces

# JSON output for scripting
boat --json frame list-ifaces
```

### 3. Sending and Receiving Frames

```bash
# Send a CAN frame (interface auto-selected to e.g. vcan0 if omitted)
boat frame send --bus-type can --can-id 0x123 --data AABBCCDD

# Send a CAN FD frame (bus type determines FD flag, no separate --fd flag)
boat frame send --bus-type canfd --can-id 0x123 --data 00112233445566778899AABBCCDDEEFF

# Explicit interface selection
boat frame send --bus-type can --can-id 0x123 --iface can0 --data AABBCCDD

# Send an Ethernet frame
boat frame send --bus-type ethernet --ethertype 0x0800 --dst-ip 10.0.0.1 --data AABB

# Subscribe to incoming frames (streaming, press Ctrl+C to stop)
boat frame subscribe --bus-types can
boat frame subscribe --bus-types ethernet
boat frame subscribe --bus-types can,ethernet
```

### 4. PDU Routing (requires PduRouter plugin on the gateway)

```bash
# Configure a route
boat pdu route --id 0x100 --transport can --iface vcan0

# With a transmission schedule (cyclic every 100ms)
boat pdu route --id 0x100 --transport can --iface vcan0 --send-type cyclic --cycle-ms 100

# I-PDU groups
boat pdu group --id 1 --name "Safety" --pdu 0x100 --pdu 0x200 --disabled
boat pdu enable-group --id 1
boat pdu list-groups
```

### 5. CAN Transport Protocol (ISO 15765-2)

A session is identified by `--nsdu-id` alone. `configure` sets the addressing
up front (`--source-addr`/`--target-addr` are both required, no fallback to
`--nsdu-id`); `send`/`remove`/`subscribe`/`subscribe-errors` then only take
`--nsdu-id` -- no addresses. Re-running `configure` for an already-configured
`--nsdu-id` edits it in place -- refused with an error if a transfer is
currently in flight (retry once it settles, or `remove` first).

```bash
# Configure a CanTp session (nsdu_id must be numeric, hex or decimal)
boat can-tp configure --nsdu-id 0x7E0 --source-addr 0x7E0 --target-addr 0x7E8

# Send a PDU -- small payloads go as a Single Frame automatically,
# larger ones are segmented into First Frame + Consecutive Frames
boat can-tp send --nsdu-id 0x7E0 --data 0123456789ABCDEF...

# Stream decoded RX payloads (completed Single Frames, or fully
# reassembled First Frame + Consecutive Frame transfers)
boat can-tp subscribe --nsdu-id 0x7E0

# Stream N_Result error/abort events instead (N_Bs/N_Cr timeout, wrong CF
# sequence number, buffer overflow) -- fires instead of (not in addition
# to) a subscribe event for an attempt that didn't complete
boat can-tp subscribe-errors --nsdu-id 0x7E0

# Delete a configured session (fails while a multi-frame transfer is in flight)
boat can-tp remove --nsdu-id 0x7E0
```

A single-ID session (one CAN ID used for both directions) is expressed by
passing that same value for both `--source-addr` and `--target-addr`
explicitly -- there is no shortcut that infers it from `--nsdu-id`.

**Timing, CAN FD, and padding** -- all optional, all default to ISO/AUTOSAR
values:

```bash
boat can-tp configure --nsdu-id 0x7E0 --source-addr 0x7E0 --target-addr 0x7E8 \
  --n-bs-ms 1000 --n-cr-ms 1000 \    # ISO default 1000ms each; OBD-II uses 75/150
  --dlc 64 --brs \                   # CAN FD with Bit Rate Switch (--brs needs a bus with a data-phase rate configured)
  --pad-byte 0xCC                    # ISO/AUTOSAR default fill byte (decimal or 0x-hex)
```

**Addressing modes** (ISO 15765-2 §10.3) -- `normal` (default, no address
byte), `extended`, or `mixed` (wire-identical to extended; different
AUTOSAR/ISO semantic label). `extended`/`mixed` use `--address-byte` (N_TA/
N_AE) independently of `--target-addr`, which is what actually lets multiple
connections share one `--target-addr`, disambiguated by that byte:

```bash
# Extended addressing with an address byte independent of target_addr
boat can-tp configure --nsdu-id 0x7E0 --source-addr 0x7E0 --target-addr 0x7E8 \
  --addressing-mode extended --address-byte 0x10

# Two connections sharing one target_addr, disambiguated by address byte
boat can-tp configure --nsdu-id 1 --source-addr 0x7E0 --target-addr 0x7E8 --addressing-mode mixed --address-byte 0x01
boat can-tp configure --nsdu-id 2 --source-addr 0x7E0 --target-addr 0x7E8 --addressing-mode mixed --address-byte 0x02
```

11-bit vs. 29-bit CAN ID isn't a separate addressing mode here -- it's just a
property of the numeric value passed as `--source-addr`/`--target-addr`
(anything > `0x7FF` gets the CAN extended-frame flag automatically).
Conventional 29-bit "Normal Fixed" (`0x18DA<TA><SA>`/`0x18DB<TA><SA>`) and
"Mixed 29-bit" (`0x18CE<TA><SA>`/`0x18CD<TA><SA>`) IDs are yours to construct
and pass like any other CAN ID:

```bash
boat can-tp configure --nsdu-id 1 --source-addr 0x18DAF110 --target-addr 0x18DA10F1
```

If more than one CanTp instance is loaded (one per CAN interface, e.g. a
gateway started with `BOAT_NODE_PLUGINS` pointing at `can_tp.so` twice with
different `iface` configs), pick one with `--iface`:

```bash
boat can-tp configure --nsdu-id 0x7E0 --source-addr 0x7E0 --target-addr 0x7E8 --iface vcan1
boat can-tp send --nsdu-id 0x7E0 --data 0123 --iface vcan1
```

List currently-configured sessions -- across every loaded instance by
default (each row tagged with its `iface`), or scoped to one. `--json`
additionally includes `n_bs_ms`/`n_cr_ms`/`brs`/`pad_byte`/`address_byte`,
left out of the plain table to avoid column truncation:

```bash
boat can-tp list-sessions
boat can-tp list-sessions --iface vcan0
boat --json can-tp list-sessions
```

### 6. Plugin Management

Plugins live in one of two `PluginManager` instances (see `README.md`'s
"Dual PluginManager"): `sim` (simulation-scoped, hot-loadable per running
scenario) or `node` (always-on, loaded once at gateway startup from
`BOAT_NODE_PLUGINS` — CanTp, PduRouter, TCP, SOME/IP, Probe). `plugin list`
shows both in one table with a `scope` column, so this is the answer to
"what CanTp interfaces are currently running" — `config_json` reveals the
`iface` each instance is bound to.

```bash
# List every loaded plugin, both scopes, with their load-time config
boat plugin list

# Load a plugin into the simulation-scoped manager at runtime
boat plugin register --path ./build/debug/src/plugins/probe/probe.so --config '{}'

# Inspect one plugin (--scope defaults to sim; pass --scope node for a node plugin)
boat plugin info "./build/debug/src/plugins/can_tp/can_tp.so?iface=vcan0" --scope node

# Unload -- --scope is always required; --scope node additionally requires
# --yes, since it's immediate and gateway-wide, not scoped to any simulation
boat plugin unload scn-plugin-id --scope sim
boat plugin unload "./build/debug/src/plugins/can_tp/can_tp.so?iface=vcan0" --scope node --yes
```

There is no `register` for node plugins -- they're only ever loaded via
`BOAT_NODE_PLUGINS` at gateway startup today.

### 7. PDU Database Inspection

```bash
# List available databases
boat db list

# Show a specific database
boat db show --db pdu_db.json

# Show signal routes
boat db signal-routes --db pdu_db.json --signal MotorSpeed
```

### 8. Trace Recording & Replay

```bash
# Start recording
boat trace start --simulation-id <id>

# Stop recording
boat trace stop

# Replay a trace
boat replay start --trace-id <id>
```

### 9. AI Assistants

AI commands use an LLM backend (default: Ollama with `qwen2.5-coder:3b`):

```bash
# Configure AI endpoint
boat ai config set --endpoint http://localhost:11434/v1 --model qwen2.5-coder:3b

# Generate a scenario from a description
boat ai scenario "Create a CAN bus with two ECUs exchanging 0x100 and 0x200"

# Get CLI command help
boat ai cli "How do I subscribe to CAN frames?"

# Generate a bus-setup config
boat ai bus-setup "vcan0 with pdu_router"
```

## JSON Mode

Add `--json` to any command for script-friendly output:

```bash
boat --json sim list
boat --json pdu list-routes
```

Output is a JSON array of objects — pipe to `jq` for filtering:

```bash
boat --json pdu list-routes | jq '.[] | select(.transport == "CAN")'
```

## Shell Completion

Shell completions are auto-generated by Typer. To enable:

```bash
# bash
eval "$(_BOAT_COMPLETE=bash_source boat)"

# zsh
eval "$(_BOAT_COMPLETE=zsh_source boat)"

# fish
boat --install-completion fish
```

## Running Tests

```bash
pytest cli/tests -v
```
