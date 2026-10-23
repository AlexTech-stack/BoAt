# Database Design

## Storage Backends

| Store | Backend | Use Case |
|---|---|---|
| Event Store | SQLite (embedded) | Queryable event history |
| Trace Store | Binary flat files + SQLite index | High-throughput raw traces |
| Config Store | SQLite + TOML files | Scenario and plugin configuration |
| Metrics Store | `MetricsService`: on-demand snapshot + 1 Hz stream | Live metrics |

Earlier revisions of this table listed TimescaleDB (distributed event store) and Prometheus
remote write (metrics). Neither was ever implemented — SQLite is the only event store, and
`MetricsService` computes points on demand from the event store and live counters rather
than keeping a store of its own.

## SQLite Schema (Event Store)

### `events` table

- `id` TEXT PRIMARY KEY (UUID)
- `simulation_id` TEXT NOT NULL
- `tick` INTEGER NOT NULL
- `wall_time_ns` INTEGER NOT NULL
- `signal_id` TEXT NOT NULL
- `value_type` INTEGER NOT NULL
- `value_blob` BLOB NOT NULL
- `tags` TEXT (JSON)
- Indexes:
  - `(simulation_id, tick)`
  - `(signal_id, tick)`
  - `(wall_time_ns)`

### `simulations` table

- `id` TEXT PRIMARY KEY
- `scenario_id` TEXT NOT NULL
- `state` INTEGER NOT NULL
- `created_at_ns` INTEGER NOT NULL
- `config_json` TEXT NOT NULL

### `scenarios` table

- `id` TEXT PRIMARY KEY
- `name` TEXT NOT NULL
- `version` TEXT NOT NULL
- `definition_json` TEXT NOT NULL
- `created_at_ns` INTEGER NOT NULL

### `traces` table

- `id` TEXT PRIMARY KEY
- `simulation_id` TEXT NOT NULL
- `start_tick` INTEGER NOT NULL
- `end_tick` INTEGER NOT NULL
- `format` INTEGER NOT NULL
- `storage_path` TEXT NOT NULL
- `size_bytes` INTEGER NOT NULL

## Performance Considerations

- WAL mode with `synchronous=NORMAL` lets readers proceed while the event writer runs.
- `AsyncEventStore` owns the writer: `InsertBatch` enqueues, and one thread commits
  transactions of up to 256 records (configurable). The default queue is 16,384 records;
  `BOAT_EVENT_STORE_QUEUE_CAPACITY` overrides it (max 1,000,000) and is recorded in the
  effective-config artifact.
- When the queue is full the **oldest** record is dropped, not the newest: blocking a tick
  deadline is worse than losing a stored observation, and the wire path has already delivered
  the frame by that point. Drops and write failures are counted (`event_store.dropped`,
  `event_store.write_errors`) and logged at the first and every 1000th occurrence.
- `Flush()` drains the queue; the destructor calls it, so clean shutdown does not truncate
  accepted records. `Query()` flushes first, preserving read-your-writes.
- Replay reads traces via memory-mapped I/O.
- An event-store record is observability, not the sole copy of a trace: a consumer that needs
  lossless capture should record a trace on the gateway.

