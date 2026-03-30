# System Architecture

## Layered Architecture

```text
┌─────────────────────────────────────────────────────────────────┐
│                        CLIENT LAYER                             │
│   CLI Tool (boat-cli)  │  Web Dashboard  │  External Tools      │
│   Python SDK           │  CI/CD Runners  │  IDE Plugins         │
└────────────────────────┬────────────────────────────────────────┘
                         │ gRPC (HTTP/2 + TLS)
┌────────────────────────▼────────────────────────────────────────┐
│                     API GATEWAY LAYER                           │
│   BoAt gRPC Server  │  Auth/AuthZ  │  Rate Limiting             │
│   REST Transcoding (grpc-gateway)  │  WebSocket bridge          │
└────────────────────────┬────────────────────────────────────────┘
                         │ Unix Domain Sockets (control)
┌────────────────────────▼────────────────────────────────────────┐
│                    SERVICE LAYER                                 │
│  ScenarioService │ SignalService │ TraceService │ PluginService  │
│  ReplayService   │ FaultService │ MetricsService│ SimulationService │
└────────────────────────┬────────────────────────────────────────┘
                         │ Shared Memory (iceoryx2) + Event Bus
┌────────────────────────▼────────────────────────────────────────┐
│                  SIMULATION CORE (C++)                          │
│  Scheduler (tick-based) │ Signal Router │ Plugin Manager        │
│  Event Bus              │ State Machine │ Determinism Engine     │
│  Time Manager           │ Fault Injector│ Scenario Loader        │
└────────────────────────┬────────────────────────────────────────┘
                         │ Plugin ABI (C stable ABI)
┌────────────────────────▼────────────────────────────────────────┐
│                    PLUGIN LAYER                                  │
│  Vehicle Dynamics Plugin │ Sensor Model Plugin │ Network Plugin  │
│  AUTOSAR Plugin          │ CAN/LIN/Ethernet Plugin │ Custom...   │
└────────────────────────┬────────────────────────────────────────┘
                         │ HAL (Hardware Abstraction Layer)
┌────────────────────────▼────────────────────────────────────────┐
│               HARDWARE ABSTRACTION LAYER                        │
│  HIL Bridge  │  CAN Interface  │  Ethernet Interface            │
│  GPIO/PWM    │  FPGA Bridge    │  Virtual Hardware Stubs        │
└─────────────────────────────────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────────┐
│                   PERSISTENCE LAYER                             │
│  Event Store (SQLite/TimescaleDB) │ Config Store (TOML/SQLite)  │
│  Trace Store (binary + index)     │ Artifact Registry            │
└─────────────────────────────────────────────────────────────────┘
```

## Component Responsibilities

| Component | Language | Responsibility |
|---|---|---|
| `boat-core` | C++20 | Tick scheduler, signal router, determinism engine |
| `boat-plugin-sdk` | C++20 + C ABI | Plugin interface, lifecycle hooks |
| `boat-gateway` | C++20 / Go | gRPC server, auth, transcoding |
| `boat-agent` | C++20 | Unix socket control daemon per simulation instance |
| `boat-store` | C++20 | Event/trace persistence, query engine |
| `boat-replay` | C++20 | Deterministic replay engine |
| `boat-hil` | C++20 | HIL bridge, hardware driver abstraction |
| `boat-py` | Python 3.11+ | Python SDK, gRPC stubs, test helpers |
| `boat-cli` | Python / Rust | Command-line interface |
| `boat-dashboard` | TypeScript/React | Web observability dashboard |
| `boat-ai` | Python | LLM-assisted test generation, anomaly detection |

