# Verifying the Wireshark integration (extcap + PDU dissector)

Manual + scripted verification steps for `tools/wireshark/`: the extcap live-capture plugin
(`boat_extcap.py`), the PDU/signal Lua post-dissector (`boat_pdu_dissector.lua`), and the
`pdu_db.json` → Lua table codegen (`gen_lua_pdu_table.py`).

**Why remote for the live-capture parts**: the extcap script needs a real running `boat_gateway`
with `vcan` traffic to subscribe to — same constraint as the rest of this project's C++/plugin
work, use the Linux test box. The codegen step and the extcap protocol-compliance checks (steps 1-2
below) need neither Wireshark nor a gateway and can be run anywhere with Python.

## 1. Codegen: `pdu_db.json` → Lua table

```bash
python3 tools/wireshark/gen_lua_pdu_table.py --db boat-platform/config/pdu_db_example.json -o tools/wireshark/boat_pdu_db.lua
cat tools/wireshark/boat_pdu_db.lua
```

Expect one Lua table entry per CAN/CAN-FD message in the source JSON, keyed by its `Identifier`
(decimal CAN ID) — `ETH`/`ETH_PDU` entries must NOT appear (they have no wire CAN ID). Regenerate
this file whenever your real `pdu_db.json` changes; it's a committed, regenerate-on-demand artifact
like the proto stubs (`sdk/python/boat/stubs/`), not something to hand-edit.

If `lua`/`luajit` is available: `lua -e 'local t = dofile("tools/wireshark/boat_pdu_db.lua"); for k,v in pairs(t) do print(k, v.name) end'`
should print each CAN ID + message name with no syntax errors.

## 2. extcap protocol compliance (no Wireshark or gateway needed)

Wireshark discovers and drives extcap plugins purely through CLI flags — these can be checked by
hand:

```bash
python3 tools/wireshark/boat_extcap.py --extcap-interfaces
python3 tools/wireshark/boat_extcap.py --extcap-interface boat-gateway --extcap-dlts
python3 tools/wireshark/boat_extcap.py --extcap-interface boat-gateway --extcap-config
```

Expect (respectively): one `extcap {...}` line + one `interface {...}` line; two `dlt {...}` lines
(`227`=CAN_SOCKETCAN, `1`=EN10MB); three `arg {...}` lines (`--host`, `--bus-types`,
`--iface-filter`). If the format doesn't match what's documented at
<https://www.wireshark.org/docs/wsdg_html_chunked/ChCaptureExtcap.html>, Wireshark will silently
fail to list/configure the interface with no useful error — check this before involving Wireshark
at all.

## 3. Live capture, remote Linux box (`testuser@10.10.7.175:~/ProjectBoat`)

**Ordering gotcha, confirmed by actually hitting it**: `PcapngWriter.__init__` opens the fifo with
plain `open(path, "wb")`, which — same as any POSIX fifo — blocks until a *reader* attaches. That
means `boat_extcap.py --capture` does nothing else (does not even start `SubscribeFrames`) until
something opens the fifo for reading. If you `cansend` before the reader (tshark/Wireshark) has
attached, those frames are gone — `SubscribeFrames` is a live stream with no backlog, not a queue.
Start the reader *before* sending any test traffic, with a real gap to let the subscription
actually establish server-side.

```bash
sudo modprobe vcan
sudo ip link add vcan0 type vcan 2>/dev/null; sudo ip link set vcan0 up

cd ~/ProjectBoat/boat-platform
BOAT_CAN_INTERFACES=vcan0 ./build/debug/src/gateway/grpc_gateway/boat_gateway &

mkfifo /tmp/boat_extcap_test.fifo
python3 ~/ProjectBoat/tools/wireshark/boat_extcap.py --capture --extcap-interface boat-gateway \
  --fifo /tmp/boat_extcap_test.fifo --host localhost:50051 --bus-types can,canfd,ethernet &
EXTCAP_PID=$!

# Reader side -- stands in for Wireshark opening the fifo; tshark is the
# closer analog since it can also apply the Lua dissector (step 4). Must
# start BEFORE sending test traffic -- see the ordering gotcha above.
tshark -i /tmp/boat_extcap_test.fifo -c 3 -w /tmp/boat_extcap_out.pcapng &
sleep 2   # let the fifo open unblock and SubscribeFrames actually establish

cansend vcan0 123#DEADBEEF11223344
sleep 0.3
cansend vcan0 456#0102030405060708
sleep 2

kill $EXTCAP_PID
tshark -r /tmp/boat_extcap_out.pcapng -x   # confirm IDs + payload bytes match what was sent
```

