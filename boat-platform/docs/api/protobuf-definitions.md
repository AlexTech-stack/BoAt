# Protobuf Definitions

## Package and File Layout

- Root path: `proto/boat/v1/`
- Package namespace: `boat.v1`
- 18 proto files defining 16 gRPC services:

| File | Service | RPCs |
|---|---|---|
| `simulation.proto` | SimulationService | 9 |
| `signal.proto` | SignalService | 3 |
| `scenario.proto` | ScenarioService | 5 |
| `replay.proto` | ReplayService | 8 |
| `plugin.proto` | PluginService | 4 |
| `metrics.proto` | MetricsService | 2 |
| `trace.proto` | TraceService | 4 |
| `fault.proto` | FaultService | 2 |
| `frame.proto` | FrameService | 3 |
| `can.proto` | CanService | 3 |
| `ethernet.proto` | EthernetService | 3 |
| `bus.proto` | BusService | 2 |
| `pdu.proto` | PduService | 10 |
| `debug.proto` | DebugService | 2 |
| `can_tp.proto` | CanTpService | 6 |
| `node_plugin.proto` | NodePluginService | 3 |
| `common.proto` | — | Shared messages (PaginationRequest, UUID, etc.) |
| `control.proto` | — | Control messages (StartCommand, etc.) |

## Service-to-Method Map

### BusService (`bus.proto`)

- `Publish`
- `Subscribe` (server streaming)

### CanService (`can.proto`)

- `SendCanFrame` — returns `CanBusInfo` per interface (driver, state, FD support, bitrate)

```protobuf
message CanBusInfo {
  string iface      = 1;
  string driver     = 2;  // e.g. "peak_usb", "vcan"
  string state      = 3;  // "up", "down", "unknown"
  bool   fd_support = 4;
  uint32 bitrate    = 5;
}
```

### CanTpService (`can_tp.proto`)

- `Configure`
- `Send`
- `ListSessions`
- `RemoveSession`
- `Subscribe` (server streaming)
- `SubscribeErrors` (server streaming)

One instance is registered per interface, so the gateway resolves these to a
specific CanTp plugin via `FindService("can_tp:" + iface)`. Requests that omit
`iface` fall back to the only loaded instance, and fail with `FAILED_PRECONDITION`
when more than one is loaded.

### DebugService (`debug.proto`)

- `StreamEvents` (server streaming)
- `GetEffectiveConfig`

`GetEffectiveConfig` returns the configuration the gateway resolved from its
environment at startup, as a JSON document -- byte-identical to what
`BOAT_CONFIG_DUMP` writes. It carries no timestamp or hostname, so two
identically configured gateways return identical documents and the artifact
can be diffed. `boat config show` is the CLI front end.

### EthernetService (`ethernet.proto`)

- `SendFrame`
- `SubscribeFrames` (server streaming)
- `ListInterfaces`

### FaultService (`fault.proto`)

- `InjectFault`
- `ListFaults`

### FrameService (`frame.proto`)

- `SendFrame`
- `SubscribeFrames` (server streaming)
- `StreamFrames` (bidirectional streaming)

The unified send/subscribe path for every bus type (`can`, `canfd`, `eth`, `tcp`,
`pdu`). A `tcp` send returns `UNIMPLEMENTED` (TCP is driven through the TCP
plugin's connection API); a `pdu` send is dispatched to the `pdu_router` plugin.

### MetricsService (`metrics.proto`)

- `GetMetrics`
- `StreamMetrics` (server streaming)

### NodePluginService (`node_plugin.proto`)

- `ListNodePlugins`
- `GetNodePluginInfo`
- `UnloadNodePlugin`

Scoped to the always-on node `PluginManager`, as opposed to `PluginService`,
which addresses the simulation-scoped one.

### PduService (`pdu.proto`)

- `SendPdu`
- `SubscribePdus` (server streaming)
- `ConfigureRoute`
- `ListRoutes`
- `ConfigureContainer`
- `ConfigureGroup`
- `EnableGroup`
- `DisableGroup`
- `ListGroups`
- `RemoveRoute`

### PluginService (`plugin.proto`)

- `RegisterPlugin`
- `ListPlugins`
- `GetPluginInfo`
- `UnloadPlugin`

### ReplayService (`replay.proto`)

- `StartReplay` — returns `ReplayControlResponse.replay_id` (session key for subsequent calls)
- `SeekReplay`
- `StreamReplay` (server streaming)
- `PauseReplay`
- `ResumeReplay`
- `StopReplay`
- `ImportTraceData`
- `StartReplayFromEvents`

### ScenarioService (`scenario.proto`)

- `CreateScenario`
- `GetScenario`
- `ListScenarios`
- `ValidateScenario`
- `DeleteScenario`

### SignalService (`signal.proto`)

- `InjectSignal`
- `SubscribeSignals` (server streaming)
- `GetSignalHistory`

### SimulationService (`simulation.proto`)

- `CreateSimulation`
- `StartSimulation`
- `PauseSimulation`
- `StepSimulation`
- `ResetSimulation`
- `StopSimulation`
- `GetSimulationState`
- `WatchSimulation` (server streaming)
- `ListSimulations`

### TraceService (`trace.proto`)

- `GetTrace`
- `ListTraces`
- `StreamTrace` (server streaming)
- `MarkStep`

## Shared Message Patterns

- Common identifiers use UUID strings.
- Paging uses `page_size` and `page_token` (defined in `common.proto`).
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
