# COM Signal Library — bit-packing convention defects

Found while fixing the Python SDK's signal packers on `sdk_rework` (2026-10-05). The Python
side is fixed and pinned by golden vectors; the C++ side is not, and the two now disagree
by design until the items below land.

## Ground truth for `StartPos`

`tools/dbc2boatjson.py:266` copies the DBC `start_bit` **verbatim** into `StartPos`, and
`boat-platform/docs/howto/dbc2boatjson.md:111` documents the result as
`ENGINE_RPM, Length: 16, StartPos: 7, ByteOrder: 1`. So `StartPos` carries the DBC/Vector
convention unchanged:

- For `ByteOrder: 1` (Motorola) `StartPos` is the signal's **MSB** position.
- A bit index `n` addresses byte `n // 8`, bit `n % 8`. **There is no `7 - (n % 8)`
  inversion.**
- Bits run downward from the MSB and wrap to the next byte's bit 7 (`7,6,…,0` then
  `15,14,…,8`), which is what makes a multi-byte Motorola signal big-endian on the wire.

The acceptance table is `boat-platform/sdk/python/tests/data/signal_vectors.json` — 16
hand-derived vectors (Intel/Motorola, 8/12/16-bit, signed/unsigned, aligned and straddling).
They were derived from the convention, not captured from any implementation, so an
implementation that disagrees with them is the thing that is wrong. The Python packers pass
all 16 in both directions plus round-trip.

Note `config/pdu_db.schema.json:140` describes `StartPos` as *"Start bit position (LSB,
0-indexed)"*, which is correct for Intel but wrong for Motorola. Worth a wording fix.

---

## #0 — The whole library is dead code (read this first)  🟢 reachability

**Confirmed 2026-10-05: nothing calls it.** `com_signal.h` is `#include`d by exactly two
files — its own `.cpp` and `src/tests/unit/test_com_signal.cpp`. `PackSignals`,
`UnpackSignals`, `PackIntel/PackMotorola`, `UnpackIntel/UnpackMotorola` and
`E2eCrc8/16/32` have **zero callers** anywhere in `src/`. The only CMake target that pulls
it in besides `boat_hil` itself is `boat_unit_com_signal`.

In particular `src/plugins/pdu_router/` does no signal packing at all — no `StartPos`,
`bit_length` or Motorola handling. (The `SignalDef` in `src/core/scenario/scenario_loader.h`
is an unrelated struct that happens to share the name; it is a scenario signal, not a COM
signal.)

So #1 and #2 below are **latent, not live**: they cannot be reached by the gateway, a
plugin, or a test run today. That drops them from "fix now" to "fix or delete before
anything starts using this". It also means the Python packer in `boat.message` is the only
signal packer actually on a code path, which is why the fix landed there first.

`AGENTS.md`'s "COM Signal Library (C++)" section presents this as a usable facility with a
worked example, with no hint that it is unwired. Worth a note there either way — and note
its example uses `is_motorola = false`, so even the documentation does not exercise the
broken path.

**Decide the disposition before fixing:** if this is intended to become the C++-side packer
(the natural consumer is `pdu_router`, which currently has none), fix #1/#2 and point it at
`signal_vectors.json`. If it is a leftover from an earlier design, delete it and its test.

## #1 — `PackMotorola`/`UnpackMotorola` use a different convention  🟠 latent

`src/hil/pdu/com/com_signal.cpp` computes `dst_bit = start_bit - (bit_length - 1 - i)` with
no byte-relative inversion, which is neither the DBC convention nor what the Python SDK now
does. For `StartPos=7, Length=16, 0x1234` the table requires `12 34`.

## #2 — `uint32_t` underflow → ~512 MB `resize()`  🟠 latent

Same functions, and the more serious half. `dst_bit` is `uint32_t`, so **any** Motorola
signal with `bit_length > start_bit + 1` underflows:

```
start_bit = 7, bit_length = 16, i = 0
  sig_bit_from_lsb = 15
  dst_bit          = 7 - 15  ->  4294967288   (uint32_t wrap)
  byte_idx         = 536870911
  buffer.resize(byte_idx + 1)  ->  ~512 MB
```

That is the *normal* multi-byte Motorola case, not an edge case. `UnpackMotorola` has the
same expression and indexes `data[byte_idx]` out of bounds instead — an OOB read.

`config/pdu_db_test.json` contains signals that reach this: `HV_Current` and `HV_Voltage` are
`ByteOrder: 1, StartPos: 0, Length: 16`, i.e. `0 - 15`.

**Confirmed 2026-10-05** by compiling `PackMotorola` verbatim into a standalone
program (g++ -std=c++20): the `StartPos=7, Length=16` case really does allocate
536870912 bytes. It is also broader than first assumed — the guard is
`bit_length > start_bit + 1`, so a **12-bit** Motorola signal at `StartPos=7` underflows
too. Only an 8-bit signal at `StartPos=7` stays in range.

Reachability: none today, see #0.

## #3 — No signed or Motorola coverage in the C++ tests  🟠

Every case in `src/tests/unit/test_com_signal.cpp` sets `value_type = "Unsigned"`, and none
exercises a multi-byte Motorola signal. That is why #1 and #2 survived. When #1/#2 are fixed,
point the Catch2 suite at `signal_vectors.json` so both languages are held to one table.

Sign extension itself (`com_signal.cpp:134,183,214`) looks correct and is the behaviour the
Python fix was matched against.

## #4 — `config/pdu_db_test.json` has implausible Motorola layouts  🟡

`HV_Current` / `HV_Voltage`: `ByteOrder: 1, StartPos: 0, Length: 16`. In DBC numbering a
Motorola start bit is the MSB, so real 16-bit signals start at 7/15/23 — `StartPos: 0` with
`Length: 16` is not a layout a DBC would produce.

Confirmed empirically with the corrected packer: `HV_Current = -12.5` in an 8-byte message
packs to `01 ff 06 00 00 00 00 00`. The bit sequence for `StartPos=0` runs
`(byte0,bit0) → (byte1,bit7…bit0) → (byte2,bit7…)`, so a 16-bit signal spills across *three*
bytes. It round-trips (pack and unpack agree), so nothing fails — but no ECU would lay a
signal out that way. Either `ByteOrder` was set without intent or `StartPos` should be 7/15.

Needs review against the source DBC. The existing tests could not catch it because they only
round-trip, and all 112 messages in this DB pack without error either way.

The two real vehicle DBs are affected in volume too:
`Dauer_Logging_SEP894_Routing_pdu_db.json` has 86 Motorola signals,
`Logging_Messsung_Startup_from_Bussleep_pdu_db.json` has 83. Any payload previously built
from those through `boat.message` was wrong on the wire.
