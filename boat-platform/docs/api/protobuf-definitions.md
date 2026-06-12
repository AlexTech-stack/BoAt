# Protobuf Definitions

## Package and File Layout

- Root path: `proto/boat/v1/`
- Package namespace: `boat.v1`
- Canonical source-of-truth files:
  - `simulation.proto`
  - `signal.proto`
  - `scenario.proto`
  - `replay.proto`
  - `plugin.proto`
  - `metrics.proto`
  - `trace.proto`
  - `fault.proto`
  - `common.proto`

## Service-to-Method Map

### `SimulationService` (`simulation.proto`)

- `CreateSimulation`
- `StartSimulation`
- `PauseSimulation`
- `StepSimulation`
- `ResetSimulation`
- `StopSimulation`
- `GetSimulationState`
- `WatchSimulation` (server streaming)
- `ListSimulations`

### `SignalService` (`signal.proto`)

- `InjectSignal`
- `SubscribeSignals` (server streaming)
- `GetSignalHistory`

### `ScenarioService` (`scenario.proto`)

- `CreateScenario`
- `GetScenario`
- `ListScenarios`
- `ValidateScenario`
- `DeleteScenario`

### `ReplayService` (`replay.proto`)

- `StartReplay`
- `SeekReplay`
- `StreamReplay` (server streaming)
- `ReplayControlResponse.replay_id` is returned by `StartReplay` and used as the required session key for `SeekReplay` and `StreamReplay`.

### `PluginService` (`plugin.proto`)

- `RegisterPlugin`
- `ListPlugins`
- `GetPluginInfo`
- `UnloadPlugin`

### `MetricsService` (`metrics.proto`)

- `GetMetrics`
- `StreamMetrics` (server streaming)

### `TraceService` (`trace.proto`)

- `GetTrace`
- `ListTraces`
- `StreamTrace` (server streaming)

### `FaultService` (`fault.proto`)

- `InjectFault`
- `ListFaults`

### `CanService` (`can.proto`)

- `SendCanFrame`
- `SubscribeCanFrames` (server streaming)
- `ListBuses` — returns `CanBusInfo` per interface (driver, state, FD support, bitrate)

```protobuf
message CanBusInfo {
  string iface      = 1;
  string driver     = 2;  // e.g. "peak_usb", "vcan"
  string state      = 3;  // "up", "down", "unknown"
  bool   fd_support = 4;
  uint32 bitrate    = 5;
}
```

### `EthernetService` (`ethernet.proto`)

- `SendFrame`
- `SubscribeFrames` (server streaming)
- `ListInterfaces`

## Shared Message Patterns

- Common identifiers use UUID strings.
- Paging uses `page_size` and `page_token`.
- State enums include `IDLE`, `RUNNING`, `PAUSED`, `STOPPED`, `ERROR`.
- Event payload values are represented with `oneof`.
- Streaming messages include tick and wall-time references for ordering.

## Versioning and Compatibility

- Keep all backward-compatible changes inside `boat.v1`.
- Introduce `boat.v2` for breaking wire changes.
- Mark fields with `[deprecated = true]` before removal.
- Maintain compatibility window across two major versions.

## Request Metadata

- Clients include API version in `x-boat-api-version`.
- Local calls can use gRPC over UDS.
- Remote calls use gRPC over TCP with TLS.

## Contract Generation

Proto contracts under `proto/boat/v1/` are the API source of truth.

```bash
protoc -I proto \
  --cpp_out=generated/cpp \
  --grpc_out=generated/cpp \
  --plugin=protoc-gen-grpc="$(which grpc_cpp_plugin)" \
  proto/boat/v1/*.proto
```

