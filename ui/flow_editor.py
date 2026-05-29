"""
BoAt Platform — Visual Flow Editor
Run:  python3 demo/flow_editor.py
Open: http://localhost:8085
"""
from __future__ import annotations
import json
import subprocess
import sys
import threading
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "boat-platform" / "sdk" / "python"))
import grpc
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from boat.client import BoAtClient
from boat.v1 import can_pb2, ethernet_pb2
_PORT      = 8085
_FLOWS_DIR = Path(__file__).parent / "flows"
_FLOWS_DIR.mkdir(exist_ok=True)
_EXECUTOR  = Path(__file__).parent / "flow_executor.py"
# ── subprocess registry ───────────────────────────────────────────────────────
# flow_id → {"proc": Popen, "logs": deque, "seq": int}
_running: dict[str, dict] = {}
_reg_lock = threading.Lock()
def _start_executor(flow_id: str, flow_file: Path) -> None:
    with _reg_lock:
        entry = _running.get(flow_id)
        if entry and entry["proc"].poll() is None:
            entry["proc"].terminate()
        logs: deque = deque(maxlen=500)
        proc = subprocess.Popen(
            [sys.executable, str(_EXECUTOR), str(flow_file)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        _running[flow_id] = {"proc": proc, "logs": logs, "seq": 0}
    def _reader() -> None:
        for line in proc.stdout:
            with _reg_lock:
                entry = _running.get(flow_id)
                if entry:
                    entry["logs"].append(line.rstrip())
                    entry["seq"] += 1
    threading.Thread(target=_reader, daemon=True).start()
def _stop_executor(flow_id: str) -> None:
    with _reg_lock:
        entry = _running.pop(flow_id, None)
    if entry and entry["proc"].poll() is None:
        entry["proc"].terminate()
def _flow_status(flow_id: str) -> str:
    with _reg_lock:
        entry = _running.get(flow_id)
    if not entry:
        return "stopped"
    return "running" if entry["proc"].poll() is None else "stopped"
# ── persistence ───────────────────────────────────────────────────────────────
def _list_flows() -> list[dict]:
    out = []
    for f in sorted(_FLOWS_DIR.glob("*.json")):
        try:
            meta = json.loads(f.read_text()).get("meta", {})
            out.append({
                "id":     meta.get("id", f.stem),
                "name":   meta.get("name", f.stem),
                "status": _flow_status(meta.get("id", f.stem)),
            })
        except Exception:
            pass
    return out
# ── FastAPI ───────────────────────────────────────────────────────────────────
app = FastAPI()
@app.get("/api/flows")
def api_list():
    return {"flows": _list_flows()}
@app.post("/api/flows")
def api_create(body: dict):
    flow_id = str(uuid.uuid4())[:8]
    name    = body.get("name", "New Flow")
    flow    = {
        "meta":     {"id": flow_id, "name": name,
                     "created": datetime.now().isoformat(timespec="seconds")},
        "drawflow": {"Home": {"data": {}}},
    }
    (_FLOWS_DIR / f"{flow_id}.json").write_text(json.dumps(flow, indent=2))
    return {"id": flow_id, "name": name}
@app.get("/api/flows/{flow_id}")
def api_get(flow_id: str):
    f = _FLOWS_DIR / f"{flow_id}.json"
    if not f.exists():
        raise HTTPException(404, "Flow not found")
    data = json.loads(f.read_text())
    # Normalise legacy double-nested format on read.
    df = data.get("drawflow", {})
    if "drawflow" in df:
        data["drawflow"] = df["drawflow"]
    return JSONResponse(data)
@app.put("/api/flows/{flow_id}")
def api_save(flow_id: str, body: dict):
    f = _FLOWS_DIR / f"{flow_id}.json"
    if not f.exists():
        raise HTTPException(404, "Flow not found")
    existing = json.loads(f.read_text())
    existing["drawflow"] = body.get("drawflow", existing["drawflow"])
    if "name" in body:
        existing["meta"]["name"] = body["name"]
    f.write_text(json.dumps(existing, indent=2))
    return {"ok": True}
@app.delete("/api/flows/{flow_id}")
def api_delete(flow_id: str):
    _stop_executor(flow_id)
    f = _FLOWS_DIR / f"{flow_id}.json"
    if f.exists():
        f.unlink()
    return {"ok": True}
@app.post("/api/flows/{flow_id}/deploy")
def api_deploy(flow_id: str):
    f = _FLOWS_DIR / f"{flow_id}.json"
    if not f.exists():
        raise HTTPException(404, "Flow not found")
    _start_executor(flow_id, f)
    return {"ok": True, "status": "running"}
@app.post("/api/flows/{flow_id}/stop")
def api_stop(flow_id: str):
    _stop_executor(flow_id)
    return {"ok": True, "status": "stopped"}
@app.get("/api/flows/{flow_id}/status")
def api_status(flow_id: str):
    return {"status": _flow_status(flow_id)}
@app.get("/api/flows/{flow_id}/log")
def api_log(flow_id: str, since: int = 0):
    with _reg_lock:
        entry = _running.get(flow_id)
    if not entry:
        return {"lines": [], "seq": 0}
    logs   = list(entry["logs"])
    total  = entry["seq"]
    offset = max(0, len(logs) - max(0, total - since))
    return {"lines": logs[offset:], "seq": total}
@app.get("/api/gateway/health")
def api_gw_health():
    try:
        c = BoAtClient("localhost:50051")
        c.can.ListBuses(can_pb2.ListBusesRequest())
        return {"running": True}
    except Exception:
        return {"running": False}

@app.get("/api/gateway/can-buses")
def api_gw_can_buses():
    try:
        c = BoAtClient("localhost:50051")
        r = c.can.ListBuses(can_pb2.ListBusesRequest())
        return {"ifaces": list(r.ifaces)}
    except Exception:
        return {"ifaces": []}
@app.get("/api/gateway/eth-ifaces")
def api_gw_eth_ifaces():
    try:
        c = BoAtClient("localhost:50051")
        r = c.ethernet.ListInterfaces(ethernet_pb2.ListEthernetInterfacesRequest())
        return {"ifaces": list(r.ifaces)}
    except Exception:
        return {"ifaces": []}
@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(_HTML)
# ── HTML ──────────────────────────────────────────────────────────────────────
_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<title>BoAt Flow Editor</title>
<link rel="stylesheet"
  href="https://cdn.jsdelivr.net/gh/jerosoler/Drawflow@0.0.59/dist/drawflow.min.css"/>
<style>
:root {
  --bg:      #0d1117; --surface: #161b22; --border: #30363d;
  --text:    #e6edf3; --muted:   #8b949e;
  --blue:    #58a6ff; --green:   #3fb950; --yellow: #d29922;
  --orange:  #f0883e; --red:     #f85149; --purple: #bc8cff;
  --src:     #58a6ff; --proc:    #d29922; --sink:   #3fb950;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { height: 100%; background: var(--bg); color: var(--text);
             font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
/* ── layout ── */
header { height: 46px; background: var(--surface); border-bottom: 1px solid var(--border);
         display: flex; align-items: center; padding: 0 16px; gap: 12px; }
.logo  { font-size: 16px; font-weight: 700; }
.spacer { flex: 1; }
#panel-nav { height: 32px; background: var(--bg); border-bottom: 1px solid var(--border);
             display: flex; align-items: center; padding: 0 16px; gap: 2px; }
#panel-nav a { font-size: 11px; color: var(--muted); text-decoration: none;
               padding: 3px 11px; border-radius: 4px; transition: background .12s, color .12s; }
#panel-nav a:hover  { background: #21262d; color: var(--text); }
#panel-nav a.active { color: var(--blue); background: rgba(88,166,255,.10); font-weight: 600; }
.main-row { display: flex; height: calc(100vh - 46px - 32px); }
/* ── sidebar ── */
#sidebar { width: 220px; background: var(--surface); border-right: 1px solid var(--border);
           display: flex; flex-direction: column; flex-shrink: 0; overflow: hidden; }
#flow-list-panel { border-bottom: 1px solid var(--border); flex-shrink: 0; }
.sidebar-hdr { padding: 6px 10px; font-size: 10px; color: var(--muted); text-transform: uppercase;
               letter-spacing: .06em; display: flex; align-items: center; gap: 4px; }
.btn-xs { padding: 1px 6px; border-radius: 3px; border: 1px solid var(--border);
          background: transparent; color: var(--muted); cursor: pointer; font-size: 10px;
          font-family: inherit; margin-left: auto; }