Expect: `tshark` prints the decoded frames with correct CAN IDs (`0x123`, `0x456`) and payload
bytes byte-for-byte matching what was sent (cross-check with `candump vcan0` running independently
if you want a second, completely independent confirmation). Confirm `boat_extcap.py`'s process
exits cleanly on `kill` (SIGTERM) — check via
`echo $?` / no leftover zombie holding the fifo open.

## 4. Lua dissector correctness (tshark, no GUI needed)

The dissector itself can be exercised headlessly against a capture from step 3, without touching
the GUI at all -- this is how the bit-extraction/lookup logic actually gets verified against real
Wireshark, not just reviewed by eye:

```bash
tshark -r /tmp/boat_extcap_out.pcapng -X lua_script:tools/wireshark/boat_pdu_dissector.lua -V
```

Known-good reference (from `config/pdu_db_example.json`'s `Motor_1` entry, `Identifier: 123`):
sending `cansend vcan0 07B#C800100000000000` (`0x07B` = decimal 123) must decode to
`BoAt PDU: Motor_1` / `Clamp15 = 0 (Off)` / `MotorSpeed = 100 rpm` -- cross-checked against
`boat.test.pdu.unpack_message()` on the same bytes to confirm it's not just running without
erroring, but actually correct. A frame whose CAN ID has no matching entry must dissect with zero
"BoAt PDU" output and zero Lua errors (`tshark ... 2>&1 | grep -c "BoAt PDU\|Lua Error"` → `0`).

If you change `boat_pdu_dissector.lua`, re-run this before touching the GUI at all -- it catches
real bugs Wireshark's Lua API is unforgiving about (e.g. `TvbRange` has no `get_index` method;
you need `:bytes()` first to get a `ByteArray`, which does -- this exact mistake shipped in the
first draft and only showed up as a `Lua Error` expert-info entry under `-V`, not a crash).

## 5. Full Wireshark GUI — manual, not automatable here

1. Copy `tools/wireshark/boat_pdu_dissector.lua` **and** the generated
   `tools/wireshark/boat_pdu_db.lua` into Wireshark's personal `plugins` folder (same directory,
   both files): Linux `~/.local/lib/wireshark/plugins/`, Windows `%APPDATA%\Wireshark\plugins\`,
   macOS `~/.local/lib/wireshark/plugins/` or `~/Library/Application Support/Wireshark/plugins/`
   depending on version (check *Help → About Wireshark → Folders → Personal Plugins* in the GUI —
   authoritative for your install, the paths above are the common defaults).
2. Copy `tools/wireshark/boat_extcap.py` into Wireshark's personal `extcap` folder (`.../extcap/`
   next to `plugins/` in the same Personal-folders listing) and ensure it's executable
   (`chmod +x` on Linux/macOS).
3. Restart Wireshark (or *Analyze → Reload Lua Plugins*, plus reopen the interfaces list for the
   new extcap entry to appear). Confirm:
   - The interfaces list now shows **BoAt Gateway (live FrameService)**.
   - Clicking the gear/config icon shows the `--host`/`--bus-types`/`--iface-filter` fields from
     step 2 above.
   - Starting a capture against a real gateway shows live frames as they arrive (use
     `cansend`/`boat can-tp send`/etc. on the gateway box to generate traffic while watching).
4. With a `pdu_db.json` that has an entry whose `Identifier` matches a CAN ID you're sending
   (`gen_lua_pdu_table.py` regenerated from it, copied alongside the dissector per step 1): click a
   matching CAN frame in the packet list and confirm a **"BoAt PDU: <name>"** subtree appears in the
   packet details pane with each signal's decoded physical value (and enum text, if the signal has
   `EnumValues`), and that the message name is appended to the Info column.
5. Sanity-check a frame with **no** matching CAN ID still dissects normally (no BoAt PDU tree, no
   errors) — the post-dissector must be a no-op for unknown traffic, not break anything else.

### Known limitations (by design, see the implementation plan)

- PDU and TCP bus types are not part of the live capture — no wire/link-layer encoding for them.
- No auto-installer; steps 1-2 above are manual. Wireshark's personal-folder paths vary enough
  across OS/version that guessing them programmatically without testing on all of them was judged
  riskier than documenting the *Help → About → Folders* lookup.
- Not verified against Windows extcap fifo semantics (Wireshark abstracts the platform-specific
  fifo/pipe mechanism, but this has only been exercised on Linux, matching where the actual gateway
  runs).
