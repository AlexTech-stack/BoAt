# PDU E2E protection (runtime) — gap

`test/PDU.md`'s `TC_PDU_010` used to assert that a database message with
`isE2E: 4` is transmitted with an incrementing rolling counter and a valid
per-frame E2E CRC. **That runtime feature does not exist.** The case was
rewritten to `TC_PDU_010_e2e_metadata_roundtrip`, which verifies only what the
platform actually does: `isE2E` and the signal layout round-trip through the
database and `boat db show --pack`.

## What exists today

- `isE2E` is a schema field (`boat-platform/config/pdu_db.schema.json`) carrying
  an AUTOSAR E2E profile number (or 0), consumed by the PDU Editor, the DBC
  converter, the Trace Analyzer/Reverse Engineer, and `boat db show`.
- `boat.message.Message.pack()` packs ordinary signals (Intel/Motorola,
  factor/offset, multiplexing). It has no E2E step.

## What is missing

- No per-frame counter update and no CRC computation anywhere in the
  transmission path (`pdu_router`'s transmission engine) or in the packing
  path (`boat.message.Message` / `PduMessageNode`).
- The database records only the profile **number**. AUTOSAR E2E also needs,
  per protected PDU, the CRC offset/length, the counter offset/length, the Data
  ID, and the profile's algorithm — none of which the schema expresses today.

## What implementing would require

1. Extend the PDU DB schema with the E2E layout (CRC position/length, counter
   position/length, Data ID, profile algorithm) and update
   `pdu_db.schema.json` + `tools/dbc2boatjson.py` + PDU Editor.
2. Decide where the per-frame transform lives. A cyclic route is driven by
   `pdu_router`'s transmission engine, so a counter that increments per frame
   must be recomputed there — which means the engine needs the E2E layout,
   not just the raw payload it has today.
3. Implement at least profile 4 (24-bit CRC + 8-bit counter) and validate
   against the AUTOSAR PRS E2E spec (`spec/text/AUTOSAR_PRS_E2EProtocol.txt`).
4. Restore a runtime E2E test case with a fixture PDU DB that carries the full
   layout.

Until then, E2E is an import/analysis annotation, not protection.
