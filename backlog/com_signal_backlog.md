# COM Signal Library (C++) — removed

**Resolved by deletion, 2026-10-05.** `src/hil/pdu/com/com_signal.{h,cpp}`,
`src/tests/unit/test_com_signal.cpp` (14 Catch2 cases) and
`demo/pdu_features/03_com_signal_demo.cpp` are gone, along with their CMake wiring.

Kept as a record of why, and of the one finding that outlived the code.

## Why it was deleted rather than fixed

It had two real defects and no users.

**It used the wrong bit layout.** `StartPos` carries the DBC/Vector convention unchanged --
`tools/dbc2boatjson.py:266` copies the DBC `start_bit` verbatim into it, and
`docs/howto/dbc2boatjson.md:111` documents the result as
`ENGINE_RPM, Length: 16, StartPos: 7, ByteOrder: 1`. Under that convention a bit index `n`
addresses byte `n // 8`, bit `n % 8`, and `StartPos=7, Length=16, 0x1234` must pack to
`12 34`. `PackMotorola` computed positions differently and did not.

**It allocated ~512 MB on normal input.** `dst_bit` was `uint32_t`, so any Motorola signal
with `bit_length > start_bit + 1` underflowed. Confirmed by compiling the function standalone:

```
start_bit = 7, bit_length = 16, i = 0
  sig_bit_from_lsb = 15
  dst_bit          = 7 - 15  ->  4294967288   (uint32_t wrap)
  byte_idx         = 536870911
  buffer.resize(536870912)                    ->  512.0 MB, really allocated
```

12-bit signals triggered it too; only an 8-bit signal at `StartPos=7` stayed in range.
`UnpackMotorola` had the same expression and indexed `data[byte_idx]` out of bounds instead.

**Nothing called it.** `com_signal.h` was included by exactly two files -- its own `.cpp` and
its unit test -- plus one standalone demo built by a `g++` line in a comment, never by CMake.
`PackSignals`, `UnpackSignals`, `PackIntel/PackMotorola`, `UnpackIntel/UnpackMotorola` and
`E2eCrc8/16/32` had no callers in `src/`. `pdu_router` does no signal packing at all. Every
case in the unit test set `value_type = "Unsigned"` and none exercised a multi-byte Motorola
signal, which is how both defects survived.

So fixing it would have been work spent making dead code correct. The packer that is actually
on a code path is `boat.message` in the Python SDK, which was fixed separately and is pinned
by `boat-platform/sdk/python/tests/data/signal_vectors.json`.

## If a C++ packer is needed later

`git log -- boat-platform/src/hil/pdu/com/` has the implementation, including the AUTOSAR E2E
CRC-8/16/32 helpers, which were correct as far as their tests went and are the part most
likely to be wanted again.

Hold any replacement to `sdk/python/tests/data/signal_vectors.json` -- 16 vectors over both
byte orders, 8/12/16-bit, signed and unsigned, aligned and straddling, hand-derived from the
DBC convention rather than captured from an implementation. The natural consumer is
`pdu_router`.

Note `config/pdu_db.schema.json:140` describes `StartPos` as *"Start bit position (LSB,
0-indexed)"*, which is right for Intel and wrong for Motorola, where it is the MSB. Worth
correcting whoever next reads it.

---

## Still open: `config/pdu_db_test.json` has implausible Motorola layouts  🟡

The one finding here that is not about the deleted code.

`HV_Current` and `HV_Voltage` are `ByteOrder: 1, StartPos: 0, Length: 16`. In DBC numbering a
Motorola start bit is the signal's MSB, so real 16-bit signals start at 7/15/23 --
`StartPos: 0` with `Length: 16` is not a layout a DBC would produce.

Confirmed with the corrected Python packer: `HV_Current = -12.5` in an 8-byte message packs
to `01 ff 06 00 00 00 00 00`. The bit sequence for `StartPos=0` runs
`(byte0,bit0) → (byte1,bit7…bit0) → (byte2,bit7…)`, so a 16-bit signal spills across *three*
bytes. It round-trips, so nothing fails -- but no ECU would lay a signal out that way. Either
`ByteOrder` was set without intent or `StartPos` should be 7/15.

Needs review against the source DBC. The existing tests could not catch it because they only
round-trip, and all 112 messages in this DB pack without error either way.

The two real vehicle DBs carry Motorola signals in volume --
`Dauer_Logging_SEP894_Routing_pdu_db.json` 86, `Logging_Messsung_Startup_from_Bussleep_pdu_db.json`
83 -- so they are worth the same check.
