# CAN databases from opendbc

BoAt's PDU demos need a realistic CAN database. Rather than ship one, this
folder fetches comma.ai's [opendbc](https://github.com/commaai/opendbc)
collection on demand. Converting a database into BoAt's format is a separate
step you run yourself.

## 1. Fetch the DBC files

```bash
tools/dbc/fetch_opendbc.sh
```

That downloads every `.dbc` from `opendbc/dbc` (57 at the time of writing) into
`tools/dbc/opendbc/`, and records the upstream ref in `tools/dbc/opendbc/FETCHED`.
Pass `--ref <commit|tag|branch>` (or set `OPENDBC_REF`) to pin a revision
instead of tracking `master`.

## 2. Convert one into a BoAt PDU database

```bash
python3 tools/dbc2boatjson.py \
  boat-platform/config/pdu_db.schema.json \
  tools/dbc/opendbc/vw_mlb.dbc \
  tools/dbc/vw_mlb.json \
  --default-cycle-ms 200
```

`--default-cycle-ms 200` matters: opendbc's DBCs carry no `GenMsgCycleTime`
attributes, so without it every message is emitted as `Spontaneous` with a
cycle time of 0 and nothing gets scheduled cyclically. Pick whatever period
suits your test; 200 ms is a reasonable default.

Other useful converter flags: `--bus` / `--bus-type` (default `CAN`),
`--node`, `--start-id`, and `--validate` to check the output against the
schema. Run `python3 tools/dbc2boatjson.py --help` for the full list.

[../test_vw_mlb_replay.py](../test_vw_mlb_replay.py) expects the result at
`tools/dbc/vw_mlb.json` and replays it onto `vcan0`.

## Licensing

**Nothing in this folder except the script and this README is part of BoAt, and
none of it is redistributed.** Both `opendbc/` (fetched) and `*.json`
(generated) are gitignored, so no opendbc-derived content is tracked or shipped
by this repository.

opendbc is MIT-licensed, Copyright (c) 2020, Comma.ai, Inc. Its DBC files are
community reverse-engineered descriptions of vehicle CAN traffic. If you
redistribute the fetched files or anything derived from them, MIT requires you
to carry its copyright and permission notice — see
<https://github.com/commaai/opendbc/blob/master/LICENSE>. That obligation is
yours as the redistributor; it does not attach to BoAt itself.

This arrangement depends on comma.ai keeping the repository public. If the
fetch fails, the demo simply cannot be generated — no BoAt functionality
depends on it.
