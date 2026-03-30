# Class Diagrams

## Plugin Hierarchy

```mermaid
classDiagram
    class IPlugin {
      +Initialize(config)
      +OnTick(tick)
      +Shutdown()
    }
    class VehicleDynamicsPlugin
    class SensorModelPlugin
    class NetworkSimPlugin

    IPlugin <|-- VehicleDynamicsPlugin
    IPlugin <|-- SensorModelPlugin
    IPlugin <|-- NetworkSimPlugin
```

`IPlugin` in this diagram maps to the C ABI dispatch table `BoatPluginVTable`
defined in `sdk/cpp/include/boat/plugin.h`. Implementations expose
`boat_plugin_create`, `boat_plugin_destroy`, and `boat_plugin_abi_version`
entry points and route lifecycle calls through that vtable.

## Signal Router Hierarchy

```mermaid
classDiagram
    class ISignalRouter {
      +Route(event)
      +Subscribe(filter)
    }
    class LocalSignalRouter
    class DistributedSignalRouter

    ISignalRouter <|-- LocalSignalRouter
    ISignalRouter <|-- DistributedSignalRouter
```

## Event Store Hierarchy

```mermaid
classDiagram
    class IEventStore {
      +InsertBatch(events)
      +Query(filter)
    }
    class SqliteEventStore
    class TimescaleEventStore

    IEventStore <|-- SqliteEventStore
    IEventStore <|-- TimescaleEventStore
```

## HAL Driver Hierarchy

```mermaid
classDiagram
    class IHalDriver {
      +Open()
      +ReadFrame()
      +WriteFrame()
      +Close()
    }
    class SocketCanDriver
    class VirtualCanDriver

    IHalDriver <|-- SocketCanDriver
    IHalDriver <|-- VirtualCanDriver
```

`HilBridge` owns a `shared_ptr<IHalDriver>` and keeps a reference to `EventBus`.
CAN frame events use dedicated discriminators: RX `kEventTypeCanFrameRx = 0xCA1F0001`
and TX `kEventTypeCanFrameTx = 0xCA1F0002`.

