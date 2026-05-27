# Module Structure

## CMake Module Tree

```text
boat-platform/
├── CMakeLists.txt                  # Root: find_package, add_subdirectory
├── cmake/
│   ├── BoAtPlugin.cmake            # add_boat_plugin() macro
│   ├── BoAtProto.cmake             # protobuf_generate() wrapper
│   └── Packaging.cmake             # CPack config
├── core/
│   ├── scheduler/                  # TickScheduler, SimClock
│   ├── signal/                     # SignalRouter, SignalBus
│   ├── event/                      # EventBus, EventQueue
│   ├── plugin/                     # PluginManager, PluginLoader
│   ├── state/                      # SimStateMachine
│   ├── determinism/                # DeterminismEngine, RNG seeding
│   ├── fault/                      # FaultInjector
│   └── scenario/                   # ScenarioLoader (YAML/JSON)
├── ipc/
│   ├── shm/                        # iceoryx2 wrapper, ShmPublisher/Subscriber
│   ├── uds/                        # Unix domain socket control channel
│   └── grpc/                       # gRPC server, service impls
├── store/
│   ├── event_store/                # SQLite-backed event persistence
│   ├── trace_store/                # Binary trace writer/reader
│   └── config_store/               # TOML config loader/validator
├── replay/
│   └── replay_engine/              # ReplayController, TimestampIndex
├── hil/
│   ├── hal/                        # HAL interface definitions
│   ├── can/                        # SocketCAN driver
│   └── virtual/                    # Virtual hardware stubs
├── plugins/
│   ├── vehicle_dynamics/           # Reference vehicle dynamics plugin
│   ├── sensor_model/               # Lidar/Camera/Radar stubs
│   └── network_sim/                # CAN/LIN/Ethernet simulation
├── sdk/
│   ├── cpp/
│   │   ├── CMakeLists.txt          # INTERFACE target: boat_plugin_sdk
│   │   └── include/boat/plugin.h   # Stable C ABI for plugins
│   └── python/                     # boat-py package (ScenarioBuilder + gRPC stubs + pytest fixtures)
├── gateway/
│   └── grpc_gateway/               # gRPC server entry point
├── cli/
│   └── boat_cli/                   # CLI commands
├── dashboard/
│   └── web/                        # React dashboard
├── ai/
│   └── boat_ai/                    # LLM integration, anomaly detection
├── proto/
│   └── boat/v1/                    # All .proto files
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── determinism/
│   └── hil/
└── docs/
    └── (symlinks to /boat-platform/*.md)
```

## Plugin Loading Model

Each plugin is built as a shared library (`.so`) and loaded at runtime via `dlopen`.  
The plugin entry point uses a stable C ABI:

- `boat_plugin_create()`

This preserves binary compatibility boundaries while allowing internal C++ evolution.

