"""
BoAt Platform — CAN Trace, Bus Signal Log & Event Log
Run:  python3 demo/dashboard.py
Open: http://localhost:8080
"""
from __future__ import annotations

import sys
import threading
from datetime import datetime
from typing import List

sys.path.insert(0, "/home/testuser/.local/lib/python3.12/site-packages")
sys.path.insert(0, "/home/testuser/ProjectBoat/boat-platform/sdk/python")

import grpc
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from boat.client import BoAtClient
from boat.v1 import bus_pb2, can_pb2, ethernet_pb2

# ── State ──────────────────────────────────────────────────────────────────────

MAX_CAN_FRAMES   = 500
MAX_ETH_FRAMES   = 500
MAX_BUS_SIGNALS  = 500
MAX_LOG_ENTRIES  = 300


class DashboardState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.can_frames: List[dict] = []
        self.eth_frames: List[dict] = []
        self.bus_signals: List[dict] = []
        self.event_log: List[dict] = []
        self._can_stream = None
        self._eth_stream = None
        self._bus_stream = None
        self._can_thread: threading.Thread | None = None
        self._eth_thread: threading.Thread | None = None
        self._bus_thread: threading.Thread | None = None

    def log(self, msg: str, level: str = "info") -> None:
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        with self.lock:
            self.event_log.append({"ts": ts, "msg": msg, "level": level})
            if len(self.event_log) > MAX_LOG_ENTRIES:
                self.event_log.pop(0)

    def push_can_frame(self, frame) -> None:
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        hex_data = frame.data.hex(":").upper() if frame.data else ""
        with self.lock:
            self.can_frames.append({
                "ts": ts,
                "iface": frame.iface or "?",
                "can_id": f"0x{frame.can_id:03X}",
                "dlc": frame.dlc,
                "data": hex_data,
            })
            if len(self.can_frames) > MAX_CAN_FRAMES:
                self.can_frames.pop(0)

    def push_eth_frame(self, frame) -> None:
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        src = frame.src_mac.hex(":") if frame.src_mac else "—"
        dst = frame.dst_mac.hex(":") if frame.dst_mac else "—"
        with self.lock:
            self.eth_frames.append({
                "ts":        ts,
                "iface":     frame.iface or "?",
                "ethertype": f"0x{frame.ethertype:04X}",
                "src_mac":   src,
                "dst_mac":   dst,
                "length":    len(frame.payload),
                "payload":   frame.payload[:16].hex(":").upper(),
            })
            if len(self.eth_frames) > MAX_ETH_FRAMES:
                self.eth_frames.pop(0)

    def push_bus_signal(self, sig) -> None:
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        kind = sig.WhichOneof("value")
        if kind == "number_value":
            val_str = str(sig.number_value)
            val_type = "number"
        elif kind == "string_value":
            val_str = repr(sig.string_value)
            val_type = "string"
        elif kind == "bool_value":
            val_str = str(sig.bool_value)
            val_type = "bool"
        elif kind == "bytes_value":
            val_str = sig.bytes_value.hex(":")
            val_type = "bytes"
        else:
            val_str = "—"
            val_type = "unknown"
        with self.lock:
            self.bus_signals.append({
                "ts": ts,
                "name": sig.name,
                "publisher": sig.publisher or "—",
                "type": val_type,
                "value": val_str,
            })
            if len(self.bus_signals) > MAX_BUS_SIGNALS:
                self.bus_signals.pop(0)

    def start_can_subscribe(self, client: BoAtClient) -> None:
        if self._can_thread and self._can_thread.is_alive():
            return

        def _worker() -> None:
            self.log("CAN subscription started")
            while True:
                try:
                    stream = client.can.SubscribeCanFrames(
                        can_pb2.SubscribeCanFramesRequest(simulation_id="", iface="")
                    )
                    with self.lock:
                        self._can_stream = stream
                    for frame in stream:
                        self.push_can_frame(frame)
                except grpc.RpcError as e:
                    self.log(f"CAN stream lost: {e.code().name} — retrying in 2 s", "warn")
                    import time; time.sleep(2)
                except Exception as e:
                    self.log(f"CAN error: {e}", "error")
                    import time; time.sleep(2)

        self._can_thread = threading.Thread(target=_worker, daemon=True, name="can-sub")
        self._can_thread.start()

    def start_eth_subscribe(self, client: BoAtClient) -> None:
        if self._eth_thread and self._eth_thread.is_alive():
            return

        def _worker() -> None:
            self.log("Ethernet subscription started")
            while True:
                try:
                    stream = client.ethernet.SubscribeFrames(
                        ethernet_pb2.SubscribeEthernetFramesRequest(iface="", ethertype=0)
                    )
                    with self.lock:
                        self._eth_stream = stream
                    for frame in stream:
                        self.push_eth_frame(frame)
                except grpc.RpcError as e:
                    self.log(f"Ethernet stream lost: {e.code().name} — retrying in 2 s", "warn")
                    import time; time.sleep(2)
                except Exception as e:
                    self.log(f"Ethernet error: {e}", "error")
                    import time; time.sleep(2)

        self._eth_thread = threading.Thread(target=_worker, daemon=True, name="eth-sub")
        self._eth_thread.start()

    def start_bus_subscribe(self, client: BoAtClient) -> None:
        if self._bus_thread and self._bus_thread.is_alive():
            return

        def _worker() -> None:
            self.log("Bus signal subscription started")
            while True:
                try:
                    stream = client.bus.Subscribe(
                        bus_pb2.BusSubscribeRequest(names=[])  # empty = all
                    )
                    with self.lock:
                        self._bus_stream = stream
                    for sig in stream:
                        self.push_bus_signal(sig)
                except grpc.RpcError as e:
                    self.log(f"Bus stream lost: {e.code().name} — retrying in 2 s", "warn")
                    import time; time.sleep(2)
                except Exception as e:
                    self.log(f"Bus error: {e}", "error")
                    import time; time.sleep(2)

        self._bus_thread = threading.Thread(target=_worker, daemon=True, name="bus-sub")
        self._bus_thread.start()


