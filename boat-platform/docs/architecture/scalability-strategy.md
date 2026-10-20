# Scalability Strategy

## Execution Model

- One `TickAuthority` thread owns the clock. Each tick it runs the registered
  phases in order — `node_plugins`, `sim_plugins`, `replay` — each to
  completion. Phase order, not thread wake-up order, is what makes tick
  attribution reproducible across runs.
- There is no worker pool and no per-core fan-out. `TickScheduler` owns no
  thread and no clock; the gateway calls `TickIfRunning()` as one phase, and
  `boat sim step` advances ticks synchronously on the caller's thread.
- Plugins are `dlopen()`ed into the gateway process and invoked on the tick
  thread. They are not pinned to CPUs, and there is no separate-process
  sandbox: a plugin crash kills the gateway, and `crash_attribution` only
  reports which `.so` caused it.
- Work outside the tick domain is threaded: one RX thread per CAN interface
  (`HilBridge`) and per Ethernet interface, one `SignalRouter` dispatcher
  thread, gRPC handler threads, and one 1 ms polling thread per shared-memory
  subscriber. These feed the tick domain; they do not define tick order.

## Shared Memory Strategy

- `src/ipc/` uses iceoryx2 `ServiceType::Ipc` publish-subscribe services, one
  service name per topic.
- `IpcChannelSelector` keeps control payloads up to 4 KiB
  (`kShmThresholdBytes`) on the UDS channel and moves larger ones to shared
  memory.
- `ShmPublisher` uses `send_copy()`; `ShmSubscriber` polls every 1 ms on its own
  thread and invokes the callback. BoAt does not loan chunks directly, and it
  defines no fixed payload size classes — iceoryx2 owns the segment layout.
- Topics are scoped per gateway instance by an id derived from the bound UDS
  socket path.

## Storage and Backpressure

- The event store is SQLite (WAL, `synchronous=NORMAL`). `InsertBatch` writes
  in transactions of up to 1000 rows, synchronously on the caller's thread
  ([#23](https://github.com/AlexTech-stack/BoAt/issues/23)).
- The trace store writes a flat binary file with plain `write()` and reads it
  with `mmap`; there is no `O_DIRECT` path.
- There is no simulation-loop pool allocator. Capacity is bounded by queues
  instead: gRPC stream queues and `EventBus` (4096 entries) drop the oldest,
  `SignalRouter`'s per-subscription SPSC ring (4096) drops the newest, and all
  drops are counted and exposed as metrics. A consumer that needs lossless
  capture should record a trace on the gateway rather than rely on a client
  stream.
