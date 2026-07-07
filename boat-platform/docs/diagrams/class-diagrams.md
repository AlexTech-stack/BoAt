# Class Diagrams

## Plugin Hierarchy (ABI v8)

```mermaid
classDiagram
    class IPlugin {
      +initialize(config_json)
      +on_tick(tick)
      +on_frame(BoatFrame)
      +set_frame_publisher(fn)
      +set_pdu_publisher(fn)
      +declared_buses() bus_types
      +shutdown()
    }
    class PduRouterPlugin
    class FrameForwarderPlugin
    class CanIoPlugin
    class CanTpPlugin
    class TcpPlugin
    class SomeIpPlugin
    class VehicleDynamicsPlugin
    class SensorModelPlugin
    class NetworkSimPlugin
    class CanResponderPlugin

    IPlugin <|-- PduRouterPlugin
    IPlugin <|-- FrameForwarderPlugin
    IPlugin <|-- CanIoPlugin
    IPlugin <|-- CanTpPlugin
    IPlugin <|-- TcpPlugin
    IPlugin <|-- SomeIpPlugin
    IPlugin <|-- VehicleDynamicsPlugin
    IPlugin <|-- SensorModelPlugin
    IPlugin <|-- NetworkSimPlugin
    IPlugin <|-- CanResponderPlugin
```

`IPlugin` in this diagram maps to the C ABI dispatch table `BoatPluginVTable`
(9 fields) defined in `sdk/cpp/include/boat/plugin.h`. `BOAT_PLUGIN_ABI_VERSION`
is **8**; a plugin reporting an older version is rejected at `dlopen`.
Implementations expose `boat_plugin_create`, `boat_plugin_destroy`, and
`boat_plugin_abi_version` entry points and route lifecycle calls through that
vtable. All plugins are loaded at runtime via `dlopen` — including the built-in
ones (as of v8, `PduRouter` is a plugin, not part of the core gateway).

Key v8 methods:
- `on_frame(BoatFrame)` — the plugin receives every frame dispatched by
  `PluginManager::DispatchFrame()` (all bus types).
- `set_frame_publisher(fn)` — the gateway hands the plugin a callback it uses to
  publish outbound frames back onto the bus.
- `declared_buses()` — the set of `bus_type`s a plugin handles
  (e.g. `FrameForwarderPlugin` declares `["can","canfd","eth","pdu","tcp"]`).

## Unified Frame Type

```mermaid
classDiagram
    class BoatFrame {
      +bus_type
      +iface
      +timestamp_ns
      +payload
      +meta
    }
    class FrameMeta {
      <<union by bus_type>>
    }
    class CanMeta {
      +can_id
      +dlc
      +flags
    }
    class EthMeta {
      +dst_mac
      +src_mac
      +ethertype
      +src_ip
      +dst_ip
      +ip_version
      +flags
    }

    BoatFrame o-- FrameMeta
    FrameMeta <|-- CanMeta
    FrameMeta <|-- EthMeta
```

`BoatFrame` (`sdk/cpp/include/boat/frame.h`) is the single ABI frame type for all
bus types; the pre-v8 `BoatCanFrame` / `BoatEthFrame` types were removed. Its
`bus_type` discriminator is one of `CAN | CANFD | ETH | PDU | TCP`, and `meta`
holds bus-specific fields (CAN: `can_id`/`dlc`/`flags`; Ethernet:
`dst_mac`/`src_mac`/`ethertype`/`src_ip`/`dst_ip`/`ip_version`/`flags`). Its
internal counterpart is `core::Frame` (`src/core/`), which crosses the ABI
boundary via `core::Frame::ToAbi()`, and its wire/trace representation is the
`boat.v1.Frame` protobuf. The self-sent flags (`BOAT_CAN_FLAG_SELF_SENT = 0x08`,
`BOAT_ETH_FLAG_SELF_SENT = 0x01`) let `FrameForwarderPlugin` and `can_io` skip
locally-generated frames and avoid dispatch loops.

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