dash = DashboardState()
client = BoAtClient("localhost:50051")
app = FastAPI()

# Start subscribing immediately at boot
dash.start_can_subscribe(client)
dash.start_eth_subscribe(client)
dash.start_bus_subscribe(client)

# ── REST API ──────────────────────────────────────────────────────────────────


@app.get("/api/can")
def api_can(since: int = 0):
    """Return CAN frames newer than index `since`."""
    with dash.lock:
        frames = list(dash.can_frames)
    return {"frames": frames[since:], "total": len(frames)}


@app.post("/api/can/clear")
def api_can_clear():
    with dash.lock:
        dash.can_frames.clear()
    return {"ok": True}


@app.get("/api/eth")
def api_eth(since: int = 0):
    with dash.lock:
        frames = list(dash.eth_frames)
    return {"frames": frames[since:], "total": len(frames)}


@app.post("/api/eth/clear")
def api_eth_clear():
    with dash.lock:
        dash.eth_frames.clear()
    return {"ok": True}


@app.get("/api/bus")
def api_bus(since: int = 0):
    with dash.lock:
        sigs = list(dash.bus_signals)
    return {"signals": sigs[since:], "total": len(sigs)}


@app.post("/api/bus/clear")
def api_bus_clear():
    with dash.lock:
        dash.bus_signals.clear()
    return {"ok": True}


@app.get("/api/log")
def api_log(since: int = 0):
    with dash.lock:
        entries = list(dash.event_log)
    return {"entries": entries[since:], "total": len(entries)}


