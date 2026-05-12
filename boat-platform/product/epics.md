# Product Epics

| Epic | Description | Priority |
|---|---|---|
| E1: Simulation Core | Tick scheduler, signal router, state machine | P0 |
| E2: Plugin SDK | C++ SDK, Python bindings, example plugins | P0 |
| E3: API Gateway | gRPC server, all service implementations | P0 |
| E4: CLI Tool | `boat sim`, `boat scenario`, `boat replay`, `boat plugin` commands | P1 |
| E5: Observability | Event store, trace store, metrics, live streaming | P1 |
| E6: Replay Engine | Deterministic replay, seek, speed control | P1 |
| E7: HIL Bridge | HAL, SocketCAN, virtual stubs | P2 |
| E8: Web Dashboard | Live signal viewer, scenario manager, trace browser | P2 |
| E9: AI Features | Scenario generation, anomaly detection | P3 |
| E10: Distributed Sim | Multi-node coordination, HLA bridge | P3 |

## Epic to Milestone Mapping

- **M1 Core Sim Engine:** E1
- **M2 API Gateway:** E3
- **M3 Plugin SDK:** E2
- **M4 Observability:** E5, E6
- **M5 HIL:** E7
- **M6 GA:** E4, E8, E9, E10 hardening and release packaging

