# Trace Editor — How To

`tools/trace_editor.py` is a standalone tool for viewing, filtering, and editing the frames
inside a gateway binary trace file (the format produced by `boat replay import` /
`TraceReplayer.convert_to_binary()`). It needs no running gateway to load, edit, or save a
trace — a gateway is only needed for the optional "Push to Gateway" action.

```
python3 tools/trace_editor.py
# open http://localhost:8089  (port via BOAT_TRACE_EDITOR_PORT)
```

## Loading a trace

The file dropdown auto-populates from three places, no Browse needed for the common case:

- `/tmp` — where the gateway actually writes imported traces (`ImportTraceData`'s hardcoded
  storage path is `/tmp/<trace_id>.trace`), so anything already imported shows up here.
- `traces/` — this tool's own default save location.
- your home directory.

Pick a file and click **Load**, or **Browse...** to type an arbitrary path. **New** starts an
empty trace from scratch.

## The frame table

Columns: index, bus type, iface, timestamp (ns), a one-line summary (CAN ID/DLC, MAC/IP:port,
etc.), the payload as hex, and **Len** — always the *actual* current payload byte count,
recomputed from the hex string every time the table renders, including trailing zero bytes. It
never trusts a separately cached length field, so it can't drift from what's really there.

Use the filter bar (bus type, iface substring, CAN ID, timestamp range) to narrow down a large
trace. Filtering is client-side over the already-loaded frame list — no round-trip per filter
change.

Row actions: **Edit**, **Insert After** (clones the row as a starting point for the new frame),
and **Delete**. Checkboxes + **Delete Selected** for bulk removal.

## Saving and pushing to a gateway

- **Save** writes back to the currently loaded path; **Save As** picks a new one (relative paths
  land under `traces/`).