.btn-xs:hover { border-color: var(--blue); color: var(--blue); }
#flow-list { max-height: 130px; overflow-y: auto; }
.flow-item { padding: 4px 10px; cursor: pointer; display: flex; align-items: center; gap: 6px;
             border-left: 2px solid transparent; }
.flow-item:hover { background: rgba(255,255,255,.04); }
.flow-item.active { border-left-color: var(--blue); background: rgba(88,166,255,.07); }
.flow-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--muted); flex-shrink: 0; }
.flow-dot.running { background: var(--green); }
.flow-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
#palette { flex: 1; overflow-y: auto; }
.pal-section { font-size: 10px; color: var(--muted); text-transform: uppercase; letter-spacing: .06em;
               padding: 6px 10px 2px; }
.pal-node { padding: 5px 10px; cursor: grab; display: flex; align-items: center; gap: 7px;
            border-radius: 3px; margin: 1px 4px; }
.pal-node:hover { background: rgba(255,255,255,.06); }
.pal-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
/* ── properties panel (right column) ── */
#props-panel { width: 240px; background: var(--surface); border-left: 1px solid var(--border);
               display: flex; flex-direction: column; flex-shrink: 0; overflow-y: auto; }
#props-content { flex: 1; }
.prop-hdr  { display: flex; justify-content: space-between; align-items: baseline;
             padding: 8px 10px 4px; }
.prop-title { font-size: 12px; font-weight: 700; color: var(--text); }
.prop-cat   { font-size: 10px; }
.prop-desc  { font-size: 10px; color: var(--muted); padding: 0 10px 6px; line-height: 1.55; }
/* config field rows */
.prop-section { font-size: 9px; color: var(--muted); text-transform: uppercase;
                letter-spacing: .07em; padding: 5px 10px 3px;
                border-top: 1px solid var(--border); margin-top: 2px; }
.prop-fields  { padding: 2px 10px 6px; display: flex; flex-direction: column; gap: 5px; }
.prop-row     { display: flex; flex-direction: column; gap: 2px; }
.prop-key     { font-size: 10px; color: var(--muted); cursor: default; }
.prop-val     { font-size: 11px; color: var(--text); word-break: break-all;
                background: var(--bg); border-radius: 3px; padding: 2px 5px; min-height: 18px; }
.prop-val.empty { color: var(--muted); font-style: italic; }
/* message shape */
.prop-shape   { padding: 4px 10px 10px; display: flex; flex-direction: column; gap: 2px; }
.psr          { display: flex; gap: 6px; align-items: baseline; }
.psr.psi      { padding-left: 12px; }
.psk          { font-size: 10px; color: var(--blue); flex-shrink: 0; min-width: 72px; }
.psk.muted    { color: var(--muted); }
.psv          { font-size: 10px; color: var(--muted); }
.psp          { font-size: 10px; color: var(--text); }   /* port label */
.prop-empty   { font-size: 10px; color: var(--muted); padding: 14px 10px; line-height: 1.6; }
/* ── canvas + toolbar ── */
#canvas-col { flex: 1; display: flex; flex-direction: column; min-width: 0; }
#toolbar { height: 38px; background: var(--surface); border-bottom: 1px solid var(--border);
           display: flex; align-items: center; gap: 6px; padding: 0 10px; flex-shrink: 0; }
#flow-name-input { background: transparent; border: 1px solid transparent; color: var(--text);
                   font-family: inherit; font-size: 13px; font-weight: 600; padding: 2px 6px;
                   border-radius: 3px; width: 200px; }
#flow-name-input:focus { outline: none; border-color: var(--blue); }
.tb-sep { width: 1px; height: 20px; background: var(--border); }
.tb-btn { padding: 3px 10px; border-radius: 4px; border: 1px solid var(--border);
          background: transparent; color: var(--muted); cursor: pointer; font-size: 11px;
          font-family: inherit; transition: all .12s; }
.tb-btn:hover { border-color: var(--text); color: var(--text); }
.tb-btn.green { border-color: var(--green); color: var(--green); }
.tb-btn.green:hover { background: rgba(63,185,80,.10); }
.tb-btn.red   { border-color: var(--red); color: var(--red); }
.tb-btn.red:hover { background: rgba(248,81,73,.10); }
#status-badge { font-size: 10px; margin-left: auto; }
.badge-stopped { color: var(--muted); }
.badge-running { color: var(--green); }
#drawflow-wrap { flex: 1; position: relative; overflow: hidden; }
#drawflow { width: 100%; height: 100%; background: var(--bg); }
/* ── log pane ── */
#log-pane { height: 140px; background: var(--bg); border-top: 1px solid var(--border);
            display: flex; flex-direction: column; flex-shrink: 0; }
#log-pane-hdr { padding: 3px 10px; border-bottom: 1px solid var(--border); display: flex;
                align-items: center; gap: 8px; font-size: 10px; color: var(--muted); }
