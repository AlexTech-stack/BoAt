"""
BoAt Platform — Commander Panel
Run:  python3 demo/commander.py
Open: http://localhost:8082
"""
from __future__ import annotations

import os
import sys
from typing import Optional

sys.path.insert(0, "/home/testuser/.local/lib/python3.12/site-packages")
sys.path.insert(0, "/home/testuser/ProjectBoat/boat-platform/sdk/python")

import grpc
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from boat.client import BoAtClient
from boat.v1 import bus_pb2, can_pb2, ethernet_pb2

_CANFD_FDF = 0x04
_CANFD_BRS = 0x01

_DEFAULT_GW = os.environ.get("BOAT_GATEWAY", "localhost:50051")
_PORT       = int(os.environ.get("BOAT_CMD_PORT", "8082"))

# ── gRPC client cache (one channel per address) ────────────────────────────────

_clients: dict[str, BoAtClient] = {}

def _client(address: str) -> BoAtClient:
    if address not in _clients:
        _clients[address] = BoAtClient(address)
    return _clients[address]


# ── Request models ─────────────────────────────────────────────────────────────

class CanSendReq(BaseModel):
    address:  str = _DEFAULT_GW
    can_id:   str           # hex or decimal, e.g. "0x123"
    data:     str = ""      # hex bytes, e.g. "DEADBEEF"
    dlc:      int = -1      # -1 = infer from data
    iface:    str = ""      # "" = all registered buses
    fd:       bool = False
    brs:      bool = False

class EthSendReq(BaseModel):
    address:    str = _DEFAULT_GW
    ethertype:  str = "0x0800"
    payload:    str = ""    # hex bytes
    iface:      str = ""    # "" = all
    src_mac:    str = ""    # hex, e.g. "020000000001" or "02:00:00:00:00:01"
    dst_mac:    str = ""

class BusPublishReq(BaseModel):
    address:    str = _DEFAULT_GW
    name:       str
    value_type: str         # "number" | "string" | "bool" | "bytes"
    value:      str
    publisher:  str = "commander"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _parse_hex_bytes(s: str) -> bytes:
    return bytes.fromhex(s.replace(":", "").replace("-", "").replace(" ", ""))

def _parse_mac(s: str) -> bytes:
    if not s:
        return b""
    b = _parse_hex_bytes(s)
    if len(b) != 6:
        raise ValueError(f"MAC must be 6 bytes, got {len(b)}")
    return b


# ── FastAPI ────────────────────────────────────────────────────────────────────

app = FastAPI()


@app.get("/api/gateway")
def api_gateway():
    return {"address": _DEFAULT_GW}


# ── Discovery endpoints ────────────────────────────────────────────────────────

@app.get("/api/can/buses")
def api_can_buses(address: str = _DEFAULT_GW):
    try:
        resp = _client(address).can.ListBuses(can_pb2.ListBusesRequest())
        return {"ifaces": list(resp.ifaces)}
    except Exception:
        return {"ifaces": []}


@app.get("/api/eth/ifaces")
def api_eth_ifaces(address: str = _DEFAULT_GW):
    try:
        resp = _client(address).ethernet.ListInterfaces(
            ethernet_pb2.ListEthernetInterfacesRequest()
        )
        return {"ifaces": list(resp.ifaces)}
    except Exception:
        return {"ifaces": []}


@app.get("/api/bus/signals")
def api_bus_signals(address: str = _DEFAULT_GW):
    try:
        resp = _client(address).bus.ListSignals(bus_pb2.BusListSignalsRequest())
        return {"signals": list(resp.names)}
    except Exception:
        return {"signals": []}


# ── Send / Publish endpoints ───────────────────────────────────────────────────