- **Push to Gateway** uploads the current in-memory frames straight to a running gateway via
  `ReplayService.ImportTraceData`, using the **Gateway** address field in the toolbar (remembered
  across sessions via a cookie, so you don't retype it every time). This is *not* the same as
  running `boat replay import` — that command only accepts source formats (`.asc`/`.blf`/`.pcap`)
  and always does its own client-side conversion, so it can't re-ingest a trace that's already in
  this tool's binary format. Push is the only way to get an edited trace back into a simulation.
  After pushing, actually play it with:
  ```
  boat replay start --trace <trace_id> --buses <ifaceA,ifaceB>
  ```

Both **Save** and **Push** report non-blocking warnings (shown as toasts) for anything that looks
like a mistake rather than blocking you outright — see below for what they check.

## Editing a frame

Click **Edit** (or **Insert After** / **+ Add Frame**) to open the frame form. Fields shown
depend on the selected **Bus Type**.

### Common fields

- **Iface** — what this actually does depends on bus type (see [Gotchas](#gotchas-and-shared-semantics) below); for CAN/CANFD it's essentially informational.
- **Timestamp (ns)** — the raw nanosecond value you edit is a big integer (real epoch timestamps
  are ~19 digits); the box underneath is a read-only, colored, dot-separated grouping of that same
  number (seconds · milliseconds · microseconds · nanoseconds, right to left) purely to make it
  easier to read at a glance. It does not change what gets saved.
- **Payload (hex)** — shared across all bus types. For CAN/CANFD and PDU this is the actual
  payload. For Ethernet it's *everything after the Ethernet header* — an IP packet, or a raw
  EtherCAT datagram (see below) — normally built for you rather than hand-edited.

### CAN / CANFD

- **DLC** auto-fills to match the Payload's byte length every time you edit Payload. **DLC means
  "how many bytes actually get sent" — it is not an ISO 11898-1 DLC code.** If you edit DLC by
  hand to something smaller than the payload, the frame gets truncated to that many bytes; a red
  warning appears immediately if DLC and payload length disagree, and the same check runs again
  server-side on Save/Push in case a mismatch was created outside the UI (e.g. a direct API call).
- For CAN FD, if the resulting length isn't already one of the 8 valid FD lengths
  (0-8/12/16/20/24/32/48/64 bytes), the gateway rounds it up and zero-pads automatically when the
  frame is actually sent — you never need to pre-pad it yourself in the editor.
- **Flags** is a bitmask: `0x01`=CANFD_BRS (bit-rate switch), `0x02`=CANFD_ESI (error state
  indicator), `0x04`=CANFD_FDF (FD frame format). Combine with bitwise OR — `0x05` is a typical FD
  frame with BRS. Leave at `0` for classic CAN.

### Ethernet

`Frame.payload` for Ethernet frames is everything after the Ethernet header — there's no separate
"header vs. data" split at the protocol level, which used to mean hand-building the entire
IPv4/IPv6 + UDP/ICMP header (or an EtherCAT datagram) yourself as raw hex just to send one message.
The editor now does that construction for you:

- **EtherType** and **VLAN ID** are plain L2 metadata, set independently of everything below.
  **EtherType is never auto-filled or overwritten** by the IP Version / L4 Protocol choice — set
  it yourself to match (`0x0800` for IPv4, `0x86DD` for IPv6, `0x88A4` for EtherCAT). Leaving it
  inconsistent with the actual packet inside builds a frame a real receiver can't parse correctly;
  the editor won't catch that mismatch for you.
- **L4 Protocol** controls the guided form: **None**, **UDP**, **ICMP** (both IP-based, see below),
  or **EtherCAT** (no IP at all — see its own section below).
  - **UDP** — Src Port, Dst Port, Application Data (hex). Builds a full IP+UDP packet with correct
    length and checksum. IP Version (IPv4/IPv6) and Src/Dst IP apply here.
  - **ICMP** — Type, Code, Identifier, Sequence, Application Data (hex). IPv4 echo request/reply =
    `8`/`0` and `0`/`0`; IPv6 = `128`/`0` and `129`/`0`. IP Version and Src/Dst IP apply here too.
  - **None** — the Payload field becomes a plain hex editor again, for anything the guided form
    doesn't cover. This is also where TCP payloads go: this codebase treats TCP as
    connection-oriented and sends it through a dedicated TCP plugin, not as raw frames, so a
    guided TCP builder here wouldn't be something you could actually replay live anyway — it's
    still fine to view/edit already-captured TCP bytes as raw hex, just not to construct new
    "live" TCP traffic this way.
- While a protocol is selected, the Payload field is a **read-only preview** of the exact bytes
  that will be sent, with checksum/length computed — switch L4 Protocol back to "None" to take
  over editing the raw bytes directly.
- **Opening an existing frame** auto-detects UDP/ICMP/EtherCAT from its actual payload bytes (the
  EtherCAT check only runs when EtherType is `0x88A4`) and pre-fills the guided fields; anything it
  can't recognize (TCP, a multi-datagram EtherCAT frame, or anything that isn't well-formed) is
  left in raw mode untouched — existing bytes are never silently reinterpreted.

#### EtherCAT

Selecting **EtherCAT** hides the IP Version/Src IP/Dst IP fields (EtherCAT has no IP layer — it
rides directly on the Ethernet header via EtherType `0x88A4`) and builds a single EtherCAT
datagram: the 2-byte EtherCAT frame header plus a 10-byte datagram header, your data, and a
2-byte Working Counter.

- **Command** — the 15 standard EtherCAT commands (NOP, APRD/APWR/APRW, FPRD/FPWR/FPRW, BRD/BWR/BRW,
  LRD/LWR/LRW, ARMW, FRMW).
- **Index (Idx)** — a free-form byte the master can use to correlate a response to its request; not
  interpreted by slaves.
- **Address ADP / ADO** — their meaning depends on the selected Command, and their field labels
  update to say so: auto-increment commands (AP*, ARMW) treat ADP as a ring position counted back
  from the master and ADO as a byte offset into the slave's memory; configured-address commands
  (FP*, FRMW) treat ADP as the slave's fixed station address; broadcast commands (B*) ignore ADP
  (leave it `0`); logical commands (L*) treat ADP:ADO together as one 32-bit logical address (ADP =
  low 16 bits, ADO = high 16 bits) matched by a slave's FMMU configuration.
- **Working Counter (WKC)** — starts at `0x0000` for a frame sent by the master; each slave that
  successfully processes the datagram increments it. Only set this to something nonzero if you're
  deliberately hand-crafting a frame to look like it already circulated through slaves (e.g. to
  test a master's response-validation logic in isolation, without a real or simulated slave chain).
- **Datagram Data (hex)** — the same "Application Data" field used by UDP/ICMP, relabeled.

This builds exactly **one** EtherCAT datagram. Real EtherCAT frames often chain several datagrams
together (the "More" bit in each datagram's length word signals another follows) to address
multiple slaves per cycle — that's not constructible in this guided form. Switch L4 Protocol to
"None" and hand-edit the raw bytes if you need a multi-datagram frame; opening such a frame later
will correctly leave it in raw mode rather than truncating it to the first datagram.

### TCP / PDU

TCP and PDU fields are more direct: IPs/ports/connection-id for TCP (`Conn Id`: `-1` opens a new
connection, `-2` closes one, `>=0` reuses an existing one), just a numeric ID for PDU. Neither has
a packed flags field.

## Gotchas and shared semantics

- **`iface` means different things per bus type** (confirmed from `replay_engine.cpp`'s
  `ProtoToCoreFrame()`):
  - **CAN/CANFD** — ignored at replay time. The actual target interface always comes from
    `channel`, resolved via `boat replay start/stream --buses` (channel 1 → first `--buses`
    entry, channel 2 → second, etc.).
  - **Ethernet** — used as a fallback only if `--eth-iface` isn't passed to replay.
  - **PDU** — used directly.
  - **TCP** — not used by this replay path at all (handled by the TCP plugin).
- **Timestamps should be non-decreasing** across the trace. The replay engine schedules frames by
  absolute `timestamp_ns`; a frame timestamped earlier than the one before it is a red flag (Save
  and Push both warn about this) — it used to be able to hang the whole replay subsystem
  indefinitely on that one bad frame, which is now fixed to at least not hang, but out-of-order
  timestamps are still not something you want in a trace meant to replay in order.
- **Len always reflects the real payload**, computed fresh from the hex string, not a cached
  field — trust it even after manual edits.