.log-clear-btn { margin-left: auto; cursor: pointer; }
.log-clear-btn:hover { color: var(--text); }
#log-lines { flex: 1; overflow-y: auto; padding: 4px 10px; font-size: 11px; color: var(--text); }
.log-line { white-space: pre; line-height: 1.5; }
.log-warn  { color: var(--yellow); }
.log-error { color: var(--red); }
/* ── Drawflow overrides ── */
.drawflow { background: var(--bg) !important; }
.drawflow .drawflow-node {
  background: var(--surface) !important;
  border: 1px solid var(--border) !important;
  border-radius: 6px !important;
  min-width: 180px !important;
  padding: 0 !important;
  box-shadow: 0 2px 8px rgba(0,0,0,.4) !important;
  color: var(--text) !important;
}
.drawflow .drawflow-node.selected {
  border-color: var(--blue) !important;
  box-shadow: 0 0 0 2px rgba(88,166,255,.25) !important;
}
.drawflow .drawflow-node .input,
.drawflow .drawflow-node .output {
  background: var(--muted) !important;
  border-color: var(--border) !important;
  width: 10px !important; height: 10px !important;
  top: 50% !important; transform: translateY(-50%) !important;
}
.drawflow .drawflow-node .input:hover,
.drawflow .drawflow-node .output:hover {
  background: var(--blue) !important;
}
.drawflow .connection .main-path { stroke: #444c56 !important; stroke-width: 2px !important; }
.drawflow .connection .main-path:hover { stroke: var(--blue) !important; }
.drawflow .connection.selected .main-path { stroke: var(--blue) !important; }
/* ── typed port colours ── */
.drawflow .drawflow-node .input.port-can_frame,
.drawflow .drawflow-node .output.port-can_frame  { background: #58a6ff !important; }
.drawflow .drawflow-node .input.port-eth_frame,
.drawflow .drawflow-node .output.port-eth_frame  { background: #bc8cff !important; }
.drawflow .drawflow-node .input.port-bus_signal,
.drawflow .drawflow-node .output.port-bus_signal { background: #d29922 !important; }
.drawflow .drawflow-node .input.port-value,
.drawflow .drawflow-node .output.port-value      { background: #3fb950 !important; }
.drawflow .drawflow-node .input.port-any,
.drawflow .drawflow-node .output.port-any        { background: #8b949e !important; }
/* orange dashed warning wire for type mismatch */
.drawflow .connection.conn-warn .main-path {
  stroke: #f0883e !important; stroke-dasharray: 6,3 !important;
}
/* ── boat node cards ── */
.boat-node { border-radius: 6px; overflow: hidden; }
.boat-node-hdr {
  padding: 5px 10px; font-size: 11px; font-weight: 600;
  letter-spacing: .03em; color: #0d1117;
}
.boat-node-hdr.src  { background: var(--src); }
.boat-node-hdr.proc { background: var(--proc); }
.boat-node-hdr.sink { background: var(--sink); }
.boat-node-body { padding: 8px 10px; display: flex; flex-direction: column; gap: 5px; }
.boat-field { display: flex; flex-direction: column; gap: 2px; }
.boat-field label { font-size: 10px; color: var(--muted); }
.boat-field input, .boat-field select {
  background: var(--bg); border: 1px solid var(--border); border-radius: 3px;
  color: var(--text); font-family: inherit; font-size: 11px; padding: 3px 6px;
  width: 100%;
}
.boat-field input:focus, .boat-field select:focus { outline: none; border-color: var(--blue); }
  .gw-status-badge {
    font-size: 11px; padding: 2px 10px; border-radius: 12px;
    font-family: var(--mono); transition: all .3s; flex-shrink: 0;
  }
  .gw-status-badge.on { background: #1f3a1f; color: var(--green); border: 1px solid #2ea043; }
  .gw-status-badge.off { background: #3d0b0b; color: var(--red); border: 1px solid #8b2020; }
</style>
</head>
<body>
<header>
  <span class="logo">⛵ BoAt</span>
  <span style="color:var(--muted)">Flow Editor</span>
  <span class="gw-status-badge off" id="gw-status-badge">○ gateway</span>
  <span class="spacer"></span>
</header>
<nav id="panel-nav">
  <a class="nav-link" data-port="8086">Launcher</a>
  <a class="nav-link" data-port="8080">Dashboard</a>
  <a class="nav-link" data-port="8081">Nodes</a>
  <a class="nav-link" data-port="8082">Commander</a>
  <a class="nav-link" data-port="8083">Recorder</a>
</nav>
<div class="main-row">
  <!-- ── sidebar ── -->
  <div id="sidebar">
    <div id="flow-list-panel">
      <div class="sidebar-hdr">
        Flows
        <button class="btn-xs" onclick="newFlow()">+ New</button>
      </div>
      <div id="flow-list"></div>
    </div>
    <div id="palette">
      <div class="pal-section">Sources</div>
      <div class="pal-node" draggable="true" ondragstart="drag(event,'can_in')">
        <span class="pal-dot" style="background:var(--src)"></span>CAN In
      </div>
      <div class="pal-node" draggable="true" ondragstart="drag(event,'eth_in')">
        <span class="pal-dot" style="background:var(--src)"></span>Ethernet In
      </div>
      <div class="pal-node" draggable="true" ondragstart="drag(event,'bus_in')">
        <span class="pal-dot" style="background:var(--src)"></span>Bus Signal In
      </div>
      <div class="pal-node" draggable="true" ondragstart="drag(event,'timer')">
        <span class="pal-dot" style="background:var(--src)"></span>Timer
      </div>
      <div class="pal-node" draggable="true" ondragstart="drag(event,'inject')">
        <span class="pal-dot" style="background:var(--src)"></span>Inject
      </div>
      <div class="pal-section">Processing</div>
      <div class="pal-node" draggable="true" ondragstart="drag(event,'filter')">
        <span class="pal-dot" style="background:var(--proc)"></span>Filter
      </div>
      <div class="pal-node" draggable="true" ondragstart="drag(event,'transform')">
        <span class="pal-dot" style="background:var(--proc)"></span>Transform
      </div>
      <div class="pal-node" draggable="true" ondragstart="drag(event,'math')">
        <span class="pal-dot" style="background:var(--proc)"></span>Math
      </div>
      <div class="pal-node" draggable="true" ondragstart="drag(event,'counter')">
        <span class="pal-dot" style="background:var(--proc)"></span>Counter
      </div>
      <div class="pal-node" draggable="true" ondragstart="drag(event,'delay')">
        <span class="pal-dot" style="background:var(--proc)"></span>Delay
      </div>
      <div class="pal-node" draggable="true" ondragstart="drag(event,'change')">
        <span class="pal-dot" style="background:var(--proc)"></span>Change Detector
      </div>
      <div class="pal-node" draggable="true" ondragstart="drag(event,'merge')">
        <span class="pal-dot" style="background:var(--proc)"></span>Merge
      </div>
      <div class="pal-node" draggable="true" ondragstart="drag(event,'if_node')">
        <span class="pal-dot" style="background:var(--proc)"></span>If / Else
      </div>
      <div class="pal-node" draggable="true" ondragstart="drag(event,'switch_node')">
        <span class="pal-dot" style="background:var(--proc)"></span>Switch
      </div>
      <div class="pal-section">State</div>
      <div class="pal-node" draggable="true" ondragstart="drag(event,'set_var')">
        <span class="pal-dot" style="background:var(--proc)"></span>Set Variable
      </div>
      <div class="pal-node" draggable="true" ondragstart="drag(event,'get_var')">
        <span class="pal-dot" style="background:var(--proc)"></span>Get Variable
      </div>
      <div class="pal-section">Convert</div>
      <div class="pal-node" draggable="true" ondragstart="drag(event,'can_to_bytes')">
        <span class="pal-dot" style="background:var(--proc)"></span>CAN → Bytes
      </div>
      <div class="pal-node" draggable="true" ondragstart="drag(event,'bytes_to_can')">
        <span class="pal-dot" style="background:var(--proc)"></span>Bytes → CAN
      </div>
      <div class="pal-node" draggable="true" ondragstart="drag(event,'extract_field')">
        <span class="pal-dot" style="background:var(--proc)"></span>Extract Field
      </div>
      <div class="pal-node" draggable="true" ondragstart="drag(event,'set_field')">
        <span class="pal-dot" style="background:var(--proc)"></span>Set Field
      </div>
      <div class="pal-section">Sinks</div>
      <div class="pal-node" draggable="true" ondragstart="drag(event,'can_out')">
        <span class="pal-dot" style="background:var(--sink)"></span>CAN Out
      </div>
      <div class="pal-node" draggable="true" ondragstart="drag(event,'eth_out')">
        <span class="pal-dot" style="background:var(--sink)"></span>Ethernet Out
      </div>
      <div class="pal-node" draggable="true" ondragstart="drag(event,'bus_out')">
        <span class="pal-dot" style="background:var(--sink)"></span>Bus Signal Out
      </div>
      <div class="pal-node" draggable="true" ondragstart="drag(event,'debug')">
        <span class="pal-dot" style="background:var(--sink)"></span>Debug
      </div>
    </div>
  </div>
  <!-- ── canvas column ── -->
  <div id="canvas-col">
    <div id="toolbar">
      <input id="flow-name-input" type="text" value="Untitled Flow"
             onchange="renameFlow(this.value)" placeholder="Flow name"/>
      <div class="tb-sep"></div>
      <button class="tb-btn" onclick="saveFlow()">Save</button>
      <button class="tb-btn green" id="btn-deploy" onclick="deployFlow()">▶ Deploy</button>
      <button class="tb-btn red"   id="btn-stop"   onclick="stopFlow()" style="display:none">■ Stop</button>
      <span id="status-badge" class="badge-stopped">● stopped</span>
    </div>
    <div id="drawflow-wrap"
         ondrop="drop(event)" ondragover="allowDrop(event)">
      <div id="drawflow"></div>
    </div>
    <div id="log-pane">
      <div id="log-pane-hdr">
        Log
        <span class="log-clear-btn" onclick="clearLog()">✕ clear</span>
      </div>
      <div id="log-lines"></div>
    </div>
  </div>
  <!-- ── properties panel (right) ── -->
  <div id="props-panel">
    <div class="sidebar-hdr">Properties</div>
    <div id="props-content">
      <div class="prop-empty">Click a node to see its properties.</div>
    </div>
  </div>
</div>
<!-- shared datalists — populated by loadGatewayData() or static -->
<datalist id="dl-can-ifaces"></datalist>
<datalist id="dl-eth-ifaces"></datalist>
<datalist id="dl-bus-signals"></datalist>
<datalist id="dl-field-paths">
  <option value="payload.can_id"/>
  <option value="payload.dlc"/>
  <option value="payload.data"/>
  <option value="payload.iface"/>
  <option value="payload.ethertype"/>
  <option value="payload.src_mac"/>
  <option value="payload.dst_mac"/>
  <option value="payload.name"/>
  <option value="payload.value"/>
  <option value="payload.type"/>
  <option value="payload.publisher"/>
  <option value="payload.count"/>
  <option value="topic"/>
  <option value="ts_ns"/>
  <option value="count"/>
</datalist>
<script src="https://cdn.jsdelivr.net/gh/jerosoler/Drawflow@0.0.59/dist/drawflow.min.js"></script>
<script>
// ── Drawflow init ─────────────────────────────────────────────────────────────
const editor = new Drawflow(document.getElementById('drawflow'));
editor.reroute = true;
editor.start();
// ── Node templates ────────────────────────────────────────────────────────────
const TEMPLATES = {
  can_in: `<div class="boat-node">
    <div class="boat-node-hdr src">CAN In</div>
    <div class="boat-node-body">
      <div class="boat-field"><label>Interface</label>
        <input type="text" df-iface list="dl-can-ifaces" placeholder="vcan0"/></div>
      <div class="boat-field"><label>CAN ID filter (optional)</label>
        <input type="text" df-can_id_filter placeholder="0x123 or empty"/></div>
    </div></div>`,
  eth_in: `<div class="boat-node">
    <div class="boat-node-hdr src">Ethernet In</div>
    <div class="boat-node-body">
      <div class="boat-field"><label>Interface</label>
        <input type="text" df-iface list="dl-eth-ifaces" placeholder="veth0"/></div>
      <div class="boat-field"><label>EtherType filter (optional)</label>
        <input type="text" df-ethertype_filter placeholder="0x0800 or empty"/></div>
    </div></div>`,
  bus_in: `<div class="boat-node">
    <div class="boat-node-hdr src">Bus Signal In</div>
    <div class="boat-node-body">
      <div class="boat-field"><label>Signal name filter (optional)</label>
        <input type="text" df-signal_filter list="dl-bus-signals" placeholder="engine.rpm or empty"/></div>
    </div></div>`,
  timer: `<div class="boat-node">
    <div class="boat-node-hdr src">Timer</div>
    <div class="boat-node-body">
      <div class="boat-field"><label>Interval (ms)</label>
        <input type="number" df-interval_ms value="1000" min="10"/></div>
      <div class="boat-field"><label>Topic</label>
        <input type="text" df-topic placeholder="timer"/></div>
    </div></div>`,
  inject: `<div class="boat-node">
    <div class="boat-node-hdr src">Inject</div>
    <div class="boat-node-body">
      <div class="boat-field"><label>Topic</label>
        <input type="text" df-topic placeholder="inject"/></div>
      <div class="boat-field"><label>Payload (JSON or scalar)</label>
        <input type="text" df-payload placeholder='{"key":"val"} or 42'/></div>
      <div class="boat-field"><label>Delay (ms)</label>
        <input type="number" df-delay_ms value="500" min="0"/></div>
    </div></div>`,
  filter: `<div class="boat-node">
    <div class="boat-node-hdr proc">Filter</div>
    <div class="boat-node-body">
      <div class="boat-field"><label>Field</label>
        <input type="text" df-field list="dl-field-paths" placeholder="payload.can_id"/></div>
      <div class="boat-field"><label>Operator</label>
        <select df-op>
          <option value="==">== equals</option>
          <option value="!=">!= not equals</option>
          <option value=">">&gt; greater than</option>
          <option value="<">&lt; less than</option>
          <option value="contains">contains</option>
        </select></div>
      <div class="boat-field"><label>Value</label>
        <input type="text" df-value placeholder="0x123"/></div>
    </div></div>`,
  transform: `<div class="boat-node">
    <div class="boat-node-hdr proc">Transform</div>
    <div class="boat-node-body">
      <div class="boat-field"><label>Field to set</label>
        <input type="text" df-field list="dl-field-paths" placeholder="payload.can_id"/></div>
      <div class="boat-field"><label>Value</label>
        <input type="text" df-value placeholder="0x234"/></div>
    </div></div>`,
  counter: `<div class="boat-node">
    <div class="boat-node-hdr proc">Counter</div>
    <div class="boat-node-body">
      <div style="color:var(--muted);font-size:10px;padding:2px 0">
        Adds <code>count</code> field to each message</div>
    </div></div>`,
  delay: `<div class="boat-node">
    <div class="boat-node-hdr proc">Delay</div>
    <div class="boat-node-body">
      <div class="boat-field"><label>Delay (ms)</label>
        <input type="number" df-delay_ms value="100" min="0"/></div>
    </div></div>`,
  math: `<div class="boat-node">
    <div class="boat-node-hdr proc">Math</div>
    <div class="boat-node-body">
      <div class="boat-field"><label>Field</label>
        <input type="text" df-field list="dl-field-paths" placeholder="payload.value"/></div>
      <div class="boat-field"><label>Operation</label>
        <select df-op>
          <option value="+">+ add</option>
          <option value="-">− subtract</option>
          <option value="*">× multiply</option>
          <option value="/">÷ divide</option>
          <option value="%">% modulo</option>
        </select></div>
      <div class="boat-field"><label>Operand</label>
        <input type="text" df-value placeholder="10"/></div>
    </div></div>`,
  switch_node: `<div class="boat-node">
    <div class="boat-node-hdr proc">Switch</div>
    <div class="boat-node-body">
      <div class="boat-field"><label>Field</label>
        <input type="text" df-field list="dl-field-paths" placeholder="payload.can_id"/></div>
      <div class="boat-field"><label>Case 1 value → out 1</label>
        <input type="text" df-case1 placeholder="0x100"/></div>
      <div class="boat-field"><label>Case 2 value → out 2</label>
        <input type="text" df-case2 placeholder="0x200"/></div>
      <div class="boat-field"><label>Case 3 value → out 3</label>
        <input type="text" df-case3 placeholder="0x300"/></div>
      <div style="color:var(--muted);font-size:10px;margin-top:4px">out 4 = default</div>
    </div></div>`,
  change: `<div class="boat-node">
    <div class="boat-node-hdr proc">Change Detector</div>
    <div class="boat-node-body">
      <div class="boat-field"><label>Field to watch</label>
        <input type="text" df-field list="dl-field-paths" placeholder="payload.value"/></div>
      <div style="color:var(--muted);font-size:10px;padding:2px 0">
        Forwards message only when value changes</div>
    </div></div>`,
  merge: `<div class="boat-node">
    <div class="boat-node-hdr proc">Merge</div>
    <div class="boat-node-body">
      <div style="color:var(--muted);font-size:10px;padding:2px 0">
        Combines two streams into one output</div>
    </div></div>`,
  set_var: `<div class="boat-node">
    <div class="boat-node-hdr proc">Set Variable</div>
    <div class="boat-node-body">
      <div class="boat-field"><label>Variable name</label>
        <input type="text" df-name placeholder="my_var"/></div>
      <div class="boat-field"><label>Read from field (empty = payload)</label>
        <input type="text" df-field list="dl-field-paths" placeholder="payload.value"/></div>
    </div></div>`,
  get_var: `<div class="boat-node">
    <div class="boat-node-hdr proc">Get Variable</div>
    <div class="boat-node-body">
      <div class="boat-field"><label>Variable name</label>
        <input type="text" df-name placeholder="my_var"/></div>
      <div class="boat-field"><label>Write to field</label>
        <input type="text" df-field list="dl-field-paths" placeholder="payload.value"/></div>
    </div></div>`,
  if_node: `<div class="boat-node">
    <div class="boat-node-hdr proc">If / Else</div>
    <div class="boat-node-body">
      <div class="boat-field"><label>Field</label>
        <input type="text" df-field list="dl-field-paths" placeholder="payload.can_id"/></div>
      <div class="boat-field"><label>Operator</label>
        <select df-op>
          <option value="==">== equals</option>
          <option value="!=">!= not equals</option>
          <option value=">">&gt; greater than</option>
          <option value="<">&lt; less than</option>
          <option value="contains">contains</option>
        </select></div>
      <div class="boat-field"><label>Value</label>
        <input type="text" df-value placeholder="0x555"/></div>
      <div style="display:flex;justify-content:space-between;font-size:10px;margin-top:4px;padding:0 2px">
        <span style="color:var(--green)">▲ out 1 — IF true</span>
        <span style="color:var(--red)">out 2 — ELSE ▲</span>
      </div>
    </div></div>`,
  can_out: `<div class="boat-node">
    <div class="boat-node-hdr sink">CAN Out</div>
    <div class="boat-node-body">
      <div class="boat-field"><label>Interface (required)</label>
        <input type="text" df-iface list="dl-can-ifaces" placeholder="vcan0"/></div>
      <div class="boat-field"><label>CAN ID (overrides msg)</label>
        <input type="text" df-can_id placeholder="0x234 or empty"/></div>
      <div class="boat-field"><label>Data hex (overrides msg)</label>
        <input type="text" df-data placeholder="DE AD BE EF or empty"/></div>
    </div></div>`,
  eth_out: `<div class="boat-node">
    <div class="boat-node-hdr sink">Ethernet Out</div>
    <div class="boat-node-body">
      <div class="boat-field"><label>Interface (required)</label>
        <input type="text" df-iface list="dl-eth-ifaces" placeholder="veth0"/></div>
      <div class="boat-field"><label>EtherType (overrides msg)</label>
        <input type="text" df-ethertype placeholder="0x0800 or empty"/></div>
      <div class="boat-field"><label>Src MAC (optional)</label>
        <input type="text" df-src_mac placeholder="02:00:00:00:00:01"/></div>
      <div class="boat-field"><label>Dst MAC (optional)</label>
        <input type="text" df-dst_mac placeholder="FF:FF:FF:FF:FF:FF"/></div>
    </div></div>`,
  bus_out: `<div class="boat-node">
    <div class="boat-node-hdr sink">Bus Signal Out</div>
    <div class="boat-node-body">
      <div class="boat-field"><label>Signal name (overrides msg)</label>
        <input type="text" df-signal_name list="dl-bus-signals" placeholder="engine.rpm or empty"/></div>
      <div class="boat-field"><label>Value type</label>
        <select df-signal_type>
          <option value="number">number</option>
          <option value="string">string</option>
          <option value="bool">bool</option>
          <option value="bytes">bytes (hex)</option>
        </select></div>
    </div></div>`,
  debug: `<div class="boat-node">
    <div class="boat-node-hdr sink">Debug</div>
    <div class="boat-node-body">
      <div class="boat-field"><label>Label</label>
        <input type="text" df-label placeholder="debug"/></div>
    </div></div>`,
  can_to_bytes: `<div class="boat-node">
    <div class="boat-node-hdr proc">CAN → Bytes</div>
    <div class="boat-node-body">
      <div style="color:var(--muted);font-size:10px;padding:2px 0">
        Packs <code>can_id</code> (4B BE) + <code>dlc</code> (1B) + data<br>
        into a <code>value</code> bytes payload</div>
    </div></div>`,
  bytes_to_can: `<div class="boat-node">
    <div class="boat-node-hdr proc">Bytes → CAN</div>
    <div class="boat-node-body">
      <div class="boat-field"><label>Interface (required)</label>
        <input type="text" df-iface list="dl-can-ifaces" placeholder="vcan0"/></div>
      <div style="color:var(--muted);font-size:10px;padding:2px 0">
        Unpacks bytes → <code>can_frame</code></div>
    </div></div>`,
  extract_field: `<div class="boat-node">
    <div class="boat-node-hdr proc">Extract Field</div>
    <div class="boat-node-body">
      <div class="boat-field"><label>Field path</label>
        <input type="text" df-field list="dl-field-paths" placeholder="payload.can_id"/></div>
      <div style="color:var(--muted);font-size:10px;padding:2px 0">
        Emits a <code>value</code> message with extracted field</div>
    </div></div>`,
  set_field: `<div class="boat-node">
    <div class="boat-node-hdr proc">Set Field</div>
    <div class="boat-node-body">
      <div class="boat-field"><label>Field path</label>
        <input type="text" df-field list="dl-field-paths" placeholder="payload.value"/></div>
      <div class="boat-field"><label>Value</label>
        <input type="text" df-value placeholder="42"/></div>
    </div></div>`,
};
const NODE_IO = {
  // sources
  can_in:       [0,1], eth_in:    [0,1], bus_in:  [0,1],
  timer:        [0,1], inject:    [0,1],
  // processing
  filter:       [1,1], transform: [1,1], counter: [1,1], delay:    [1,1],
  math:         [1,1], change:    [1,1],
  set_var:      [1,1], get_var:   [1,1],
  merge:        [2,1],
  if_node:      [1,2], switch_node:[1,4],
  // convert
  can_to_bytes: [1,1], bytes_to_can: [1,1],
  extract_field:[1,1], set_field:    [1,1],
  // sinks
  can_out:      [1,0], eth_out:   [1,0], bus_out: [1,0], debug: [1,0],
};
// ── Port type system ──────────────────────────────────────────────────────────
// in/out arrays list the message type expected/produced on each port (by index).
const PORT_TYPES = {
  // sources
  can_in:        { in: [],                           out: ['can_frame'] },
  eth_in:        { in: [],                           out: ['eth_frame'] },
  bus_in:        { in: [],                           out: ['bus_signal'] },
  timer:         { in: [],                           out: ['any'] },
  inject:        { in: [],                           out: ['any'] },
  // processing
  filter:        { in: ['any'],                      out: ['any'] },
  transform:     { in: ['any'],                      out: ['any'] },
  counter:       { in: ['any'],                      out: ['any'] },
  delay:         { in: ['any'],                      out: ['any'] },
  math:          { in: ['any'],                      out: ['any'] },
  change:        { in: ['any'],                      out: ['any'] },
  merge:         { in: ['any', 'any'],               out: ['any'] },
  set_var:       { in: ['any'],                      out: ['any'] },
  get_var:       { in: ['any'],                      out: ['any'] },
  if_node:       { in: ['any'],                      out: ['any', 'any'] },
  switch_node:   { in: ['any'],                      out: ['any','any','any','any'] },
  // convert
  can_to_bytes:  { in: ['can_frame'],                out: ['value'] },
  bytes_to_can:  { in: ['value'],                    out: ['can_frame'] },
  extract_field: { in: ['any'],                      out: ['value'] },
  set_field:     { in: ['any'],                      out: ['any'] },
  // sinks
  can_out:       { in: ['can_frame'],                out: [] },
  eth_out:       { in: ['eth_frame'],                out: [] },
  bus_out:       { in: ['bus_signal'],               out: [] },
  debug:         { in: ['any'],                      out: [] },
};
const _PORT_CLASSES = ['port-can_frame','port-eth_frame','port-bus_signal','port-value','port-any'];
function colorNodePorts(nodeId) {
  const nodeEl = document.querySelector(`.drawflow-node[id="node-${nodeId}"]`);
  if (!nodeEl) return;
  // Drawflow adds the node type as the second class on the node element
  const nodeName = nodeEl.classList[1];
  const def = PORT_TYPES[nodeName];
  if (!def) return;
  nodeEl.querySelectorAll('.input').forEach((el, i) => {
    el.classList.remove(..._PORT_CLASSES);
    el.classList.add('port-' + (def.in[i] || 'any'));
  });
  nodeEl.querySelectorAll('.output').forEach((el, i) => {
    el.classList.remove(..._PORT_CLASSES);
    el.classList.add('port-' + (def.out[i] || 'any'));
  });
}
function _portType(nodeId, kind, idx) {
  const nodeEl = document.querySelector(`.drawflow-node[id="node-${nodeId}"]`);
  if (!nodeEl) return 'any';
  const def = PORT_TYPES[nodeEl.classList[1]];
  return def ? (def[kind][idx] || 'any') : 'any';
}
function colorConnection(connEl) {
  const cls = Array.from(connEl.classList);
  const outNode = (cls.find(c => c.startsWith('node_out_node-')) || '').replace('node_out_node-', '');
  const inNode  = (cls.find(c => c.startsWith('node_in_node-'))  || '').replace('node_in_node-', '');
  const outPort = (cls.find(c => c.startsWith('output_')) || 'output_1').replace('output_', '');
  const inPort  = (cls.find(c => c.startsWith('input_'))  || 'input_1').replace('input_', '');
  if (!outNode || !inNode) return;
  const srcType = _portType(outNode, 'out', parseInt(outPort) - 1);
  const dstType = _portType(inNode,  'in',  parseInt(inPort)  - 1);
  const warn = srcType !== 'any' && dstType !== 'any' && srcType !== dstType;
  connEl.classList.toggle('conn-warn', warn);
}
function colorAll() {
  document.querySelectorAll('.drawflow-node').forEach(el => {
    const m = el.id.match(/node-(\d+)/);
    if (m) colorNodePorts(m[1]);
  });
  document.querySelectorAll('#drawflow .connection').forEach(colorConnection);
}
editor.on('nodeCreated',       id  => colorNodePorts(id));
editor.on('connectionCreated', ()  => setTimeout(colorAll, 20));
editor.on('connectionRemoved', ()  => setTimeout(colorAll, 20));
// ── Node documentation ────────────────────────────────────────────────────────
// shape rows: [key, type_desc]
//   keys starting with "payload." are indented under a "payload {" block.
//   a row with key="" is rendered as a spacer/divider line.
// input:   null = source (no input)
//          {type, shape?} = expected input type + optional field details
// outputs: null = sink (no output)
//          [{port, type, shape?, note?}] = one entry per output port
const NODE_DOCS = {
  can_in: {
    title: "CAN In", cat: "Source",
    desc: "Subscribes to CAN frames from the gateway. Emits one message per received frame.",
    fields: {
      iface:         "Interface to subscribe (e.g. vcan0). Empty = all buses.",
      can_id_filter: "CAN ID to accept (e.g. 0x123). Empty = all IDs.",
    },
    input: null,
    outputs: [{
      port: "Output", type: "can_frame",
      shape: [
        ["topic",         '"can/{iface}/0x{ID}"'],
        ["ts_ns",         "timestamp (nanoseconds)"],
        ["payload.can_id","integer — CAN frame ID"],
        ["payload.dlc",   "integer 0–8 — data length"],
        ["payload.data",  "bytes — frame payload"],
        ["payload.iface", "string — interface name"],
      ]
    }]
  },
  eth_in: {
    title: "Ethernet In", cat: "Source",
    desc: "Subscribes to Ethernet frames from the gateway. Emits one message per received frame.",
    fields: {
      iface:            "Interface to subscribe (e.g. veth0). Empty = all.",
      ethertype_filter: "EtherType to accept (e.g. 0x0800). Empty = all.",
    },
    input: null,
    outputs: [{
      port: "Output", type: "eth_frame",
      shape: [
        ["topic",           '"eth/{iface}/0x{ethertype}"'],
        ["ts_ns",           "timestamp (nanoseconds)"],
        ["payload.iface",   "string — interface name"],
        ["payload.ethertype","integer — EtherType"],
        ["payload.src_mac", "string — source MAC (hex colon-separated)"],
        ["payload.dst_mac", "string — destination MAC"],
        ["payload.data",    "bytes — Ethernet payload (max 1500 B)"],
      ]
    }]
  },
  bus_in: {
    title: "Bus Signal In", cat: "Source",
    desc: "Subscribes to named bus signals published by any node on the gateway.",
    fields: {
      signal_filter: "Signal name to subscribe to (e.g. engine.rpm). Empty = all.",
    },
    input: null,
    outputs: [{
      port: "Output", type: "bus_signal",
      shape: [
        ["topic",             '"bus/{name}"'],
        ["ts_ns",             "timestamp (nanoseconds)"],
        ["payload.name",      "string — signal name"],
        ["payload.value",     "number | string | bool | bytes"],
        ["payload.type",      '"number_value" | "string_value" | "bool_value" | "bytes_value"'],
        ["payload.publisher", "string — publisher node name"],
      ]
    }]
  },
  timer: {
    title: "Timer", cat: "Source",
    desc: "Emits a periodic tick message with an incrementing counter. Use as a clock to drive cyclic behaviour.",
    fields: {
      interval_ms: "Tick period in milliseconds.",
      topic:       'Topic string on every tick (e.g. "timer").',
    },
    input: null,
    outputs: [{
      port: "Output", type: "any",
      shape: [
        ["topic",          "configured topic string"],
        ["ts_ns",          "timestamp (nanoseconds)"],
        ["payload.count",  "integer — tick counter (starts at 0)"],
      ]
    }]
  },
  inject: {
    title: "Inject", cat: "Source",
    desc: "Fires a single message once after the flow starts. Useful for initial state setup or one-shot tests.",
    fields: {
      topic:    "Topic string for the injected message.",
      payload:  'Payload as JSON object or scalar (e.g. 42 or {"key":"val"}).',
      delay_ms: "Milliseconds to wait after flow start before firing.",
    },
    input: null,
    outputs: [{
      port: "Output", type: "any",
      shape: [
        ["topic",   "configured topic string"],
        ["ts_ns",   "timestamp (nanoseconds)"],
        ["payload", "configured payload (object or scalar wrapped in {value:…})"],
      ]
    }]
  },
  filter: {
    title: "Filter", cat: "Processing",
    desc: "Passes messages through only when a field condition is true. Messages that don't match are dropped.",
    fields: {
      field: "Dotted field path to test (e.g. payload.can_id).",
      op:    "Comparison operator: ==  !=  >  <  contains.",
      value: "Expected value (hex strings like 0x1F are parsed automatically).",
    },
    input:   { type: "any", note: "Any message type" },
    outputs: [{ port: "Output", type: "→ same as input", note: "Forwarded unchanged when condition is true." }]
  },
  transform: {
    title: "Transform", cat: "Processing",
    desc: "Overwrites one field in the message with a fixed value. All other fields are preserved.",
    fields: {
      field: "Dotted path of the field to overwrite (e.g. payload.can_id).",
      value: "New value to write (hex strings are parsed).",
    },
    input:   { type: "any", note: "Any message type" },
    outputs: [{ port: "Output", type: "→ same as input", note: "Same structure, one field replaced." }]
  },
  math: {
    title: "Math", cat: "Processing",
    desc: "Applies an arithmetic operation to a numeric field and writes the result back to the same field.",
    fields: {
      field: "Dotted path of the numeric field (e.g. payload.value).",
      op:    "Operation: +  −  ×  ÷  %.",
      value: "Right-hand operand (numeric literal).",
    },
    input:   { type: "any", note: "Field must resolve to a number" },
    outputs: [{ port: "Output", type: "→ same as input", note: "Same structure, operated field replaced with result." }]
  },
  counter: {
    title: "Counter", cat: "Processing",
    desc: "Appends a monotonically increasing count field to the message at the root level. Resets to 0 on restart.",
    fields: {},
    input:   { type: "any", note: "Any message type" },
    outputs: [{
      port: "Output", type: "→ same as input + count",
      shape: [["count", "integer — message counter (starts at 0)"]]
    }]
  },
  delay: {
    title: "Delay", cat: "Processing",
    desc: "Pauses for a fixed duration before forwarding. Note: blocks the dispatch thread — keep values small.",
    fields: {
      delay_ms: "Milliseconds to wait before forwarding.",
    },
    input:   { type: "any", note: "Any message type" },
    outputs: [{ port: "Output", type: "→ same as input", note: "Forwarded unchanged after the delay." }]
  },
  change: {
    title: "Change Detector", cat: "Processing",
    desc: "Only forwards a message when the watched field's value differs from the previous message. Identical successive values are dropped.",
    fields: {
      field: "Dotted path to watch for changes (e.g. payload.value).",
    },
    input:   { type: "any", note: "Any message type" },
    outputs: [{ port: "Output", type: "→ same as input", note: "Forwarded only when watched field changes." }]
  },
  merge: {
    title: "Merge", cat: "Processing",
    desc: "Combines two input streams into one. Any message arriving on either input is forwarded unchanged.",
    fields: {},
    input:   { type: "any", note: "Two independent inputs — any message type on either port." },
    outputs: [{ port: "Output", type: "→ same as input", note: "Forwarded unchanged from whichever input fired." }]
  },
  if_node: {
    title: "If / Else", cat: "Processing",
    desc: "Two-way conditional router. Evaluates a field condition and routes the message to the matching output port.",
    fields: {
      field: "Dotted field path to test.",
      op:    "Comparison operator: ==  !=  >  <  contains.",
      value: "Expected value.",
    },
    input: { type: "any", note: "Any message type" },
    outputs: [
      { port: "Output 1 — IF true",  type: "→ same as input", note: "Message forwarded here when condition is TRUE." },
      { port: "Output 2 — ELSE",     type: "→ same as input", note: "Message forwarded here when condition is FALSE." },
    ]
  },
  switch_node: {
    title: "Switch", cat: "Processing",
    desc: "Four-way router. Tests a field against three configured values and routes to the first match. Output 4 is the default.",
    fields: {
      field: "Dotted field path to test.",
      case1: "Value that routes to output 1.",
      case2: "Value that routes to output 2.",
      case3: "Value that routes to output 3.",
    },
    input: { type: "any", note: "Any message type" },
    outputs: [
      { port: "Output 1 — case 1", type: "→ same as input", note: "Forwarded when field == case1 value." },
      { port: "Output 2 — case 2", type: "→ same as input", note: "Forwarded when field == case2 value." },
      { port: "Output 3 — case 3", type: "→ same as input", note: "Forwarded when field == case3 value." },
      { port: "Output 4 — default",type: "→ same as input", note: "Forwarded when no case matches." },
    ]
  },
  set_var: {
    title: "Set Variable", cat: "State",
    desc: "Stores a value extracted from the message into a named variable. Variables are shared across all nodes in the flow and persist until the flow is stopped. The message passes through unchanged.",
    fields: {
      name:  "Variable name to write (e.g. last_speed).",
      field: "Field to read from (e.g. payload.value). Empty = whole payload.",
    },
    input:   { type: "any", note: "Any message type" },
    outputs: [{ port: "Output", type: "→ same as input", note: "Message forwarded unchanged after storing the variable." }]
  },
  get_var: {
    title: "Get Variable", cat: "State",
    desc: "Reads a stored variable and injects its value into the message at the specified field path. Use Set Variable upstream to populate the store.",
    fields: {
      name:  "Variable name to read.",
      field: "Field path to write the value into (e.g. payload.value).",
    },
    input:   { type: "any", note: "Any message type" },
    outputs: [{ port: "Output", type: "→ same as input", note: "Same message but with the configured field set to the variable's value." }]
  },
  can_to_bytes: {
    title: "CAN → Bytes", cat: "Convert",
    desc: "Packs a can_frame message into a compact byte buffer. Use before sending CAN data over a non-CAN channel.",
    fields: {},
    input: {
      type: "can_frame",
      note: "Expects a can_frame message (from CAN In or similar).",
    },
    outputs: [{
      port: "Output", type: "value",
      shape: [
        ["payload.value", "bytes — 4B can_id (BE) + 1B dlc + data bytes"],
        ["payload.name",  '"can_bytes"'],
      ]
    }]
  },
  bytes_to_can: {
    title: "Bytes → CAN", cat: "Convert",
    desc: "Unpacks a raw byte buffer back into a can_frame message. Counterpart to CAN → Bytes.",
    fields: {
      iface: "CAN interface to attach to the reconstructed frame (e.g. vcan0).",
    },
    input: {
      type: "value",
      note: "Expects bytes in payload.value: 4B CAN ID (BE) + 1B DLC + data.",
    },
    outputs: [{
      port: "Output", type: "can_frame",
      shape: [
        ["topic",         '"can/{iface}/0x{ID}"'],
        ["payload.can_id","integer"],
        ["payload.dlc",   "integer"],
        ["payload.data",  "bytes"],
        ["payload.iface", "string — from config"],
      ]
    }]
  },
  extract_field: {
    title: "Extract Field", cat: "Convert",
    desc: "Pulls one field from any message and emits a new value message carrying only that field's content.",
    fields: {
      field: "Dotted path of the field to extract (e.g. payload.can_id).",
    },
    input: { type: "any", note: "Any message type" },
    outputs: [{
      port: "Output", type: "value",
      shape: [
        ["payload.value", "extracted field value"],
        ["payload.name",  "field path string (e.g. \"payload.can_id\")"],
      ]
    }]
  },
  set_field: {
    title: "Set Field", cat: "Convert",
    desc: "Sets a specific field to a configured literal value. Message type is preserved; all other fields are unchanged.",
    fields: {
      field: "Dotted path of the field to set.",
      value: "Literal value to write (hex strings are parsed).",
    },
    input:   { type: "any", note: "Any message type" },
    outputs: [{ port: "Output", type: "→ same as input", note: "Same message with the configured field overwritten." }]
  },
  can_out: {
    title: "CAN Out", cat: "Sink",
    desc: "Sends a CAN frame via the gateway. Config fields override values from the incoming message payload.",
    fields: {
      iface:  "CAN interface to send on (required, e.g. vcan0).",
      can_id: "CAN ID override — leave empty to use payload.can_id.",
      data:   "Hex data override, e.g. DE AD BE EF — leave empty to use payload.data.",
    },
    input: {
      type: "can_frame",
      note: "Expects a can_frame message. Other types are rejected with an error log.",
      shape: [
        ["payload.can_id", "integer — used if no can_id configured"],
        ["payload.data",   "bytes — used if no data configured"],
        ["payload.iface",  "string — used if no iface configured"],
      ]
    },
    outputs: null
  },
  eth_out: {
    title: "Ethernet Out", cat: "Sink",
    desc: "Sends an Ethernet frame via the gateway. Config fields override values from the incoming message payload.",
    fields: {
      iface:     "Ethernet interface to send on (required, e.g. veth0).",
      ethertype: "EtherType override — leave empty to use payload.ethertype.",
      src_mac:   "Source MAC, e.g. 02:00:00:00:00:01 (optional).",
      dst_mac:   "Destination MAC, e.g. FF:FF:FF:FF:FF:FF (optional).",
    },
    input: {
      type: "eth_frame",
      note: "Expects an eth_frame message. Other types are rejected with an error log.",
      shape: [
        ["payload.iface",     "string — used if no iface configured"],
        ["payload.ethertype", "integer — used if no ethertype configured"],
        ["payload.data",      "bytes — Ethernet payload to send"],
      ]
    },
    outputs: null
  },
  bus_out: {
    title: "Bus Signal Out", cat: "Sink",
    desc: "Publishes a named signal to the gateway bus. Config fields override values from the incoming message.",
    fields: {
      signal_name: "Signal name to publish — leave empty to use payload.name.",
      signal_type: "Value type: number, string, bool, or bytes (hex).",
    },
    input: {
      type: "bus_signal",
      note: "Expects a bus_signal message. Other types are rejected with an error log.",
      shape: [
        ["payload.name",  "string — signal name (used if no signal_name configured)"],
        ["payload.value", "value to publish (number | string | bool | bytes)"],
      ]
    },
    outputs: null
  },
  debug: {
    title: "Debug", cat: "Sink",
    desc: "Logs the incoming message payload to the flow log panel. Does not forward the message.",
    fields: {
      label: "Label prefix shown in the log output.",
    },
    input:   { type: "any", note: "Accepts any message type." },
    outputs: null
  },
};
const _CAT_COLOR = {
  Source: 'var(--src)', Processing: 'var(--proc)', State: 'var(--proc)',
  Convert: 'var(--blue)', Sink: 'var(--sink)',
};
// Render an array of [key, desc] shape rows into HTML.
// Keys starting with "payload." are grouped under a "payload {" block.
function _renderShape(shape) {
  if (!shape || !shape.length) return '';
  const top     = shape.filter(([k]) => !k.startsWith('payload.'));
  const payload = shape.filter(([k]) =>  k.startsWith('payload.'));
  let h = '';
  for (const [k, v] of top)
    h += `<div class="psr"><span class="psk">${k}</span><span class="psv">${v}</span></div>`;
  if (payload.length) {
    h += `<div class="psr"><span class="psk muted">payload</span><span class="psv">{</span></div>`;
    for (const [k, v] of payload)
      h += `<div class="psr psi"><span class="psk">${k.slice(8)}</span><span class="psv">${v}</span></div>`;
    h += `<div class="psr"><span class="psv">}</span></div>`;
  }
  return `<div class="prop-shape">${h}</div>`;
}
function _typeTag(type) {
  const colors = {
    can_frame: 'var(--src)', eth_frame: 'var(--purple)',
    bus_signal: 'var(--yellow)', value: 'var(--green)', any: 'var(--muted)',
  };
  const base = type.replace(/^→\s*/, '');
  const arrow = type.startsWith('→') ? '→ ' : '';
  const col = colors[base] || 'var(--muted)';
  return `<span style="color:${col};font-weight:600">${arrow}</span>`
       + `<span style="color:${col}">${base}</span>`;
}
function showProps(nodeId) {
  const nd  = editor.getNodeFromId(nodeId);
  if (!nd) return;
  const doc  = NODE_DOCS[nd.name] || { title: nd.name, cat: '—', desc: '', fields: {}, input: null, outputs: null };
  const data = nd.data || {};
  // ── config fields ─────────────────────────────────────────────────────────
  const allKeys = new Set([...Object.keys(doc.fields || {}), ...Object.keys(data)]);
  let fieldsHtml = '';
  for (const key of allKeys) {
    const hint    = (doc.fields || {})[key] || '';
    const raw     = data[key];
    const isEmpty = raw === '' || raw === null || raw === undefined;
    fieldsHtml += `
      <div class="prop-row">
        <div class="prop-key" title="${hint}">${key}${hint ? ' ⓘ' : ''}</div>
        <div class="prop-val${isEmpty ? ' empty' : ''}">${isEmpty ? 'not set' : String(raw)}</div>
      </div>`;
  }
  // ── input section ─────────────────────────────────────────────────────────
  let inputHtml = '';
  if (doc.input) {
    const tag  = _typeTag(doc.input.type);
    const note = doc.input.note ? `<div class="prop-desc">${doc.input.note}</div>` : '';
    const shp  = _renderShape(doc.input.shape);
    inputHtml  = `<div class="prop-section">Input</div>
                  <div style="padding:3px 10px 0">${tag}</div>
                  ${note}${shp}`;
  }
  // ── output section ────────────────────────────────────────────────────────
  let outputHtml = '';
  if (doc.outputs) {
    outputHtml = `<div class="prop-section">Output${doc.outputs.length > 1 ? 's' : ''}</div>`;
    for (const o of doc.outputs) {
      const portLabel = doc.outputs.length > 1 ? `<div style="padding:4px 10px 0;font-size:10px;color:var(--text)">${o.port}</div>` : '';
      const tag  = _typeTag(o.type);
      const note = o.note ? `<div class="prop-desc">${o.note}</div>` : '';
      const shp  = _renderShape(o.shape);
      outputHtml += `${portLabel}<div style="padding:3px 10px 0">${tag}</div>${note}${shp}`;
    }
  }
  document.getElementById('props-content').innerHTML = `
    <div class="prop-hdr">
      <span class="prop-title">${doc.title}</span>
      <span class="prop-cat" style="color:${_CAT_COLOR[doc.cat] || 'var(--muted)'}">${doc.cat}</span>
    </div>
    <div class="prop-desc">${doc.desc}</div>
    ${fieldsHtml ? `<div class="prop-section">Config</div><div class="prop-fields">${fieldsHtml}</div>` : ''}
    ${inputHtml}
    ${outputHtml}
  `;
}
function clearProps() {
  document.getElementById('props-content').innerHTML =
    '<div class="prop-empty">Click a node to see its properties and message structure.</div>';
}
editor.on('nodeSelected',    id => showProps(id));
editor.on('nodeUnselected',  ()  => clearProps());
editor.on('nodeDataChanged', id  => showProps(id));
// ── State ─────────────────────────────────────────────────────────────────────
let currentFlowId   = null;
let logSince        = 0;
let statusPollTimer = null;
// ── Drag & drop ───────────────────────────────────────────────────────────────
function drag(ev, type) { ev.dataTransfer.setData('nodeType', type); }
function allowDrop(ev)  { ev.preventDefault(); }
function drop(ev) {
  ev.preventDefault();
  const type = ev.dataTransfer.getData('nodeType');
  if (!type || !currentFlowId) return;
  const rect   = document.getElementById('drawflow').getBoundingClientRect();
  const pos_x  = ev.clientX - rect.left;
  const pos_y  = ev.clientY - rect.top;
  const [ins, outs] = NODE_IO[type];
  editor.addNode(type, ins, outs, pos_x, pos_y, type, {}, TEMPLATES[type]);
}
// ── Flow CRUD ─────────────────────────────────────────────────────────────────
async function loadFlowList() {
  const r  = await fetch('/api/flows');
  const d  = await r.json();
  const el = document.getElementById('flow-list');
  el.innerHTML = '';
  for (const f of d.flows) {
    const div = document.createElement('div');
    div.className = 'flow-item' + (f.id === currentFlowId ? ' active' : '');
    div.dataset.id = f.id;
    div.innerHTML  = `<span class="flow-dot ${f.status === 'running' ? 'running' : ''}"></span>
                      <span class="flow-name" title="${f.name}">${f.name}</span>`;
    div.onclick = () => openFlow(f.id);
    el.appendChild(div);
  }
}
async function newFlow() {
  const name = prompt('Flow name:', 'New Flow');
  if (!name) return;
  const r = await fetch('/api/flows', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({name}),
  });
  const d = await r.json();
  await openFlow(d.id);
  await loadFlowList();
}
async function openFlow(id) {
  if (currentFlowId && id !== currentFlowId) await autoSave();
  clearInterval(statusPollTimer);
  const r  = await fetch('/api/flows/' + id);
  const d  = await r.json();
  currentFlowId = id;
  document.getElementById('flow-name-input').value = d.meta?.name || id;
  editor.clear();
  // Normalise: strip legacy double-nesting {"drawflow": {"Home": {...}}} → {"Home": {...}}
  const raw = d.drawflow;
  const df  = raw?.drawflow ? raw.drawflow : raw;
  if (df && Object.keys(df.Home?.data || {}).length > 0) {
    editor.import({drawflow: df});  // Drawflow expects the outer wrapper back
    setTimeout(colorAll, 50);
  }
  logSince = 0;
  document.getElementById('log-lines').innerHTML = '';
  document.querySelectorAll('.flow-item').forEach(el => {
    el.classList.toggle('active', el.dataset.id === id);
  });
  pollStatus();
  statusPollTimer = setInterval(pollStatus, 1500);
}
async function autoSave() {
  if (!currentFlowId) return;
  // editor.export() returns {"drawflow": {"Home": {...}}}
  // We strip the outer key so the file stores {"Home": {...}} directly.
  const df = editor.export();
  await fetch('/api/flows/' + currentFlowId, {
    method: 'PUT', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({drawflow: df.drawflow}),
  });
}
async function saveFlow() {
  if (!currentFlowId) return;
  await autoSave();
  flashToolbarMsg('Saved');
}
async function renameFlow(name) {
  if (!currentFlowId) return;
  await fetch('/api/flows/' + currentFlowId, {
    method: 'PUT', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({name}),
  });
  await loadFlowList();
}
// ── Deploy / stop ─────────────────────────────────────────────────────────────
async function deployFlow() {
  if (!currentFlowId) return;
  await autoSave();
  logSince = 0;
  document.getElementById('log-lines').innerHTML = '';
  await fetch('/api/flows/' + currentFlowId + '/deploy', {method:'POST'});
  await pollStatus();
}
async function stopFlow() {
  if (!currentFlowId) return;
  await fetch('/api/flows/' + currentFlowId + '/stop', {method:'POST'});
  await pollStatus();
}
async function pollStatus() {
  if (!currentFlowId) return;
  const r  = await fetch('/api/flows/' + currentFlowId + '/status');
  const d  = await r.json();
  const running = d.status === 'running';
  document.getElementById('status-badge').textContent = running ? '● running' : '● stopped';
  document.getElementById('status-badge').className   = running ? 'badge-running' : 'badge-stopped';
  document.getElementById('btn-deploy').style.display = running ? 'none'  : '';
  document.getElementById('btn-stop').style.display   = running ? ''      : 'none';
  document.querySelectorAll('.flow-dot').forEach(el => {
    if (el.closest('.flow-item')?.dataset.id === currentFlowId)
      el.classList.toggle('running', running);
  });
  if (running) fetchLog();
}
// ── Log ───────────────────────────────────────────────────────────────────────
let logFetching = false;
async function fetchLog() {
  if (!currentFlowId || logFetching) return;
  logFetching = true;
  try {
    const r = await fetch(`/api/flows/${currentFlowId}/log?since=${logSince}`);
    const d = await r.json();
    logFetching = false;
    if (!d.lines.length) return;
    logSince = d.seq;
    const el  = document.getElementById('log-lines');
    const atBottom = el.scrollHeight - el.clientHeight - el.scrollTop < 40;
    for (const line of d.lines) {
      const div = document.createElement('div');
      div.className = 'log-line' +
        (line.includes('ERROR') ? ' log-error' : line.includes('WARN') ? ' log-warn' : '');
      div.textContent = line;
      el.appendChild(div);
    }
    while (el.children.length > 300) el.removeChild(el.firstChild);
    if (atBottom) el.scrollTop = el.scrollHeight;
  } catch { logFetching = false; }
}
function clearLog() {
  document.getElementById('log-lines').innerHTML = '';
  logSince = 0;
}
// ── Toolbar flash ─────────────────────────────────────────────────────────────
function flashToolbarMsg(msg) {
  const badge = document.getElementById('status-badge');
  const prev  = badge.textContent;
  badge.textContent = msg;
  setTimeout(() => { badge.textContent = prev; }, 1200);
}
// ── Nav bar ───────────────────────────────────────────────────────────────────
(function() {
  const h = window.location.hostname, p = window.location.port;
  document.querySelectorAll('.nav-link').forEach(a => {
    a.href = 'http://' + h + ':' + a.dataset.port + '/';
    if (a.dataset.port === p) a.classList.add('active');
  });
})();
// ── Gateway data (populates datalists) ───────────────────────────────────────
async function loadGatewayData() {
  try {
    const [cr, er] = await Promise.all([
      fetch('/api/gateway/can-buses'),
      fetch('/api/gateway/eth-ifaces'),
    ]);
    const [cd, ed] = await Promise.all([cr.json(), er.json()]);
    document.getElementById('dl-can-ifaces').innerHTML =
      cd.ifaces.map(i => `<option value="${i}"/>`).join('');
    document.getElementById('dl-eth-ifaces').innerHTML =
      ed.ifaces.map(i => `<option value="${i}"/>`).join('');
  } catch {}
}
async function pollGatewayHealth() {
  try {
    const r = await fetch('/api/gateway/health');
    const d = await r.json();
    const badge = document.getElementById('gw-status-badge');
    if (badge) { badge.className = 'gw-status-badge ' + (d.running ? 'on' : 'off'); badge.textContent = d.running ? '● gateway' : '○ gateway'; }
  } catch {
    const badge = document.getElementById('gw-status-badge');
    if (badge) { badge.className = 'gw-status-badge off'; badge.textContent = '○ gateway'; }
  }
}
setInterval(pollGatewayHealth, 2000);
pollGatewayHealth();
// ── Boot ──────────────────────────────────────────────────────────────────────
setInterval(() => { if (currentFlowId) fetchLog(); }, 600);
setInterval(loadGatewayData, 5000);   // refresh interface lists every 5 s
loadFlowList();
loadGatewayData();
</script>
</body>
</html>
"""
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=_PORT, log_level="warning")
