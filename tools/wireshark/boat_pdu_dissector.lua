-- BoAt PDU/signal post-dissector for Wireshark.
--
-- Decodes CAN frames captured from BoAt (via boat_extcap.py, or any
-- DLT_CAN_SOCKETCAN capture) into named PDUs and signal values, using a
-- table generated from pdu_db.json by gen_lua_pdu_table.py. Runs as a
-- post-dissector (register_postdissector), i.e. AFTER Wireshark's own
-- built-in SocketCAN dissector has already run -- this only adds a
-- "BoAt PDU" tree on top, it does not replace Wireshark's native CAN
-- decoding/columns/filtering.
--
-- Install: copy this file AND boat_pdu_db.lua (generate it first, see
-- gen_lua_pdu_table.py) into Wireshark's personal plugins folder. See
-- docs/testing/wireshark-integration-verification.md for exact paths.
--
-- Bit-extraction below is a direct, line-for-line port of
-- sdk/python/boat/test/pdu.py's _unpack_intel/_unpack_motorola -- keep
-- them in sync if that logic ever changes. Uses only arithmetic (no
-- native bitwise operators), since Wireshark has shipped multiple Lua
-- versions across releases and this needs to work on all of them.

local boat_pdu = Proto("boat_pdu", "BoAt PDU")

local PDU_DB_FILENAME = "boat_pdu_db.lua"

local function script_dir()
  local info = debug.getinfo(1, "S")
  local path = info.source:match("^@(.*[/\\])")
  return path or ""
end

local function load_pdu_db()
  local path = script_dir() .. PDU_DB_FILENAME
  local chunk, err = loadfile(path)
  if not chunk then
    return nil, "could not load " .. path .. ": " .. tostring(err)
  end
  local ok, result = pcall(chunk)
  if not ok then
    return nil, "error running " .. path .. ": " .. tostring(result)
  end
  return result, nil
end

local pdu_db, pdu_db_err = load_pdu_db()

-- ── Bit-level helpers (arithmetic-only, no bitwise ops) ─────────────────────

local function get_bit(byte_val, bit_in_byte)
  return math.floor(byte_val / (2 ^ bit_in_byte)) % 2
end

-- Extract `length` bits at Intel start_bit from a byte array (1-indexed
-- Lua table of integers 0-255). Mirrors _unpack_intel in boat/test/pdu.py.
local function unpack_intel(data, start_bit, length)
  local raw = 0
  local bit = start_bit + length - 1
  for _ = 1, length do
    local byte_idx = math.floor(bit / 8)
    local bit_in_byte = bit % 8
    local b = data[byte_idx + 1] or 0
    raw = raw * 2 + get_bit(b, bit_in_byte)
    bit = bit - 1
  end
  return raw
end

-- Extract `length` bits at Motorola MSB start_bit. Mirrors
-- _unpack_motorola in boat/test/pdu.py (same zig-zag traversal).
local function unpack_motorola(data, start_bit, length)
  local raw = 0
  local sb = start_bit
  for _ = 1, length do
    local byte_idx = math.floor(sb / 8)
    local bit_in_byte = 7 - (sb % 8)
    local b = data[byte_idx + 1] or 0
    raw = raw * 2 + get_bit(b, bit_in_byte)
    if sb % 8 == 0 then
      sb = sb + 15
    else
      sb = sb - 1
    end
  end
  return raw
end

local function unpack_signal(data, sig)
  if sig.byte_order == "motorola" then
    return unpack_motorola(data, sig.start, sig.length)
  end
  return unpack_intel(data, sig.start, sig.length)
end

local function raw_to_physical(raw, sig)
  local factor = sig.factor
  if factor == 0 then factor = 1.0 end
  return raw * factor + sig.offset
end

-- ── Postdissector ────────────────────────────────────────────────────────────

local f_can_id = Field.new("can.id")

-- CAN_EFF_FLAG (extended ID) -- must be masked off the same way
-- sdk/python/boat/pcapng.py's unpack_can_frame does when reading the ID
-- back out of a DLT_CAN_SOCKETCAN frame.
local CAN_EFF_FLAG = 0x80000000
local CAN_ID_MASK  = 0x1FFFFFFF

function boat_pdu.dissector(tvb, pinfo, tree)
  if pdu_db == nil then
    return
  end

  local can_id_field = f_can_id()
  if can_id_field == nil then
    return  -- not a CAN frame (or Wireshark's socketcan dissector didn't run)
  end

  local raw_id = can_id_field.value
  local can_id = raw_id % CAN_EFF_FLAG  -- drop the EFF flag bit, keep 29/11-bit ID

  local msg = pdu_db[can_id]
  if msg == nil then
    return
  end

  -- CAN payload starts at a fixed offset in DLT_CAN_SOCKETCAN's on-wire
  -- layout (see pack_can_frame in sdk/python/boat/pcapng.py): 4-byte BE
  -- id + 1 length + 3 reserved/flag bytes = 8-byte header, then data.
  local ok, data_bytes = pcall(function()
    return tvb(8, math.min(msg.length, tvb:len() - 8)):bytes()
  end)
  if not ok then
    return
  end
  local data = {}
  for i = 0, data_bytes:len() - 1 do
    data[i + 1] = data_bytes:get_index(i)
  end

  local subtree = tree:add(boat_pdu, tvb(), "BoAt PDU: " .. msg.name)
  pinfo.cols.info:append("  [" .. msg.name .. "]")

  for _, sig in ipairs(msg.signals) do
    local raw = unpack_signal(data, sig)
    local phys = raw_to_physical(raw, sig)
    local text = sig.name .. " = " .. tostring(phys)
    if sig.unit ~= "" then
      text = text .. " " .. sig.unit
    end
    if sig.enum and sig.enum[raw] then
      text = text .. " (" .. sig.enum[raw] .. ")"
    end
    subtree:add(boat_pdu, tvb(), text)
  end
end

if pdu_db == nil then
  -- Report the load failure once, loudly, rather than silently dissecting
  -- nothing -- a missing/misnamed boat_pdu_db.lua is the most likely
  -- install mistake.
  report_failure("boat_pdu_dissector: " .. tostring(pdu_db_err))
else
  register_postdissector(boat_pdu)
end
