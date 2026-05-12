# Technical Plan - BoAt Platform

## Functional Requirements (FR)

1. Simulation lifecycle management with commands: `init`, `run`, `pause`, `step`, `reset`, `stop`
2. Deterministic tick-based execution across repeated runs
3. Plugin loading and unloading at runtime
4. Event recording and deterministic replay
5. Signal injection for scenario manipulation and testing
6. Fault injection for resilience and failure-mode validation
7. Hardware-in-the-loop (HIL) bridge for external interfaces
8. Multi-scenario batch execution for regression and CI
9. Real-time monitoring dashboard for state, signals, and metrics

## Non-Functional Requirements (NFR)

- Soft real-time latency: <= 1 ms jitter in simulated mode
- Throughput: >= 1,000,000 events per second
- Zero-copy IPC for payloads larger than 4 KB
- Deterministic replay must be bit-exact
- Plugin isolation with crash containment strategy
- Primary OS target: Linux (Ubuntu 22.04 LTS and 24.04 LTS)
- Portability target: macOS and Windows via abstraction layer

## Constraints

- Core language: C++20
- Bindings language: Python 3.11+
- Build system: CMake 3.25+
- API stack: gRPC 1.60+, Protocol Buffers v3
- Storage: SQLite embedded + optional TimescaleDB for distributed scale
- IPC: Eclipse iceoryx2 for zero-copy shared-memory communication

## Glossary

- **BoAt:** The simulation and testing platform defined by this project
- **Scenario:** Declarative simulation setup including plugins, signals, timing, and faults
- **Tick:** Discrete deterministic simulation time step
- **Plugin:** Dynamically loaded module implementing simulation behavior or integrations
- **Signal:** Typed data stream exchanged between components/plugins
- **Trace:** Persisted event timeline for analysis and replay
- **HIL:** Hardware-in-the-loop execution mode bridging simulation with physical hardware
- **SUT (System Under Test):** The target software or hardware component being validated