# ── HTML ──────────────────────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>BoAt — Live Monitor</title>

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
  .logo { font-weight: 700; font-size: 15px; color: var(--blue); letter-spacing: .4px; }
  .subtitle { color: var(--muted); font-size: 12px; }
  header .spacer { flex: 1; }
  .gw-badge {
    font-size: 11px; padding: 2px 10px; border-radius: 12px;
    background: #1f3a1f; color: var(--green); border: 1px solid #2ea043;
  }

  /* ── Main layout: three columns ── */
  .layout {
    display: flex;
    height: calc(100vh - 78px);
    overflow: hidden;
  }

  /* Left column: CAN trace */
  .col-left {
    flex: 0 0 35%;
    display: flex;
    flex-direction: column;
    border-right: 1px solid var(--border);
    min-height: 0;
  }

  /* Middle column: Ethernet trace */
  .col-mid {
    flex: 0 0 35%;
    display: flex;
    flex-direction: column;
    border-right: 1px solid var(--border);
    min-height: 0;
  }

  /* Right column: Bus signals (top) + Event log (bottom) */
  .col-right {
    flex: 1;
    display: flex;
    flex-direction: column;
    min-height: 0;
  }

  /* ── Shared pane chrome ── */
  .pane {
    display: flex;
    flex-direction: column;
    min-height: 0;
  }
  .pane-header {
    height: 36px;
    padding: 0 12px;
    display: flex;
    align-items: center;
    gap: 8px;
    border-bottom: 1px solid var(--border);
    background: var(--panel);
    flex-shrink: 0;
  }
  .pane-title {
    font-size: 11px; font-weight: 600;
    text-transform: uppercase; letter-spacing: .8px;
    color: var(--muted);
  }
  .live-dot {
    width: 6px; height: 6px; border-radius: 50%;
    background: var(--green);
    animation: pulse 2s infinite;
    flex-shrink: 0;
  }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.3} }
  .pane-spacer { flex: 1; }

  .filter-input {
    background: var(--bg);
    border: 1px solid var(--border);
    color: var(--text);
    border-radius: 4px;
    padding: 3px 7px;
    font-size: 11px;
    font-family: var(--mono);
    width: 130px;
    outline: none;
  }
  .filter-input:focus { border-color: var(--blue); }
  .frame-count { font-size: 11px; color: var(--muted); font-family: var(--mono); }

  .btn-small {
    font-size: 11px; padding: 2px 8px;
    background: var(--bg); border: 1px solid var(--border);
    color: var(--muted); border-radius: 4px; cursor: pointer;
  }
  .btn-small:hover { background: #21262d; color: var(--text); }

  /* scrollable table area */
  .tbl-scroll {
    flex: 1;
    overflow-y: auto;
    min-height: 0;
  }

  table { width: 100%; border-collapse: collapse; }
  thead th {
    position: sticky; top: 0;
    background: #1c2128;
    border-bottom: 1px solid var(--border);
    padding: 5px 10px;
    text-align: left;
    font-size: 10px; font-weight: 600;
    text-transform: uppercase; letter-spacing: .6px;
    color: var(--muted); z-index: 1;
  }
  tbody tr {
    border-bottom: 1px solid rgba(48,54,61,.35);
    transition: background .1s;
  }
  tbody tr:hover { background: #1c2128; }
  td {
    padding: 4px 10px;
    font-family: var(--mono);
    font-size: 12px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 220px;
  }

  /* CAN columns */
  .td-ts    { color: var(--muted); width: 84px; }
  .td-iface { width: 68px; }
  .td-id    { width: 68px; }
  .td-dlc   { width: 36px; color: var(--muted); text-align: center; }
  .td-data  { color: var(--green); letter-spacing: .5px; }

  /* Bus signal columns */
  .td-name  { color: var(--blue); }
  .td-pub   { color: var(--muted); width: 120px; }
  .td-type  { width: 54px; }
  .td-val   { }

  .iface-pill {
    display: inline-block; padding: 1px 7px;
    border-radius: 10px; font-size: 10px; font-weight: 600;
  }

  /* value type badges */
  .type-number  { color: var(--blue); }
  .type-string  { color: var(--green); }
  .type-bool    { color: var(--yellow); }
  .type-bytes   { color: var(--purple); }
  .type-unknown { color: var(--muted); }

  @keyframes rowIn {
    from { background: rgba(88,166,255,.12); }
    to   { background: transparent; }
  }
  .row-new { animation: rowIn .8s ease-out forwards; }

  /* ── Bus pane — top half of right col ── */
  .bus-pane { flex: 1; border-bottom: 1px solid var(--border); }

  /* ── Event log pane ── */
  .log-pane { height: 180px; flex-shrink: 0; background: var(--panel); }
  .log-scroll {
    flex: 1; overflow-y: auto;
    padding: 4px 12px 8px;
    font-family: var(--mono); font-size: 11px;
    min-height: 0;
  }
  .log-entry { display: flex; gap: 10px; padding: 2px 0; border-bottom: 1px solid rgba(48,54,61,.3); }
  .log-ts    { color: var(--muted); flex-shrink: 0; width: 80px; }
  .log-info  { color: var(--text); }
  .log-warn  { color: var(--yellow); }
  .log-error { color: var(--red); }

  ::-webkit-scrollbar { width: 4px; height: 4px; }
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

  /* ── Bus signal live-view ── */
  @keyframes flashVal {
    0%   { background: rgba(88,166,255,.25); }
    100% { background: transparent; }
  }
  .val-flash { animation: flashVal .6s ease-out; }
  .td-upd { color: var(--muted); width: 80px; font-size: 11px; }
</style>
</head>
<body>

<header>
  <span class="logo">⛵ BoAt</span>
  <span class="subtitle">Live Monitor</span>
  <span class="spacer"></span>
  <span class="gw-badge">● gateway :50051</span>
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

  <!-- ══ LEFT — CAN trace ══ -->
  <div class="col-left pane">
    <div class="pane-header">
      <div class="live-dot"></div>
      <span class="pane-title">CAN Frames</span>
      <span class="frame-count" id="frame-count">0</span>
      <div class="pane-spacer"></div>
      <input class="filter-input" id="filter-id"    placeholder="ID e.g. 0x123" oninput="applyCanFilter()"/>
      <input class="filter-input" id="filter-iface" placeholder="iface e.g. vcan1" oninput="applyCanFilter()"/>
      <button class="btn-small" onclick="clearFrames()">Clear</button>
      <button class="btn-small" id="btn-pause" onclick="togglePause()">⏸ Pause</button>
    </div>
    <div class="tbl-scroll" id="can-scroll">
      <table>
        <thead>
          <tr>
            <th class="td-ts">Time</th>
            <th class="td-iface">Iface</th>
            <th class="td-id">CAN ID</th>
            <th class="td-dlc">DLC</th>
            <th class="td-data">Data</th>
          </tr>
        </thead>
        <tbody id="can-tbody"></tbody>
      </table>
    </div>
  </div>

  <!-- ══ MIDDLE — Ethernet trace ══ -->
  <div class="col-mid pane">
    <div class="pane-header">
      <div class="live-dot"></div>
      <span class="pane-title">Ethernet Frames</span>
      <span class="frame-count" id="eth-count">0</span>
      <div class="pane-spacer"></div>
      <input class="filter-input" id="filter-eth-iface"     placeholder="iface e.g. veth0"   oninput="applyEthFilter()"/>
      <input class="filter-input" id="filter-eth-ethertype" placeholder="type e.g. 0x0800"   oninput="applyEthFilter()"/>
      <button class="btn-small" onclick="clearEth()">Clear</button>
      <button class="btn-small" id="btn-eth-pause" onclick="toggleEthPause()">⏸ Pause</button>
    </div>
    <div class="tbl-scroll" id="eth-scroll">
      <table>
        <thead>
          <tr>
            <th class="td-ts">Time</th>
            <th class="td-iface">Iface</th>
            <th style="width:60px">Type</th>
            <th style="width:36px;text-align:center">Len</th>
            <th>Payload (first 16 B)</th>
          </tr>
        </thead>
        <tbody id="eth-tbody"></tbody>
      </table>
    </div>
  </div>

  <!-- ══ RIGHT column ══ -->
  <div class="col-right">

    <!-- ── Bus signal log ── -->
    <div class="pane bus-pane">
      <div class="pane-header">
        <div class="live-dot"></div>
        <span class="pane-title">Bus Signals</span>
        <span class="frame-count" id="bus-count">0</span>
        <div class="pane-spacer"></div>
        <input class="filter-input" id="filter-bus-name" placeholder="signal name" oninput="applyBusFilter()"/>
        <button class="btn-small" id="btn-bus-liveview" onclick="toggleBusLiveView()" title="Toggle between feed and per-signal live table">Live View</button>
        <button class="btn-small" onclick="clearBus()">Clear</button>
        <button class="btn-small" id="btn-bus-pause" onclick="toggleBusPause()">⏸ Pause</button>
      </div>
      <!-- scrolling feed -->
      <div class="tbl-scroll" id="bus-scroll">
        <table>
          <thead>
            <tr>
              <th class="td-ts">Time</th>
              <th class="td-name">Signal Name</th>
              <th class="td-pub">Publisher</th>
              <th class="td-type">Type</th>
              <th class="td-val">Value</th>
            </tr>
          </thead>
          <tbody id="bus-tbody"></tbody>
        </table>
      </div>
      <!-- live value table (hidden by default) -->
      <div class="tbl-scroll" id="bus-live-scroll" style="display:none">
        <table>
          <thead>
            <tr>
              <th class="td-name">Signal Name</th>
              <th class="td-pub">Publisher</th>
              <th class="td-type">Type</th>
              <th class="td-val">Value</th>
              <th class="td-upd">Updated</th>
            </tr>
          </thead>
          <tbody id="bus-live-tbody"></tbody>
        </table>
      </div>
    </div>

    <!-- ── Event log ── -->
    <div class="pane log-pane">
      <div class="pane-header">
        <div class="live-dot"></div>
        <span class="pane-title">Event Log</span>
        <div class="pane-spacer"></div>
        <button class="btn-small" onclick="clearLog()">Clear</button>
      </div>
      <div class="log-scroll" id="log-scroll"></div>
    </div>

  </div><!-- end col-right -->
</div><!-- end layout -->

<script>
// ── CAN state ─────────────────────────────────────────────────────────────────
let allFrames   = [];
let canSince    = 0;
let canPaused   = false;
let filterIdStr = "";
let filterIface = "";

// ── Bus state ─────────────────────────────────────────────────────────────────
let allSignals   = [];
let busSince     = 0;
let busPaused    = false;
let filterBusName = "";

let logSince = 0;

// ── iface colour palette ──────────────────────────────────────────────────────
const IFACE_COLORS = [
  { bg: "#1c3a5c", fg: "#58a6ff" },
  { bg: "#1f3a1f", fg: "#3fb950" },
  { bg: "#3a1c3a", fg: "#d2a8ff" },
  { bg: "#3a2a00", fg: "#ffa657" },
  { bg: "#3a1c1c", fg: "#f78166" },
];
const ifaceColorIdx = {};
let nextIfaceColor = 0;
function ifaceColor(iface) {
  if (!(iface in ifaceColorIdx)) ifaceColorIdx[iface] = nextIfaceColor++ % IFACE_COLORS.length;
  return IFACE_COLORS[ifaceColorIdx[iface]];
}

// ── CAN ───────────────────────────────────────────────────────────────────────
let canFetching = false;
function fetchCan() {
  if (canFetching) return;
  canFetching = true;
  fetch('/api/can?since=' + canSince)
    .then(r => r.json())
    .then(d => {
      canFetching = false;
      if (!d.frames.length) return;
      canSince = d.total;
      allFrames.push(...d.frames);
      if (allFrames.length > 500) allFrames.splice(0, allFrames.length - 500);
      if (!canPaused) renderCanFrames(d.frames);
    }).catch(() => { canFetching = false; });
}

function matchesCanFilter(f) {
  if (filterIdStr && f.can_id.toLowerCase().indexOf(filterIdStr) === -1) return false;
  if (filterIface && f.iface.toLowerCase().indexOf(filterIface) === -1)   return false;
  return true;
}

function renderCanFrames(frames) {
  const tbody  = document.getElementById('can-tbody');
  const scroll = document.getElementById('can-scroll');
  const atBottom = scroll.scrollHeight - scroll.clientHeight - scroll.scrollTop < 60;
  for (const f of frames) {
    if (!matchesCanFilter(f)) continue;
    const c = ifaceColor(f.iface);
    const tr = document.createElement('tr');
    tr.className = 'row-new';
    tr.innerHTML =
      `<td class="td-ts">${f.ts}</td>` +
      `<td class="td-iface"><span class="iface-pill" style="background:${c.bg};color:${c.fg}">${f.iface}</span></td>` +
      `<td class="td-id" style="color:var(--blue)">${f.can_id}</td>` +
      `<td class="td-dlc">${f.dlc}</td>` +
      `<td class="td-data">${f.data || '—'}</td>`;
    tbody.appendChild(tr);
  }
  while (tbody.rows.length > 300) tbody.deleteRow(0);
  document.getElementById('frame-count').textContent = allFrames.length;
  if (atBottom) scroll.scrollTop = scroll.scrollHeight;
}

function applyCanFilter() {
  filterIdStr = document.getElementById('filter-id').value.trim().toLowerCase();
  filterIface = document.getElementById('filter-iface').value.trim().toLowerCase();
  document.getElementById('can-tbody').innerHTML = '';
  renderCanFrames(allFrames.filter(matchesCanFilter));
}

function clearFrames() {
  fetch('/api/can/clear', { method: 'POST' }).then(() => {
    allFrames = []; canSince = 0;
    document.getElementById('can-tbody').innerHTML = '';
    document.getElementById('frame-count').textContent = '0';
  }).catch(() => {});
}

function togglePause() {
  canPaused = !canPaused;
  document.getElementById('btn-pause').textContent = canPaused ? '▶ Resume' : '⏸ Pause';
  if (!canPaused) {
    document.getElementById('can-tbody').innerHTML = '';
    renderCanFrames(allFrames);
  }
}

// ── Ethernet frames ───────────────────────────────────────────────────────────
let allEthFrames   = [];
let ethSince       = 0;
let ethPaused      = false;
let filterEthIface = "";
let filterEthType  = "";

let ethFetching = false;
function fetchEth() {
  if (ethFetching) return;
  ethFetching = true;
  fetch('/api/eth?since=' + ethSince)
    .then(r => r.json())
    .then(d => {
      ethFetching = false;
      if (!d.frames.length) return;
      ethSince = d.total;
      allEthFrames.push(...d.frames);
      if (allEthFrames.length > 500) allEthFrames.splice(0, allEthFrames.length - 500);
      if (!ethPaused) renderEthFrames(d.frames);
    }).catch(() => { ethFetching = false; });
}

function matchesEthFilter(f) {
  if (filterEthIface && f.iface.toLowerCase().indexOf(filterEthIface) === -1) return false;
  if (filterEthType  && f.ethertype.toLowerCase().indexOf(filterEthType) === -1) return false;
  return true;
}

function renderEthFrames(frames) {
  const tbody  = document.getElementById('eth-tbody');
  const scroll = document.getElementById('eth-scroll');
  const atBottom = scroll.scrollHeight - scroll.clientHeight - scroll.scrollTop < 60;
  for (const f of frames) {
    if (!matchesEthFilter(f)) continue;
    const c = ifaceColor(f.iface);
    const tr = document.createElement('tr');
    tr.className = 'row-new';
    tr.innerHTML =
      `<td class="td-ts">${f.ts}</td>` +
      `<td class="td-iface"><span class="iface-pill" style="background:${c.bg};color:${c.fg}">${f.iface}</span></td>` +
      `<td style="color:var(--orange);font-family:var(--mono);font-size:12px">${f.ethertype}</td>` +
      `<td style="text-align:center;color:var(--muted);font-family:var(--mono);font-size:12px">${f.length}</td>` +
      `<td style="color:var(--purple);font-family:var(--mono);font-size:12px;letter-spacing:.4px">${f.payload || '—'}</td>`;
    tbody.appendChild(tr);
  }
  while (tbody.rows.length > 300) tbody.deleteRow(0);
  document.getElementById('eth-count').textContent = allEthFrames.length;
  if (atBottom) scroll.scrollTop = scroll.scrollHeight;
}

function applyEthFilter() {
  filterEthIface = document.getElementById('filter-eth-iface').value.trim().toLowerCase();
  filterEthType  = document.getElementById('filter-eth-ethertype').value.trim().toLowerCase();
  document.getElementById('eth-tbody').innerHTML = '';
  renderEthFrames(allEthFrames.filter(matchesEthFilter));
}

function clearEth() {
  fetch('/api/eth/clear', { method: 'POST' }).then(() => {
    allEthFrames = []; ethSince = 0;
    document.getElementById('eth-tbody').innerHTML = '';
    document.getElementById('eth-count').textContent = '0';
  }).catch(() => {});
}

function toggleEthPause() {
  ethPaused = !ethPaused;
  document.getElementById('btn-eth-pause').textContent = ethPaused ? '▶ Resume' : '⏸ Pause';
  if (!ethPaused) {
    document.getElementById('eth-tbody').innerHTML = '';
    renderEthFrames(allEthFrames);
  }
}

// ── Bus signals ───────────────────────────────────────────────────────────────
let busLiveView = false;
const busLiveMap = {};   // name → {type, value, publisher, ts, rowEl, valEl}

let busFetching = false;
function fetchBus() {
  if (busFetching) return;
  busFetching = true;
  fetch('/api/bus?since=' + busSince)
    .then(r => r.json())
    .then(d => {
      busFetching = false;
      if (!d.signals.length) return;
      busSince = d.total;
      allSignals.push(...d.signals);
      if (allSignals.length > 500) allSignals.splice(0, allSignals.length - 500);
      if (!busPaused) {
        if (busLiveView) updateBusLiveView(d.signals);
        else renderBusSignals(d.signals);
      }
    }).catch(() => { busFetching = false; });
}

function matchesBusFilter(s) {
  if (filterBusName && s.name.toLowerCase().indexOf(filterBusName) === -1) return false;
  return true;
}

function renderBusSignals(signals) {
  const tbody  = document.getElementById('bus-tbody');
  const scroll = document.getElementById('bus-scroll');
  const atBottom = scroll.scrollHeight - scroll.clientHeight - scroll.scrollTop < 60;
  for (const s of signals) {
    if (!matchesBusFilter(s)) continue;
    const tr = document.createElement('tr');
    tr.className = 'row-new';
    tr.innerHTML =
      `<td class="td-ts">${s.ts}</td>` +
      `<td class="td-name">${s.name}</td>` +
      `<td class="td-pub">${s.publisher}</td>` +
      `<td class="td-type"><span class="type-${s.type}">${s.type}</span></td>` +
      `<td class="td-val type-${s.type}">${s.value}</td>`;
    tbody.appendChild(tr);
  }
  while (tbody.rows.length > 300) tbody.deleteRow(0);
  document.getElementById('bus-count').textContent = allSignals.length;
  if (atBottom) scroll.scrollTop = scroll.scrollHeight;
}

function updateBusLiveView(signals) {
  const tbody = document.getElementById('bus-live-tbody');
  for (const s of signals) {
    if (!matchesBusFilter(s)) continue;
    if (busLiveMap[s.name]) {
      // update existing row
      const entry = busLiveMap[s.name];
      entry.valEl.textContent = s.value;
      entry.valEl.className   = `td-val type-${s.type}`;
      entry.updEl.textContent = s.ts;
      // flash animation — re-trigger by removing/adding class
      entry.valEl.classList.remove('val-flash');
      void entry.valEl.offsetWidth;  // reflow
      entry.valEl.classList.add('val-flash');
    } else {
      // new signal — insert sorted
      const tr = document.createElement('tr');
      const valEl = document.createElement('td');
      valEl.className = `td-val type-${s.type} val-flash`;
      valEl.textContent = s.value;
      const updEl = document.createElement('td');
      updEl.className = 'td-upd';
      updEl.textContent = s.ts;
      tr.innerHTML =
        `<td class="td-name">${s.name}</td>` +
        `<td class="td-pub">${s.publisher}</td>` +
        `<td class="td-type"><span class="type-${s.type}">${s.type}</span></td>`;
      tr.appendChild(valEl);
      tr.appendChild(updEl);
      // insert in alphabetical order
      let inserted = false;
      for (const row of tbody.rows) {
        if (row.cells[0].textContent > s.name) {
          tbody.insertBefore(tr, row);
          inserted = true; break;
        }
      }
      if (!inserted) tbody.appendChild(tr);
      busLiveMap[s.name] = {valEl, updEl};
    }
  }
  document.getElementById('bus-count').textContent = Object.keys(busLiveMap).length;
}

function toggleBusLiveView() {
  busLiveView = !busLiveView;
  const btn = document.getElementById('btn-bus-liveview');
  const feed = document.getElementById('bus-scroll');
  const live = document.getElementById('bus-live-scroll');
  if (busLiveView) {
    btn.style.color = 'var(--blue)';
    btn.style.borderColor = 'var(--blue)';
    feed.style.display = 'none';
    live.style.display = '';
    // populate live view from accumulated data
    document.getElementById('bus-live-tbody').innerHTML = '';
    for (const k in busLiveMap) delete busLiveMap[k];
    updateBusLiveView(allSignals.filter(matchesBusFilter));
  } else {
    btn.style.color = '';
    btn.style.borderColor = '';
    live.style.display = 'none';
    feed.style.display = '';
  }
}

function applyBusFilter() {
  filterBusName = document.getElementById('filter-bus-name').value.trim().toLowerCase();
  if (busLiveView) {
    document.getElementById('bus-live-tbody').innerHTML = '';
    for (const k in busLiveMap) delete busLiveMap[k];
    updateBusLiveView(allSignals);
  } else {
    document.getElementById('bus-tbody').innerHTML = '';
    renderBusSignals(allSignals.filter(matchesBusFilter));
  }
}

function clearBus() {
  fetch('/api/bus/clear', { method: 'POST' }).then(() => {
    allSignals = []; busSince = 0;
    document.getElementById('bus-tbody').innerHTML = '';
    document.getElementById('bus-live-tbody').innerHTML = '';
    for (const k in busLiveMap) delete busLiveMap[k];
    document.getElementById('bus-count').textContent = '0';
  }).catch(() => {});
}

function toggleBusPause() {
  busPaused = !busPaused;
  document.getElementById('btn-bus-pause').textContent = busPaused ? '▶ Resume' : '⏸ Pause';
  if (!busPaused) {
    if (!busLiveView) {
      document.getElementById('bus-tbody').innerHTML = '';
      renderBusSignals(allSignals);
    }
  }
}

// ── Event log ─────────────────────────────────────────────────────────────────
let logFetching = false;
function fetchLog() {
  if (logFetching) return;
  logFetching = true;
  fetch('/api/log?since=' + logSince)
    .then(r => r.json())
    .then(d => {
      logFetching = false;
      if (!d.entries.length) return;
      logSince = d.total;
      appendLog(d.entries);
    }).catch(() => { logFetching = false; });
}

function appendLog(entries) {
  const el = document.getElementById('log-scroll');
  const atBottom = el.scrollHeight - el.clientHeight - el.scrollTop < 40;
  for (const e of entries) {
    const row = document.createElement('div');
    row.className = 'log-entry';
    row.innerHTML = `<span class="log-ts">${e.ts}</span><span class="log-${e.level}">${e.msg}</span>`;
    el.appendChild(row);
  }
  while (el.children.length > 200) el.removeChild(el.firstChild);
  if (atBottom) el.scrollTop = el.scrollHeight;
}

function clearLog() {
  document.getElementById('log-scroll').innerHTML = '';
  logSince = 0;
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

// ── boot ──────────────────────────────────────────────────────────────────────
setInterval(fetchCan, 150);
setInterval(fetchEth, 150);
setInterval(fetchBus, 150);
setInterval(fetchLog, 500);
fetchCan(); fetchEth(); fetchBus(); fetchLog();
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(HTML)


if __name__ == "__main__":
    import os
    port = int(os.environ.get("BOAT_DASH_PORT", "8080"))
    print(f"BoAt Live Monitor → http://localhost:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