@app.post("/api/can/send")
def api_can_send(req: CanSendReq):
    try:
        can_id = int(req.can_id, 0)
    except ValueError:
        return {"ok": False, "detail": f"Invalid CAN ID: {req.can_id!r}"}
    try:
        raw = _parse_hex_bytes(req.data) if req.data.strip() else b""
    except ValueError as e:
        return {"ok": False, "detail": f"Invalid data: {e}"}

    flags = 0
    if req.fd:
        flags |= _CANFD_FDF
    if req.brs and req.fd:
        flags |= _CANFD_BRS

    max_len  = 64 if req.fd else 8
    if len(raw) > max_len:
        return {"ok": False, "detail": f"Payload too long ({len(raw)} > {max_len} bytes)"}

    byte_count = req.dlc if req.dlc >= 0 else len(raw)
    byte_count = min(byte_count, max_len)
    if byte_count > len(raw):
        raw = raw + bytes(byte_count - len(raw))  # zero-pad
    else:
        raw = raw[:byte_count]                     # truncate

    frame = can_pb2.CanFrame(
        can_id=can_id, dlc=byte_count, data=raw,
        iface=req.iface, flags=flags,
    )
    try:
        resp = _client(req.address).can.SendCanFrame(
            can_pb2.SendCanFrameRequest(frame=frame)
        )
        return {"ok": bool(resp.accepted)}
    except grpc.RpcError as e:
        return {"ok": False, "detail": f"gRPC error: {e.details()}"}
    except Exception as e:
        return {"ok": False, "detail": str(e)}


@app.post("/api/eth/send")
def api_eth_send(req: EthSendReq):
    try:
        etype = int(req.ethertype, 0)
    except ValueError:
        return {"ok": False, "detail": f"Invalid ethertype: {req.ethertype!r}"}
    try:
        payload = _parse_hex_bytes(req.payload) if req.payload.strip() else b""
    except ValueError as e:
        return {"ok": False, "detail": f"Invalid payload: {e}"}
    try:
        src = _parse_mac(req.src_mac)
        dst = _parse_mac(req.dst_mac)
    except ValueError as e:
        return {"ok": False, "detail": str(e)}
    if len(payload) > 1500:
        return {"ok": False, "detail": f"Payload too long ({len(payload)} > 1500 bytes)"}

    frame = ethernet_pb2.EthernetFrame(
        ethertype=etype, payload=payload,
        iface=req.iface, src_mac=src, dst_mac=dst,
    )
    try:
        resp = _client(req.address).ethernet.SendFrame(
            ethernet_pb2.SendEthernetFrameRequest(frame=frame)
        )
        return {"ok": bool(resp.accepted)}
    except grpc.RpcError as e:
        return {"ok": False, "detail": f"gRPC error: {e.details()}"}
    except Exception as e:
        return {"ok": False, "detail": str(e)}


@app.post("/api/bus/publish")
def api_bus_publish(req: BusPublishReq):
    if not req.name.strip():
        return {"ok": False, "detail": "Signal name is required"}
    sig = bus_pb2.BusSignal(name=req.name.strip(), publisher=req.publisher)
    try:
        vt = req.value_type
        if vt == "number":
            sig.number_value = float(req.value)
        elif vt == "string":
            sig.string_value = req.value
        elif vt == "bool":
            sig.bool_value = req.value.strip().lower() in ("1", "true", "yes", "on")
        elif vt == "bytes":
            sig.bytes_value = _parse_hex_bytes(req.value)
        else:
            return {"ok": False, "detail": f"Unknown value type: {vt!r}"}
    except ValueError as e:
        return {"ok": False, "detail": str(e)}

    try:
        resp = _client(req.address).bus.Publish(bus_pb2.BusPublishRequest(signal=sig))
        return {"ok": bool(resp.accepted)}
    except grpc.RpcError as e:
        return {"ok": False, "detail": f"gRPC error: {e.details()}"}
    except Exception as e:
        return {"ok": False, "detail": str(e)}


