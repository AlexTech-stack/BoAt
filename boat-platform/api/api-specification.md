# API Specification

## gRPC Services

All protobuf service files are defined under `proto/boat/v1/`.

### `simulation.proto` - `SimulationService`

| Method | Type | Description |
|---|---|---|
| `CreateSimulation` | Unary | Create instance from scenario |
| `StartSimulation` | Unary | Transition to RUNNING |
| `PauseSimulation` | Unary | Transition to PAUSED |
| `StepSimulation` | Unary | Advance N ticks (PAUSED only) |
| `ResetSimulation` | Unary | Reset to tick 0 |
| `StopSimulation` | Unary | Terminate instance |
| `GetSimulationState` | Unary | Query current state |
| `WatchSimulation` | Server-streaming | Live state change events |
| `ListSimulations` | Unary | Paginated list |

### `signal.proto` - `SignalService`

| Method | Type | Description |
|---|---|---|
| `InjectSignal` | Unary | Override signal value at next tick |
| `SubscribeSignals` | Server-streaming | Stream signal values by filter |
| `GetSignalHistory` | Unary | Query historical values (paginated) |

### `scenario.proto` - `ScenarioService`

| Method | Type | Description |
|---|---|---|
| `CreateScenario` | Unary | Register new scenario |
| `GetScenario` | Unary | Fetch scenario definition |
| `ListScenarios` | Unary | Paginated list |
| `ValidateScenario` | Unary | Dry-run validation |
| `DeleteScenario` | Unary | Remove scenario |

### `replay.proto` - `ReplayService`

| Method | Type | Description |
|---|---|---|
| `StartReplay` | Unary | Begin deterministic replay from trace |
| `SeekReplay` | Unary | Jump to tick N |
| `StreamReplay` | Server-streaming | Stream replayed events |

### `plugin.proto` - `PluginService`

| Method | Type | Description |
|---|---|---|
| `RegisterPlugin` | Unary | Register plugin .so |
| `ListPlugins` | Unary | List available plugins |
| `GetPluginInfo` | Unary | Fetch plugin metadata |
| `UnloadPlugin` | Unary | Hot-unload plugin |

### `metrics.proto` - `MetricsService`

| Method | Type | Description |
|---|---|---|
| `GetMetrics` | Unary | Snapshot of current metrics |
| `StreamMetrics` | Server-streaming | Live metrics stream |

### `trace.proto` - `TraceService`

| Method | Type | Description |
|---|---|---|
| `GetTrace` | Unary | Fetch trace events by id |
| `ListTraces` | Unary | Paginated trace listing |
| `StreamTrace` | Server-streaming | Live trace stream |

### `fault.proto` - `FaultService`

| Method | Type | Description |
|---|---|---|
| `InjectFault` | Unary | Schedule fault injection for simulation |
| `ListFaults` | Unary | Paginated fault event listing |

## API Versioning Strategy

- Package versions follow `boat.v1`, `boat.v2`, where breaking changes increment major.
- Non-breaking additions (new fields or RPCs) stay in the same major package.
- Deprecated fields are marked with `[deprecated = true]` and removed after two major versions.
- Every request carries header `x-boat-api-version`.

## Error Handling Model

- Core gRPC status codes:
  - `NOT_FOUND`
  - `INVALID_ARGUMENT`
  - `FAILED_PRECONDITION`
  - `INTERNAL`
  - `UNAVAILABLE`
- Error payloads use `google.rpc.Status`.
- `details` includes `ErrorInfo` with `domain`, `reason`, and metadata.
- Invalid state transition operations return `FAILED_PRECONDITION` and include current state metadata.

