# IPC Architecture

## IPC Strategy Matrix

| Channel | Technology | Use Case | Latency Target |
|---|---|---|---|
| High-throughput data | Eclipse iceoryx2 (shared memory) | Signal/sensor data between plugins | < 1 us |
| Control plane | Unix Domain Sockets (UDS) | Lifecycle commands, config push | < 100 us |
| External API | gRPC over UDS (local) / TCP+TLS (remote) | CLI, dashboard, CI runners | < 5 ms |
| Event streaming | gRPC server-side streaming | Live trace streaming to clients | < 10 ms |
| Replay | Memory-mapped files (`mmap`) | Deterministic event replay | N/A |

## iceoryx2 Integration

- `ShmPublisher<T>` wraps `iox2::Publisher` for zero-copy publish on named topics.
- `ShmSubscriber<T>` wraps `iox2::Subscriber` with wait-set integration.
- Signal payloads larger than 4 KB use shared memory; smaller payloads use UDS ring buffers.
- Topic naming convention: `boat/<scenario_id>/<signal_name>`.

## Control Channel (UDS)

- Each simulation instance exposes a UDS socket at `/run/boat/<instance_id>.sock`.
- Protocol uses length-prefixed protobuf messages shared with gRPC models.
- Supported commands:
  - `START`
  - `PAUSE`
  - `STEP`
  - `RESET`
  - `STOP`
  - `INJECT_FAULT`
  - `QUERY_STATE`