# ── HTML ───────────────────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>BoAt — Commander</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --bg:     #0d1117;
    --panel:  #161b22;
    --border: #30363d;
    --text:   #e6edf3;
    --muted:  #8b949e;
    --blue:   #58a6ff;
    --green:  #3fb950;
    --yellow: #d29922;
    --red:    #f85149;
    --purple: #d2a8ff;
    --orange: #ffa657;
    --mono:   "SFMono-Regular", Consolas, "Liberation Mono", monospace;
  }

  html, body {
    height: 100%;
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    font-size: 13px;
    overflow: hidden;
  }

  /* ── Header ── */
  header {
    height: 46px;
    background: var(--panel);
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    padding: 0 20px;
    gap: 14px;
    flex-shrink: 0;
  }
  .logo     { font-weight: 700; font-size: 15px; color: var(--blue); letter-spacing: .4px; }
  .subtitle { color: var(--muted); font-size: 12px; }
  header .spacer { flex: 1; }
  .gw-badge {
    font-size: 11px; padding: 2px 10px; border-radius: 12px;
    background: #1f3a1f; color: var(--green); border: 1px solid #2ea043;
    font-family: var(--mono);
  }
  .gw-input {
    background: var(--bg); border: 1px solid var(--border);
    color: var(--text); border-radius: 4px;
    padding: 3px 9px; font-size: 12px; font-family: var(--mono);
    width: 200px; outline: none;
  }
  .gw-input:focus { border-color: var(--blue); }

  /* ── Three-column layout ── */
  .layout {
    display: flex;
    height: calc(100vh - 78px);
    overflow: hidden;
  }
  .col {
    flex: 1;
    display: flex;
    flex-direction: column;
    border-right: 1px solid var(--border);
    min-height: 0;
    min-width: 0;
  }
  .col:last-child { border-right: none; }

  /* ── Pane chrome (shared with dashboard) ── */
  .pane-header {
    height: 36px;
    padding: 0 14px;
    display: flex;
    align-items: center;
    gap: 8px;
    border-bottom: 1px solid var(--border);
    background: var(--panel);
    flex-shrink: 0;
  }
  .pane-title {
    font-size: 11px; font-weight: 600;
    text-transform: uppercase; letter-spacing: .8px; color: var(--muted);
  }
  .pane-spacer { flex: 1; }

  .btn-icon {
    background: none; border: none; color: var(--muted);
    cursor: pointer; font-size: 13px; padding: 2px 4px;
    border-radius: 3px; transition: color .15s;
  }
  .btn-icon:hover { color: var(--blue); }

  /* ── Form area ── */
  .form-area {
    flex-shrink: 0;
    padding: 14px;
    display: flex;
    flex-direction: column;
    gap: 8px;
    border-bottom: 1px solid var(--border);
  }

  .field-row {
    display: grid;
    grid-template-columns: 96px 1fr;
    align-items: center;
    gap: 8px;
  }
  .field-row.full-row {
    grid-template-columns: 1fr;
  }
  label {
    font-size: 11px; color: var(--muted);
    text-align: right; white-space: nowrap;
  }

  input[type="text"], input[type="number"], select, textarea {
    background: var(--bg);
    border: 1px solid var(--border);
    color: var(--text);
    border-radius: 4px;
    padding: 4px 8px;
    font-size: 12px;
    font-family: var(--mono);
    width: 100%;
    outline: none;
    transition: border-color .15s;
  }
  input[type="text"]:focus,
  input[type="number"]:focus,
  select:focus { border-color: var(--blue); }
  input::placeholder { color: #3d4450; }
  select option { background: var(--panel); }

  /* checkboxes row */
  .check-row {
    display: grid;
    grid-template-columns: 96px 1fr;
    align-items: center;
    gap: 8px;
  }
  .checks {
    display: flex; gap: 18px; align-items: center;
  }
  .check-label {
    display: flex; align-items: center; gap: 5px;
    font-size: 12px; font-family: var(--mono); color: var(--text);
    cursor: pointer; user-select: none;
  }
  input[type="checkbox"] {
    accent-color: var(--blue);
    width: 13px; height: 13px; cursor: pointer;
  }
  input[type="checkbox"]:disabled + span { color: var(--muted); }

  /* send button */
  .send-row {
    display: grid;
    grid-template-columns: 96px 1fr;
    align-items: center;
    gap: 8px;
    margin-top: 2px;
  }
  .btn-send {
    padding: 6px 0;
    background: #1c3a5c; color: var(--blue);
    border: 1px solid var(--blue); border-radius: 5px;
    font-size: 12px; font-weight: 600;
    cursor: pointer; transition: background .15s;
    width: 100%;
  }
  .btn-send:hover:not(:disabled) { background: #24497a; }
  .btn-send:disabled { opacity: .4; cursor: not-allowed; }
  .btn-send.success { background: #1a3a1a; color: var(--green); border-color: var(--green); }
  .btn-send.error   { background: #3a1a1a; color: var(--red);   border-color: var(--red); }

  /* value type selector (bus signal) */
  .vtype-select {
    max-width: 110px;
  }

  /* ── Activity feed ── */
  .activity-area {
    flex: 1;
    display: flex;
    flex-direction: column;
    min-height: 0;
  }
  .activity-header {
    padding: 6px 14px;
    border-bottom: 1px solid var(--border);
    font-size: 10px; font-weight: 600;
    text-transform: uppercase; letter-spacing: .7px;
    color: var(--muted);
    background: var(--panel);
    flex-shrink: 0;
  }
  .activity-scroll {
    flex: 1;
    overflow-y: auto;
    padding: 4px 0;
  }
  .act-item {
    display: flex;
    align-items: baseline;
    gap: 8px;
    padding: 4px 14px;
    border-bottom: 1px solid rgba(48,54,61,.3);
    font-family: var(--mono);
    font-size: 11px;
    animation: fadeIn .2s ease-out;
  }
  @keyframes fadeIn { from { background: rgba(88,166,255,.08); } to { background: transparent; } }
  .act-ts   { color: var(--muted); flex-shrink: 0; width: 72px; }
  .act-desc { flex: 1; color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .act-ok   { flex-shrink: 0; color: var(--green); }
  .act-err  { flex-shrink: 0; color: var(--red); }
  .act-detail { color: var(--red); font-size: 10px; margin-top: 1px; padding-left: 80px; font-style: italic; }

  ::-webkit-scrollbar { width: 4px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }

  /* ── Nav bar ── */
  #panel-nav {
    height: 32px; flex-shrink: 0;
    background: #0d1117;
    border-bottom: 1px solid var(--border);
    display: flex; align-items: center;
    padding: 0 16px; gap: 2px;
  }
  #panel-nav a {
    font-size: 11px; color: var(--muted);
    text-decoration: none;
    padding: 3px 11px; border-radius: 4px;
    transition: background .12s, color .12s;
  }
  #panel-nav a:hover { background: #21262d; color: var(--text); }
  #panel-nav a.active { color: var(--blue); background: rgba(88,166,255,.10); font-weight: 600; }
</style>
</head>
<body>

<header>
  <span class="logo">⛵ BoAt</span>
  <span class="subtitle">Commander</span>
  <span class="spacer"></span>
  <input class="gw-input" id="gw-addr" placeholder="localhost:50051"/>
  <span class="gw-badge" id="gw-badge">● :50051</span>
</header>

<nav id="panel-nav">
  <a class="nav-link" data-port="8080">Dashboard</a>
  <a class="nav-link" data-port="8081">Nodes</a>
  <a class="nav-link" data-port="8082">Commander</a>
  <a class="nav-link" data-port="8083">Recorder</a>
  <a class="nav-link" data-port="8084">Debug</a>
  <a class="nav-link" data-port="8085">Flow Editor</a>
</nav>

<div class="layout">

  <!-- ══ CAN Send ══ -->
  <div class="col">
    <div class="pane-header">
      <span class="pane-title">CAN Send</span>
      <span class="pane-spacer"></span>
      <button class="btn-icon" title="Reload interfaces" onclick="loadCanBuses()">↺</button>
    </div>

    <div class="form-area">
      <div class="field-row">
        <label for="can-id">CAN ID</label>
        <input type="text" id="can-id" placeholder="0x123" spellcheck="false"/>
      </div>
      <div class="field-row">
        <label for="can-data">Data (hex)</label>
        <input type="text" id="can-data" placeholder="DE AD BE EF" spellcheck="false"/>
      </div>
      <div class="field-row">
        <label for="can-dlc">DLC override</label>
        <input type="number" id="can-dlc" placeholder="auto" min="0" max="64"/>
      </div>
      <div class="field-row">
        <label for="can-iface">Interface</label>
        <select id="can-iface">
          <option value="" disabled selected>— select —</option>
        </select>
      </div>
      <div class="check-row">
        <label></label>
        <div class="checks">
          <label class="check-label">
            <input type="checkbox" id="can-fd" onchange="onFdChange()"/>
            <span>CAN FD</span>
          </label>
          <label class="check-label">
            <input type="checkbox" id="can-brs" disabled/>
            <span>BRS</span>
          </label>
        </div>
      </div>
      <div class="send-row">
        <label></label>
        <button class="btn-send" id="btn-can-send" onclick="sendCan()">Send Frame</button>
      </div>
    </div>

    <div class="activity-area">
      <div class="activity-header">Recent sends</div>
      <div class="activity-scroll" id="act-can"></div>
    </div>
  </div>

  <!-- ══ Ethernet Send ══ -->
  <div class="col">
    <div class="pane-header">
      <span class="pane-title">Ethernet Send</span>
      <span class="pane-spacer"></span>
      <button class="btn-icon" title="Reload interfaces" onclick="loadEthIfaces()">↺</button>
    </div>

    <div class="form-area">
      <div class="field-row">
        <label for="eth-iface">Interface</label>
        <select id="eth-iface">
          <option value="" disabled selected>— select —</option>
        </select>
      </div>
      <div class="field-row">
        <label for="eth-etype">EtherType</label>
        <input type="text" id="eth-etype" value="0x0800" spellcheck="false"/>
      </div>
      <div class="field-row">
        <label for="eth-payload">Payload (hex)</label>
        <input type="text" id="eth-payload" placeholder="11 AA 22 BB" spellcheck="false"/>
      </div>
      <div class="field-row">
        <label for="eth-src">Src MAC</label>
        <input type="text" id="eth-src" placeholder="02:00:00:00:00:01  (optional)" spellcheck="false"/>
      </div>
      <div class="field-row">
        <label for="eth-dst">Dst MAC</label>
        <input type="text" id="eth-dst" placeholder="FF:FF:FF:FF:FF:FF  (optional)" spellcheck="false"/>
      </div>
      <div class="send-row">
        <label></label>
        <button class="btn-send" id="btn-eth-send" onclick="sendEth()">Send Frame</button>
      </div>
    </div>

    <div class="activity-area">
      <div class="activity-header">Recent sends</div>
      <div class="activity-scroll" id="act-eth"></div>
    </div>
  </div>

  <!-- ══ Bus Signal Publish ══ -->
  <div class="col">
    <div class="pane-header">
      <span class="pane-title">Bus Signal</span>
      <span class="pane-spacer"></span>
      <button class="btn-icon" title="Reload signal names" onclick="loadSignals()">↺</button>
    </div>

    <div class="form-area">
      <div class="field-row">
        <label for="bus-name">Signal name</label>
        <input type="text" id="bus-name" placeholder="engine.rpm" list="signal-list" spellcheck="false"/>
        <datalist id="signal-list"></datalist>
      </div>
      <div class="field-row">
        <label for="bus-vtype">Type</label>
        <select id="bus-vtype" class="vtype-select" onchange="onVtypeChange()">
          <option value="number">number</option>
          <option value="string">string</option>
          <option value="bool">bool</option>
          <option value="bytes">bytes (hex)</option>
        </select>
      </div>
      <div class="field-row" id="bus-value-row">
        <label for="bus-value">Value</label>
        <input type="text" id="bus-value" placeholder="0.0" spellcheck="false"/>
      </div>
      <!-- bool shortcut row, hidden by default -->
      <div class="field-row" id="bus-bool-row" style="display:none">
        <label></label>
        <div class="checks">
          <label class="check-label">
            <input type="checkbox" id="bus-bool-check"/>
            <span id="bus-bool-label">false</span>
          </label>
        </div>
      </div>
      <div class="field-row">
        <label for="bus-pub">Publisher</label>
        <input type="text" id="bus-pub" value="commander" spellcheck="false"/>
      </div>
      <div class="send-row">
        <label></label>
        <button class="btn-send" id="btn-bus-pub" onclick="publishBus()">Publish</button>
      </div>
    </div>

    <div class="activity-area">
      <div class="activity-header">Recent publishes</div>
      <div class="activity-scroll" id="act-bus"></div>
    </div>
  </div>

</div><!-- .layout -->

<script>
// ── Gateway ───────────────────────────────────────────────────────────────────
let gateway = 'localhost:50051';

async function initGateway() {
  const r = await fetch('/api/gateway');
  const d = await r.json();
  gateway = d.address;
  document.getElementById('gw-addr').value  = gateway;
  document.getElementById('gw-badge').textContent = '● ' + gateway;
}

document.getElementById('gw-addr').addEventListener('change', e => {
  gateway = e.target.value.trim() || 'localhost:50051';
  document.getElementById('gw-badge').textContent = '● ' + gateway;
  loadCanBuses(); loadEthIfaces(); loadSignals();
});

// ── Interface / signal loaders ────────────────────────────────────────────────
async function loadCanBuses() {
  const sel = document.getElementById('can-iface');
  const prev = sel.value;
  sel.innerHTML = '<option value="" disabled selected>— select —</option>';
  try {
    const r = await fetch('/api/can/buses?address=' + encodeURIComponent(gateway));
    const d = await r.json();
    for (const iface of d.ifaces) {
      const opt = document.createElement('option');
      opt.value = iface; opt.textContent = iface;
      if (iface === prev) opt.selected = true;
      sel.appendChild(opt);
    }
  } catch {}
}

async function loadEthIfaces() {
  const sel = document.getElementById('eth-iface');
  const prev = sel.value;
  sel.innerHTML = '<option value="" disabled selected>— select —</option>';
  try {
    const r = await fetch('/api/eth/ifaces?address=' + encodeURIComponent(gateway));
    const d = await r.json();
    for (const iface of d.ifaces) {
      const opt = document.createElement('option');
      opt.value = iface; opt.textContent = iface;
      if (iface === prev) opt.selected = true;
      sel.appendChild(opt);
    }
  } catch {}
}

async function loadSignals() {
  const dl = document.getElementById('signal-list');
  dl.innerHTML = '';
  try {
    const r = await fetch('/api/bus/signals?address=' + encodeURIComponent(gateway));
    const d = await r.json();
    for (const name of d.signals) {
      const opt = document.createElement('option');
      opt.value = name;
      dl.appendChild(opt);
    }
  } catch {}
}

// ── CAN FD checkbox ───────────────────────────────────────────────────────────
function onFdChange() {
  const fd  = document.getElementById('can-fd').checked;
  const brs = document.getElementById('can-brs');
  brs.disabled = !fd;
  if (!fd) brs.checked = false;
}

// ── Bus value type switcher ───────────────────────────────────────────────────
function onVtypeChange() {
  const vt       = document.getElementById('bus-vtype').value;
  const valRow   = document.getElementById('bus-value-row');
  const boolRow  = document.getElementById('bus-bool-row');
  const valInput = document.getElementById('bus-value');

  if (vt === 'bool') {
    valRow.style.display  = 'none';
    boolRow.style.display = '';
  } else {
    valRow.style.display  = '';
    boolRow.style.display = 'none';
  }

  const placeholders = { number: '120.5', string: 'hello', bytes: 'DE AD BE EF' };
  valInput.placeholder = placeholders[vt] || '';
}

// update label next to bool checkbox
document.getElementById('bus-bool-check').addEventListener('change', e => {
  document.getElementById('bus-bool-label').textContent = e.target.checked ? 'true' : 'false';
});

// ── Activity feed ─────────────────────────────────────────────────────────────
function appendActivity(containerId, desc, result) {
  const el = document.getElementById(containerId);
  const ts = new Date().toTimeString().slice(0,12).replace(' ','');

  const item = document.createElement('div');
  item.className = 'act-item';
  const ok = result.ok;
  item.innerHTML =
    `<span class="act-ts">${ts}</span>` +
    `<span class="act-desc">${escHtml(desc)}</span>` +
    `<span class="${ok ? 'act-ok' : 'act-err'}">${ok ? '✓' : '✗'}</span>`;
  el.insertBefore(item, el.firstChild);

  if (!ok && result.detail) {
    const det = document.createElement('div');
    det.className = 'act-detail';
    det.textContent = result.detail;
    el.insertBefore(det, item.nextSibling);
  }

  // Keep at most 30 items (each entry may be 2 nodes: item + detail)
  while (el.children.length > 40) el.removeChild(el.lastChild);
}

// ── Flash send button ─────────────────────────────────────────────────────────
function flashBtn(id, ok) {
  const btn = document.getElementById(id);
  btn.disabled = true;
  btn.classList.add(ok ? 'success' : 'error');
  btn.textContent = ok ? '✓ Accepted' : '✗ Failed';
  setTimeout(() => {
    btn.classList.remove('success', 'error');
    btn.disabled = false;
    btn.textContent = id.includes('bus') ? 'Publish' : 'Send Frame';
  }, 1200);
}

// ── CAN send ──────────────────────────────────────────────────────────────────
async function sendCan() {
  const canId = document.getElementById('can-id').value.trim();
  if (!canId) { document.getElementById('can-id').focus(); return; }
  const iface = document.getElementById('can-iface').value;
  if (!iface) { document.getElementById('can-iface').focus(); return; }

  const body = {
    address: gateway,
    can_id:  canId,
    data:    document.getElementById('can-data').value.trim(),
    dlc:     parseInt(document.getElementById('can-dlc').value) || -1,
    iface:   iface,
    fd:      document.getElementById('can-fd').checked,
    brs:     document.getElementById('can-brs').checked,
  };

  const desc  = `${canId}  data=${body.data || '—'}  iface=${iface}` +
                (body.fd ? '  FD' : '') + (body.brs ? '+BRS' : '');

  document.getElementById('btn-can-send').disabled = true;
  let result;
  try {
    const r = await fetch('/api/can/send', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify(body),
    });
    result = await r.json();
  } catch (e) {
    result = { ok: false, detail: String(e) };
  }
  flashBtn('btn-can-send', result.ok);
  appendActivity('act-can', desc, result);
}

// ── Ethernet send ─────────────────────────────────────────────────────────────
async function sendEth() {
  const etype = document.getElementById('eth-etype').value.trim() || '0x0800';
  const iface = document.getElementById('eth-iface').value;
  if (!iface) { document.getElementById('eth-iface').focus(); return; }
  const body  = {
    address:   gateway,
    ethertype: etype,
    payload:   document.getElementById('eth-payload').value.trim(),
    iface:     iface,
    src_mac:   document.getElementById('eth-src').value.trim(),
    dst_mac:   document.getElementById('eth-dst').value.trim(),
  };

  const plen  = body.payload.replace(/[:\s]/g,'').length / 2;
  const desc  = `type=${etype}  ${plen}B  iface=${iface}`;

  document.getElementById('btn-eth-send').disabled = true;
  let result;
  try {
    const r = await fetch('/api/eth/send', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify(body),
    });
    result = await r.json();
  } catch (e) {
    result = { ok: false, detail: String(e) };
  }
  flashBtn('btn-eth-send', result.ok);
  appendActivity('act-eth', desc, result);
}

// ── Bus publish ───────────────────────────────────────────────────────────────
async function publishBus() {
  const name = document.getElementById('bus-name').value.trim();
  if (!name) { document.getElementById('bus-name').focus(); return; }

  const vt = document.getElementById('bus-vtype').value;
  let value;
  if (vt === 'bool') {
    value = document.getElementById('bus-bool-check').checked ? 'true' : 'false';
  } else {
    value = document.getElementById('bus-value').value.trim();
  }

  const body = {
    address:    gateway,
    name:       name,
    value_type: vt,
    value:      value,
    publisher:  document.getElementById('bus-pub').value.trim() || 'commander',
  };

  const desc = `${name} = ${vt === 'bytes' ? '0x' + value.replace(/[:\s]/g,'') : value}  (${vt})`;

  document.getElementById('btn-bus-pub').disabled = true;
  let result;
  try {
    const r = await fetch('/api/bus/publish', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify(body),
    });
    result = await r.json();
  } catch (e) {
    result = { ok: false, detail: String(e) };
  }
  flashBtn('btn-bus-pub', result.ok);
  appendActivity('act-bus', desc, result);
}

// ── Keyboard shortcuts (Enter to send) ───────────────────────────────────────
document.querySelectorAll('.col').forEach((col, i) => {
  col.querySelectorAll('input').forEach(inp => {
    inp.addEventListener('keydown', e => {
      if (e.key === 'Enter') {
        [sendCan, sendEth, publishBus][i]?.();
      }
    });
  });
});

// ── Helpers ───────────────────────────────────────────────────────────────────
function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ── Nav bar ───────────────────────────────────────────────────────────────────
(function() {
  const h = window.location.hostname;
  const p = window.location.port;
  document.querySelectorAll('.nav-link').forEach(a => {
    a.href = 'http://' + h + ':' + a.dataset.port + '/';
    if (a.dataset.port === p) a.classList.add('active');
  });
})();

// ── Boot ──────────────────────────────────────────────────────────────────────
(async () => {
  await initGateway();
  loadCanBuses();
  loadEthIfaces();
  loadSignals();
})();
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(HTML)


if __name__ == "__main__":
    print(f"BoAt Commander → http://localhost:{_PORT}")
    print(f"Default gateway : {_DEFAULT_GW}")
    uvicorn.run(app, host="0.0.0.0", port=_PORT, log_level="warning")
