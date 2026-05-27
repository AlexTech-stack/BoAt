# Risk Analysis

## Risk Register

| ID | Risk | Category | Probability | Impact | Mitigation |
|---|---|---|---|---|---|
| R01 | iceoryx2 API instability (Rust-based, evolving) | Technical | Medium | High | Pin specific release and maintain a wrapper layer |
| R02 | Plugin ABI breakage across versions | Technical | High | High | Stable C ABI, ABI version checks at load time, semver policy |
| R03 | Determinism broken by floating-point differences across compilers | Technical | Medium | High | Determinism compile policy (`-ffloat-store` or fixed-point) plus CI determinism checks |
| R04 | gRPC streaming backpressure under high event rate | Technical | Medium | Medium | Flow control tuning, client buffering, explicit drop policy |
| R05 | SQLite write throughput insufficient at 1M events/sec | Technical | High | High | Async batch writer, WAL mode, optional TimescaleDB fallback |
| R06 | Plugin crash corrupts simulation state | Technical | Medium | High | Optional process-isolated plugin mode, watchdog, pre-load snapshots |
| R07 | Open-source community adoption slower than expected | Organizational | Medium | Medium | Strong docs, examples, tutorials, and outreach |
| R08 | Key engineer departure | Organizational | Low | High | Knowledge sharing, ADRs, pair programming |
| R09 | HIL hardware availability constraints for CI | Organizational | High | Medium | Virtual CAN in CI, hardware-lab gated jobs, optional HIL path |
| R10 | LLM API cost overrun | Technical | Low | Medium | Local inference default, strict rate limiting, cost dashboard |

## Architecture and Process Alignment

- R01 mitigation is implemented by the wrapper strategy in `architecture/ipc-architecture.md`.
- R02 mitigation is implemented by C ABI and `boat_plugin_create()` in `architecture/module-structure.md`.
- R03 mitigation is enforced by determinism CI in `devops/ci-cd.md` and validation in `testing/test-strategy.md`.
- R04 mitigation is covered by streaming design and service boundaries in `api/api-specification.md`.
- R05 mitigation is reflected in async writer and backend strategy in `database/database-design.md`.
- R06 mitigation aligns with optional strict plugin sandboxing in `architecture/scalability-strategy.md`.
- R09 mitigation aligns with `vcan0` and lab-gated HIL testing in `testing/test-strategy.md`.
- R10 mitigation is fully defined in `ai/llm-cost-control.md`.

