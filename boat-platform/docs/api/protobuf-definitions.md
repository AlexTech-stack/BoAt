# Protobuf Definitions

## Package and File Layout

- Root path: `proto/boat/v1/`
- Package namespace: `boat.v1`
<!-- BEGIN GENERATED: proto-summary -->
- 16 proto files defining 14 gRPC services, 65 RPCs in total:

| File | Service | RPCs |
|---|---|---|
| `bus.proto` | BusService | 2 |
| `can_tp.proto` | CanTpService | 6 |
| `common.proto` | — | — |
| `control.proto` | — | — |
| `debug.proto` | DebugService | 2 |
| `fault.proto` | FaultService | 2 |
| `frame.proto` | FrameService | 4 |
| `metrics.proto` | MetricsService | 2 |
| `node_plugin.proto` | NodePluginService | 3 |
| `pdu.proto` | PduService | 10 |
| `plugin.proto` | PluginService | 4 |
| `replay.proto` | ReplayService | 9 |
| `scenario.proto` | ScenarioService | 5 |
| `signal.proto` | SignalService | 3 |
| `simulation.proto` | SimulationService | 9 |
| `trace.proto` | TraceService | 4 |
<!-- END GENERATED: proto-summary -->

> `common.proto` holds shared messages (`PaginationRequest`, `UUID`, …);
> `control.proto` holds the UDS control messages (`StartCommand`, …).
>
> `can.proto` / `CanService` and `ethernet.proto` / `EthernetService` were
> **deleted**. `FrameService` carries their send, subscribe and
> interface-listing RPCs for every bus type.

## Service-to-Method Map

### BusService (`bus.proto`)

<!-- BEGIN GENERATED: rpcs-BusService -->
- `Publish`
- `Subscribe` (server streaming)
<!-- END GENERATED: rpcs-BusService -->

### CanTpService (`can_tp.proto`)

<!-- BEGIN GENERATED: rpcs-CanTpService -->
- `Configure`
- `Send`
- `ListSessions`
- `RemoveSession`
- `Subscribe` (server streaming)
- `SubscribeErrors` (server streaming)
<!-- END GENERATED: rpcs-CanTpService -->

One instance is registered per interface, so the gateway resolves these to a
specific CanTp plugin via `FindService("can_tp:" + iface)`. Requests that omit
`iface` fall back to the only loaded instance, and fail with `FAILED_PRECONDITION`
when more than one is loaded.

### DebugService (`debug.proto`)

<!-- BEGIN GENERATED: rpcs-DebugService -->
- `StreamEvents` (server streaming)
- `GetEffectiveConfig`
<!-- END GENERATED: rpcs-DebugService -->

`GetEffectiveConfig` returns the configuration the gateway resolved from its
environment at startup, as a JSON document -- byte-identical to what
`BOAT_CONFIG_DUMP` writes. It carries no timestamp or hostname, so two
identically configured gateways return identical documents and the artifact
can be diffed. `boat config show` is the CLI front end.

### FaultService (`fault.proto`)

<!-- BEGIN GENERATED: rpcs-FaultService -->
- `InjectFault`
- `ListFaults`
<!-- END GENERATED: rpcs-FaultService -->

### FrameService (`frame.proto`)

<!-- BEGIN GENERATED: rpcs-FrameService -->
- `SendFrame`
- `SubscribeFrames` (server streaming)
- `ListInterfaces`
- `StreamFrames` (bidirectional streaming)
<!-- END GENERATED: rpcs-FrameService -->

The unified send/subscribe path for every bus type (`can`, `canfd`, `eth`, `tcp`,
`pdu`). A `tcp` send returns `UNIMPLEMENTED` (TCP is driven through the TCP
plugin's connection API); a `pdu` send is dispatched to the `pdu_router` plugin.

`ListInterfaces` supersedes the deleted `CanService.ListBuses` and
`EthernetService.ListInterfaces`, returning one repeated `InterfaceInfo` across
both registries. Read `bus_type` before the rest: an empty `driver` on an
`ETHERNET` entry means "not applicable", not "unknown", since the Ethernet
registry holds no equivalent metadata.

```protobuf
message InterfaceInfo {
  string        iface      = 1;  // "can0", "vcan0", "eth0"
  Frame.BusType bus_type   = 2;  // CAN or ETHERNET
  string        driver     = 3;  // CAN only: e.g. "peak_usb", "vcan"
  string        state      = 4;  // CAN only: "up", "down", "unknown"
  bool          fd_support = 5;  // CAN only: CAN FD capable (mtu >= 72)
  uint32        bitrate    = 6;  // CAN only: nominal bit/s, 0 if unknown
}
```

### MetricsService (`metrics.proto`)

<!-- BEGIN GENERATED: rpcs-MetricsService -->
- `GetMetrics`
- `StreamMetrics` (server streaming)
<!-- END GENERATED: rpcs-MetricsService -->

### NodePluginService (`node_plugin.proto`)

<!-- BEGIN GENERATED: rpcs-NodePluginService -->
- `ListNodePlugins`
- `GetNodePluginInfo`
- `UnloadNodePlugin`
<!-- END GENERATED: rpcs-NodePluginService -->

Scoped to the always-on node `PluginManager`, as opposed to `PluginService`,
which addresses the simulation-scoped one.

### PduService (`pdu.proto`)

<!-- BEGIN GENERATED: rpcs-PduService -->
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
<!-- END GENERATED: rpcs-PduService -->

### PluginService (`plugin.proto`)

<!-- BEGIN GENERATED: rpcs-PluginService -->
- `RegisterPlugin`
- `ListPlugins`
- `GetPluginInfo`
- `UnloadPlugin`
<!-- END GENERATED: rpcs-PluginService -->

### ReplayService (`replay.proto`)

<!-- BEGIN GENERATED: rpcs-ReplayService -->
- `StartReplay`
- `SeekReplay`
- `StreamReplay` (server streaming)
- `PauseReplay`
- `ResumeReplay`
- `StopReplay`
- `ImportTraceData`
- `ImportTraceDataStream` (client streaming)
- `StartReplayFromEvents`
<!-- END GENERATED: rpcs-ReplayService -->

`StartReplay` returns `ReplayControlResponse.replay_id` — the session key for
subsequent calls.

### ScenarioService (`scenario.proto`)

<!-- BEGIN GENERATED: rpcs-ScenarioService -->
- `CreateScenario`
- `GetScenario`
- `ListScenarios`
- `ValidateScenario`
- `DeleteScenario`
<!-- END GENERATED: rpcs-ScenarioService -->

### SignalService (`signal.proto`)

<!-- BEGIN GENERATED: rpcs-SignalService -->
- `InjectSignal`
- `SubscribeSignals` (server streaming)
- `GetSignalHistory`
<!-- END GENERATED: rpcs-SignalService -->

### SimulationService (`simulation.proto`)

<!-- BEGIN GENERATED: rpcs-SimulationService -->
- `CreateSimulation`
- `StartSimulation`
- `PauseSimulation`
- `StepSimulation`
- `ResetSimulation`
- `StopSimulation`
- `GetSimulationState`
- `WatchSimulation` (server streaming)
- `ListSimulations`
<!-- END GENERATED: rpcs-SimulationService -->

### TraceService (`trace.proto`)

<!-- BEGIN GENERATED: rpcs-TraceService -->
- `GetTrace`
- `ListTraces`
- `StreamTrace` (server streaming)
- `MarkStep`
<!-- END GENERATED: rpcs-TraceService -->

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
