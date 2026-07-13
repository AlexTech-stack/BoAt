"""
BoAt Platform — Trace Editor
View, filter, and edit the frames inside a gateway binary trace file
(the format produced by `boat replay import` / TraceReplayer.convert_to_binary).
Run:  python3 tools/trace_editor.py
Open: http://localhost:8089
"""
from __future__ import annotations

import ipaddress
import os
import sys
import threading
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "boat-platform" / "sdk" / "python"))

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from boat.trace_replay import TraceReplayer, TraceReplayError
from boat.v1 import frame_pb2

_PORT = int(os.environ.get("BOAT_TRACE_EDITOR_PORT", "8089"))
_EXPORT_DIR = Path(__file__).resolve().parent.parent / "traces"
_EXPORT_DIR.mkdir(exist_ok=True)

_current_frames: list[dict[str, Any]] = []
_current_path: Optional[str] = None
_frames_lock = threading.Lock()

app = FastAPI()

# ── Frame <-> dict conversion ─────────────────────────────────────────────────

def _mac_to_str(b: bytes) -> str:
    return ":".join(f"{x:02x}" for x in b) if b else ""

def _mac_from_str(s: str) -> bytes:
    s = (s or "").strip()
    if not s:
        return b""
    return bytes(int(x, 16) for x in s.split(":"))

def _ip_to_str(b: bytes) -> str:
    if not b:
        return ""
    try:
        return str(ipaddress.ip_address(b))
    except ValueError:
        return b.hex()

def _ip_from_str(s: str) -> bytes:
    s = (s or "").strip()
    if not s:
        return b""
    return ipaddress.ip_address(s).packed

def _frame_to_dict(frame, index: int) -> dict[str, Any]:
    d: dict[str, Any] = {
        "index": index,
        "bus_type": frame_pb2.Frame.BusType.Name(frame.bus_type),
        "iface": frame.iface,
        # Sent as a string, not a JSON number: real epoch-nanosecond values
        # (~1.8e18) exceed JS's 53-bit safe-integer range, so a bare number
        # here would get silently rounded by the browser's JSON.parse.
        "timestamp_ns": str(frame.timestamp_ns),
        "payload": frame.payload.hex().upper(),
        "payload_len": len(frame.payload),
        "metadata_type": frame.WhichOneof("metadata"),
    }
    which = d["metadata_type"]
    if which == "can":
        d["can"] = {
            "can_id": frame.can.can_id,
            "can_id_hex": f"0x{frame.can.can_id:X}",
            "dlc": frame.can.dlc,
            "flags": frame.can.flags,
            "channel": frame.can.channel,
        }
    elif which == "eth":
        d["eth"] = {
            "dst_mac": _mac_to_str(frame.eth.dst_mac),
            "src_mac": _mac_to_str(frame.eth.src_mac),
            "ethertype": frame.eth.ethertype,
            "vlan_id": frame.eth.vlan_id,
            "src_ip": _ip_to_str(frame.eth.src_ip),
            "dst_ip": _ip_to_str(frame.eth.dst_ip),
            "ip_version": frame.eth.ip_version,
        }
    elif which == "tcp":
        d["tcp"] = {
            "src_ip": _ip_to_str(frame.tcp.src_ip),
            "dst_ip": _ip_to_str(frame.tcp.dst_ip),
            "ip_version": frame.tcp.ip_version,
            "src_port": frame.tcp.src_port,
            "dst_port": frame.tcp.dst_port,
            "conn_id": frame.tcp.conn_id,
        }
    elif which == "pdu":
        d["pdu"] = {"pdu_id": frame.pdu.pdu_id}
    return d

def _dict_to_frame(d: dict[str, Any]):
    frame = frame_pb2.Frame()
    try:
        frame.bus_type = frame_pb2.Frame.BusType.Value(d.get("bus_type") or "UNSPECIFIED")
    except ValueError as e:
        raise ValueError(f"Invalid bus_type: {e}") from e
    frame.iface = d.get("iface") or ""
    frame.timestamp_ns = int(d.get("timestamp_ns") or 0)
    payload_hex = (d.get("payload") or "").replace(" ", "")
    frame.payload = bytes.fromhex(payload_hex) if payload_hex else b""

    which = d.get("metadata_type")
    if which == "can" and d.get("can"):
        c = d["can"]
        frame.can.CopyFrom(frame_pb2.CanMetadata(
            can_id=int(c.get("can_id") or 0),
            dlc=int(c.get("dlc") or 0),
            flags=int(c.get("flags") or 0),
            channel=int(c.get("channel") or 0),
        ))
    elif which == "eth" and d.get("eth"):
        e = d["eth"]
        frame.eth.CopyFrom(frame_pb2.EthMetadata(
            dst_mac=_mac_from_str(e.get("dst_mac", "")),
            src_mac=_mac_from_str(e.get("src_mac", "")),
            ethertype=int(e.get("ethertype") or 0),
            vlan_id=int(e.get("vlan_id") or 0),
            src_ip=_ip_from_str(e.get("src_ip", "")),
            dst_ip=_ip_from_str(e.get("dst_ip", "")),
            ip_version=int(e.get("ip_version") or 0),
        ))
    elif which == "tcp" and d.get("tcp"):
        t = d["tcp"]
        frame.tcp.CopyFrom(frame_pb2.TcpMetadata(
            src_ip=_ip_from_str(t.get("src_ip", "")),
            dst_ip=_ip_from_str(t.get("dst_ip", "")),
            ip_version=int(t.get("ip_version") or 0),
            src_port=int(t.get("src_port") or 0),
            dst_port=int(t.get("dst_port") or 0),
            conn_id=int(t.get("conn_id") or 0),
        ))
    elif which == "pdu" and d.get("pdu"):
        p = d["pdu"]
        frame.pdu.CopyFrom(frame_pb2.PduMetadata(pdu_id=int(p.get("pdu_id") or 0)))
    return frame

def _monotonic_warnings(frames: list[dict[str, Any]]) -> list[str]:
    """Flag frames whose timestamp_ns goes backwards relative to the previous one.

    The replay engine (src/replay/replay_engine/replay_engine.cpp) schedules
    frames by absolute timestamp_ns using unsigned tick arithmetic anchored
    to the first record; a frame timestamped earlier than that anchor
    underflows the delay computation and the frame effectively never plays.
    This is advisory only -- it doesn't block save/push -- but it's cheap
    to catch here before a silently-dropped frame confuses a replay.
    """
    warnings: list[str] = []
    prev_ts: Optional[int] = None
    for f in frames:
        ts = int(f.get("timestamp_ns", 0) or 0)
        if prev_ts is not None and ts < prev_ts:
            warnings.append(
                f"Frame #{f.get('index')} has timestamp_ns {ts}, which is earlier than "
                f"the preceding frame's {prev_ts}. Non-monotonic timestamps can cause the "
                f"replay engine to stall on that frame indefinitely instead of playing it."
            )
        prev_ts = ts
    return warnings

def _dlc_mismatch_warnings(frames: list[dict[str, Any]]) -> list[str]:
    """Flag CAN/CANFD frames whose can.dlc doesn't match the actual payload length.

    dlc means "how many payload bytes actually get sent" everywhere in this
    codebase, not an ISO 11898-1 DLC code (see frame.proto's CanMetadata.dlc
    comment) -- a mismatch silently truncates the frame (dlc < payload) or
    sends zero padding for the gap (dlc > payload) rather than erroring, so
    it's easy to end up with an unintended one, e.g. via a direct API/curl
    edit that bypasses the editor's own auto-sync.
    """
    warnings: list[str] = []
    for f in frames:
        if f.get("metadata_type") != "can" or not f.get("can"):
            continue
        payload_len = len((f.get("payload") or "").replace(" ", "")) // 2
        dlc = int(f["can"].get("dlc") or 0)
        if dlc != payload_len:
            consequence = (
                f"the frame will be truncated to {dlc} byte(s) on send"
                if dlc < payload_len else
                f"the extra {dlc - payload_len} byte(s) will be sent as zero padding"
            )
            warnings.append(
                f"Frame #{f.get('index')}: DLC ({dlc}) does not match payload length "
                f"({payload_len} bytes) -- {consequence}."
            )
    return warnings

def _reindex() -> None:
    for i, f in enumerate(_current_frames):
        f["index"] = i

def _resolve_path(path_str: str) -> Path:
    fp = Path(path_str).expanduser()
    if not fp.is_absolute():
        fp = _EXPORT_DIR / fp.name
    return fp

# ── API routes ────────────────────────────────────────────────────────────────

@app.get("/api/trace/list")
def api_trace_list():
    files = []
    for d in [_EXPORT_DIR, Path.home()]:
        try:
            for f in Path(d).glob("*.trace"):
                files.append(str(f))
        except Exception:
            pass
    files = sorted(set(files))[:200]
    return {"files": files, "export_dir": str(_EXPORT_DIR)}

@app.post("/api/trace/new")
def api_trace_new():
    global _current_frames, _current_path
    with _frames_lock:
        _current_frames = []
        _current_path = None
    return {"status": "ok"}

@app.get("/api/trace/load")
def api_trace_load(path: str = Query(...)):
    global _current_frames, _current_path
    fp = _resolve_path(path)
    if not fp.exists():
        raise HTTPException(404, f"File not found: {fp}")
    try:
        frames = TraceReplayer.parse_binary(fp.read_bytes())
    except TraceReplayError as e:
        raise HTTPException(400, f"Failed to parse trace: {e}")
    with _frames_lock:
        _current_frames = [_frame_to_dict(f, i) for i, f in enumerate(frames)]
        _current_path = str(fp)
    return {"path": str(fp), "count": len(_current_frames)}

@app.post("/api/trace/save")
def api_trace_save(body: dict):
    global _current_path
    path_str = body.get("path") or _current_path
    if not path_str:
        raise HTTPException(400, "No path given and no trace currently loaded")
    fp = _resolve_path(path_str)

    with _frames_lock:
        try:
            frames = [_dict_to_frame(d) for d in _current_frames]
        except ValueError as e:
            raise HTTPException(400, f"Invalid frame data: {e}")
        binary = TraceReplayer.frames_to_binary(frames)

    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_bytes(binary)
    with _frames_lock:
        _current_path = str(fp)
        warnings = _monotonic_warnings(_current_frames) + _dlc_mismatch_warnings(_current_frames)
    return {"status": "ok", "path": str(fp), "count": len(frames), "warnings": warnings}

@app.get("/api/frames")
def api_frames():
    with _frames_lock:
        return {"frames": _current_frames, "path": _current_path}

@app.put("/api/frames/{index}")
def api_frame_update(index: int, body: dict):
    with _frames_lock:
        if index < 0 or index >= len(_current_frames):
            raise HTTPException(404, f"Frame index {index} out of range")
        try:
            _dict_to_frame(body)  # validate before accepting
        except ValueError as e:
            raise HTTPException(400, f"Invalid frame data: {e}")
        body["index"] = index
        _current_frames[index] = body
    return {"status": "ok"}

@app.delete("/api/frames/{index}")
def api_frame_delete(index: int):
    with _frames_lock:
        if index < 0 or index >= len(_current_frames):
            raise HTTPException(404, f"Frame index {index} out of range")
        del _current_frames[index]
        _reindex()
        count = len(_current_frames)
    return {"status": "ok", "count": count}

@app.post("/api/frames/delete-batch")
def api_frames_delete_batch(body: dict):
    global _current_frames
    indices = {int(i) for i in body.get("indices", [])}
    with _frames_lock:
        _current_frames = [f for i, f in enumerate(_current_frames) if i not in indices]
        _reindex()
        count = len(_current_frames)
    return {"status": "ok", "count": count}

@app.post("/api/trace/push")
def api_trace_push(body: dict):
    """Upload the current in-memory frames to a running gateway via
    ReplayService.ImportTraceData.

    This is the only way to load an edited trace back into a simulation:
    `boat replay import` always runs client-side format conversion and only
    accepts source formats (.asc/.blf/.pcap), not this already-binary
    format, so re-importing an edited trace has to go through this RPC
    directly instead.
    """
    trace_id = body.get("trace_id")
    if not trace_id:
        raise HTTPException(400, "Missing 'trace_id'")
    gateway = body.get("gateway") or "localhost:50051"

    with _frames_lock:
        try:
            frames = [_dict_to_frame(d) for d in _current_frames]
        except ValueError as e:
            raise HTTPException(400, f"Invalid frame data: {e}")
        binary = TraceReplayer.frames_to_binary(frames)
        warnings = _monotonic_warnings(_current_frames) + _dlc_mismatch_warnings(_current_frames)

    try:
        from boat.client import BoAtClient
        from boat.v1 import replay_pb2
    except ImportError as e:
        raise HTTPException(500, f"gRPC stubs unavailable: {e}")

    try:
        client = BoAtClient(gateway)
        resp = client.replay.ImportTraceData(replay_pb2.ImportTraceDataRequest(
            trace_id=trace_id,
            format="TRACE",
            data=binary,
        ))
    except Exception as e:
        raise HTTPException(502, f"ImportTraceData RPC failed: {e}")

    if not resp.accepted:
        msg = resp.error.message if resp.error and resp.error.message else "unknown error"
        raise HTTPException(502, f"ImportTraceData rejected: {msg}")

    return {"status": "ok", "trace_id": trace_id, "gateway": gateway, "count": len(frames), "warnings": warnings}

@app.post("/api/frames/insert")
def api_frame_insert(body: dict):
    frame = body.get("frame")
    if frame is None:
        raise HTTPException(400, "Missing 'frame' in body")
    try:
        _dict_to_frame(frame)  # validate before accepting
    except ValueError as e:
        raise HTTPException(400, f"Invalid frame data: {e}")
    with _frames_lock:
        pos = int(body.get("after_index", -1)) + 1
        pos = max(0, min(pos, len(_current_frames)))
        _current_frames.insert(pos, frame)
        _reindex()
        count = len(_current_frames)
    return {"status": "ok", "index": pos, "count": count}

# ── HTML ──────────────────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>BoAt — Trace Editor</title>
<style>
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
  --mono:   "SFMono-Regular",Consolas,"Liberation Mono",monospace;
}
* { margin:0; padding:0; box-sizing:border-box; }
body { background:var(--bg); color:var(--text); font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif; font-size:14px; }
header {
  height:46px; background:var(--panel); border-bottom:1px solid var(--border);
  display:flex; align-items:center; padding:0 16px; gap:12px;
}
.logo { font-weight:700; color:var(--blue); font-size:16px; }
.subtitle { color:var(--muted); font-size:13px; }
.spacer { flex:1; }
#panel-nav {
  height:32px; background:#0d1117; border-bottom:1px solid var(--border);
  display:flex; align-items:center; padding:0 16px; gap:8px;
}
#panel-nav .nav-link { color:var(--muted); font-size:12px; text-decoration:none; padding:4px 10px; border-radius:4px; }
#panel-nav .nav-link:hover { color:var(--text); background:var(--panel); }
#panel-nav .nav-link.active { color:var(--blue); background:rgba(88,166,255,0.1); }
.toolbar, .filterbar {
  display:flex; align-items:center; gap:8px; padding:8px 16px; border-bottom:1px solid var(--border);
  background:var(--panel); flex-wrap:wrap;
}
.filterbar { background:var(--bg); }
.toolbar input, .filterbar input, .toolbar select, .filterbar select {
  padding:5px 8px; background:var(--bg); border:1px solid var(--border); border-radius:4px; color:var(--text);
  font-size:12px; font-family:var(--mono);
}
.filterbar input, .filterbar select { background:var(--panel); }
.toolbar label, .filterbar label { font-size:11px; color:var(--muted); display:flex; align-items:center; gap:4px; }
button, button.btn { padding:5px 10px; border:1px solid var(--border); border-radius:4px; background:var(--bg); color:var(--text); cursor:pointer; font-size:12px; }
button:hover { background:var(--panel); border-color:var(--blue); }
.btn-primary { color:var(--blue) !important; border-color:var(--blue) !important; }
.btn-primary:hover { background:rgba(88,166,255,0.1) !important; }
.btn-add { color:var(--green) !important; border-color:var(--green) !important; }
.btn-add:hover { background:rgba(63,185,80,0.1) !important; }
.btn-danger { color:var(--red) !important; border-color:var(--red) !important; }
.btn-danger:hover { background:rgba(248,81,73,0.1) !important; }
.table-wrap { height:calc(100vh - 156px); overflow:auto; }
table { width:100%; border-collapse:collapse; font-size:12px; }
th { text-align:left; padding:6px 8px; border-bottom:2px solid var(--border); color:var(--muted); font-weight:600; font-size:11px; position:sticky; top:0; background:var(--bg); white-space:nowrap; }
td { padding:5px 8px; border-bottom:1px solid var(--border); font-family:var(--mono); font-size:11px; white-space:nowrap; }
tr:hover td { background:rgba(88,166,255,0.03); }
tr.selected td { background:rgba(88,166,255,0.1); }
td.payload-cell { max-width:320px; overflow:hidden; text-overflow:ellipsis; }
.row-actions button { padding:2px 6px; font-size:10px; }
.empty-state { text-align:center; padding:60px 20px; color:var(--muted); }
.empty-state h2 { font-size:20px; margin-bottom:8px; }
.empty-state p { font-size:14px; margin-bottom:16px; }
.status-bar { padding:4px 16px; font-size:11px; color:var(--muted); border-top:1px solid var(--border); background:var(--panel); }
.toast {
  position:fixed; bottom:20px; right:20px; padding:10px 20px; border-radius:6px; font-size:13px;
  z-index:9999; animation:fadeIn 0.2s;
}
.toast.info { background:var(--blue); color:#fff; }
.toast.error { background:var(--red); color:#fff; }
.toast.success { background:var(--green); color:#fff; }
@keyframes fadeIn { from{opacity:0;transform:translateY(10px)} to{opacity:1;transform:translateY(0)} }
::-webkit-scrollbar { width:4px; height:4px; }
::-webkit-scrollbar-track { background:transparent; }
::-webkit-scrollbar-thumb { background:var(--border); border-radius:2px; }
.modal-overlay {
  position:fixed; inset:0; background:rgba(0,0,0,0.6); display:flex; align-items:center; justify-content:center; z-index:1000;
}
.modal {
  width:520px; max-height:85vh; overflow-y:auto; background:var(--panel); border:1px solid var(--border); border-radius:8px; padding:16px;
}
.modal h3 { font-size:14px; margin-bottom:12px; }
.field { margin-bottom:8px; }
.field label { display:block; font-size:11px; color:var(--muted); margin-bottom:2px; }
.field input, .field select, .field textarea {
  width:100%; padding:5px 8px; background:var(--bg); border:1px solid var(--border); border-radius:4px; color:var(--text); font-size:13px; font-family:var(--mono);
}
.field textarea { min-height:60px; resize:vertical; }
.field input:focus, .field select:focus, .field textarea:focus { border-color:var(--blue); outline:none; }
.field-row { display:flex; gap:8px; }
.field-row .field { flex:1; }
.modal-actions { display:flex; justify-content:flex-end; gap:8px; margin-top:16px; }
.field-hint {
  font-size:11px; color:var(--muted); line-height:1.6; background:var(--bg);
  border:1px solid var(--border); border-radius:4px; padding:6px 8px; margin:-2px 0 8px;
}
.field-hint code { font-family:var(--mono); color:var(--text); }
.ts-preview {
  font-family:var(--mono); font-size:14px; letter-spacing:0.5px; margin-top:4px;
  padding:6px 8px; background:var(--bg); border:1px solid var(--border); border-radius:4px;
}
.ts-s  { color:var(--muted); }
.ts-ms { color:var(--blue); }
.ts-us { color:var(--green); }
.ts-ns { color:var(--orange); }
.ts-dot { color:var(--red); font-weight:700; padding:0 1px; }
.ts-legend { font-size:10px; color:var(--muted); margin-top:3px; }
.ts-legend .ts-s, .ts-legend .ts-ms, .ts-legend .ts-us, .ts-legend .ts-ns { font-weight:700; }
</style>
</head>
<body>

<header>
  <span class="logo">⛵ BoAt</span>
  <span class="subtitle">Trace Editor</span>
  <span class="spacer"></span>
</header>

<nav id="panel-nav">
  <a class="nav-link" data-port="8086">Launcher</a>
  <a class="nav-link" data-port="8080">Dashboard</a>
  <a class="nav-link" data-port="8081">Nodes</a>
  <a class="nav-link" data-port="8082">Commander</a>
  <a class="nav-link" data-port="8083">Recorder</a>
  <a class="nav-link" data-port="8087">PDU Editor</a>
  <a class="nav-link" data-port="8088">Trace Analyzer</a>
  <a class="nav-link" data-port="8089" style="color:var(--blue)">Trace Editor</a>
</nav>

<div class="toolbar">
  <select id="file-select" style="min-width:220px">
    <option value="">— select .trace file —</option>
  </select>
  <button class="btn-primary" onclick="loadSelected()">Load</button>
  <button onclick="loadFile()">Browse...</button>
  <button onclick="newTrace()">New</button>
  <span class="spacer"></span>
  <button class="btn-add" onclick="openInsertModal(frames.length - 1)">+ Add Frame</button>
  <button class="btn-danger" id="delete-selected-btn" onclick="deleteSelected()" disabled>Delete Selected</button>
  <button class="btn-primary" onclick="saveFile()">Save</button>
  <button onclick="saveAs()">Save As</button>
  <button class="btn-add" onclick="pushToGateway()">Push to Gateway</button>
</div>

<div class="filterbar">
  <label>Bus Type
    <select id="filter-bus-type" onchange="renderTable()">
      <option value="">All</option>
      <option value="CAN">CAN</option>
      <option value="CANFD">CANFD</option>
      <option value="ETHERNET">ETHERNET</option>
      <option value="TCP">TCP</option>
      <option value="PDU">PDU</option>
      <option value="UNSPECIFIED">UNSPECIFIED</option>
    </select>
  </label>
  <label>Iface <input id="filter-iface" placeholder="substring" oninput="renderTable()" style="width:100px"/></label>
  <label>CAN ID <input id="filter-can-id" placeholder="0x123 or 291" oninput="renderTable()" style="width:110px"/></label>
  <label>Timestamp ≥ (ns) <input id="filter-ts-min" type="number" oninput="renderTable()" style="width:130px"/></label>
  <label>Timestamp ≤ (ns) <input id="filter-ts-max" type="number" oninput="renderTable()" style="width:130px"/></label>
  <button onclick="clearFilters()">Clear Filters</button>
</div>

<div class="table-wrap" id="table-wrap">
  <div class="empty-state" id="empty-state">
    <h2>No trace loaded</h2>
    <p>Select a .trace file above, or click New to start an empty trace.</p>
  </div>
  <table id="frame-table" style="display:none">
    <thead><tr>
      <th style="width:24px"><input type="checkbox" onchange="toggleSelectAll(this)"/></th>
      <th>#</th><th>Bus Type</th><th>Iface</th><th>Timestamp (ns)</th><th>Summary</th><th>Payload</th><th>Len</th><th>Actions</th>
    </tr></thead>
    <tbody id="frame-tbody"></tbody>
  </table>
</div>
<div class="status-bar" id="status-bar"></div>

<div id="modal-overlay" class="modal-overlay" style="display:none">
  <div class="modal">
    <h3 id="modal-title">Edit Frame</h3>
    <div class="field-row">
      <div class="field"><label>Bus Type</label>
        <select id="m-bus-type" onchange="onModalBusTypeChange()">
          <option value="CAN">CAN</option>
          <option value="CANFD">CANFD</option>
          <option value="ETHERNET">ETHERNET</option>
          <option value="TCP">TCP</option>
          <option value="PDU">PDU</option>
          <option value="UNSPECIFIED">UNSPECIFIED</option>
        </select>
      </div>
      <div class="field"><label>Iface</label><input id="m-iface"/></div>
    </div>
    <div class="field">
      <label>Timestamp (ns)</label>
      <input id="m-ts" type="text" inputmode="numeric" pattern="[0-9]*" placeholder="0" oninput="updateTsPreview()"/>
      <div id="m-ts-preview" class="ts-preview"></div>
      <div class="ts-legend">Grouped right-to-left in 3s: <span class="ts-s">seconds</span> . <span class="ts-ms">milliseconds</span> . <span class="ts-us">microseconds</span> . <span class="ts-ns">nanoseconds</span> — the number above is unchanged, this is just a reading aid.</div>
    </div>
    <div class="field"><label>Payload (hex)</label><textarea id="m-payload" oninput="onPayloadInput()" placeholder="AABBCCDD"></textarea></div>

    <div id="m-can-fields">
      <h3 style="font-size:12px;color:var(--muted);margin-top:12px">CAN metadata</h3>
      <div class="field-row">
        <div class="field"><label>CAN ID (hex or dec)</label><input id="m-can-id"/></div>
        <div class="field"><label>DLC</label><input id="m-can-dlc" type="number" min="0" max="64" oninput="onDlcInput()"/></div>
      </div>
      <div class="field-hint">
        DLC is simply <strong>how many bytes of the payload actually get sent</strong> — it is
        <em>not</em> an ISO 11898-1 DLC code. It normally auto-fills to match Payload's length above
        (edit Payload and this updates); if you edit DLC by hand to something smaller than the payload,
        the frame gets <strong>truncated</strong> to that many bytes, the rest of the payload is dropped.
        For CAN&nbsp;FD, if the resulting length isn't already one of 0-8/12/16/20/24/32/48/64 bytes, it
        gets rounded up and zero-padded automatically when sent — you don't need to pre-pad it yourself.
      </div>
      <div id="m-dlc-warning" class="field-hint" style="display:none;border-color:var(--red);color:var(--red)"></div>
      <div class="field-row">
        <div class="field"><label>Flags</label><input id="m-can-flags"/></div>
        <div class="field"><label>Channel</label><input id="m-can-channel" type="number" min="0"/></div>
      </div>
      <div class="field-hint">
        Flags is a bitmask, combine with bitwise OR: <code>0x01</code> = CANFD_BRS (bit-rate switch),
        <code>0x02</code> = CANFD_ESI (error state indicator), <code>0x04</code> = CANFD_FDF (FD frame format).
        E.g. <code>0x05</code> = FDF + BRS, a typical CAN FD frame. Leave 0 for classic CAN.
      </div>
    </div>

    <div id="m-eth-fields" style="display:none">
      <h3 style="font-size:12px;color:var(--muted);margin-top:12px">Ethernet metadata</h3>
      <div class="field-row">
        <div class="field"><label>Src MAC</label><input id="m-eth-src-mac" placeholder="aa:bb:cc:dd:ee:ff"/></div>
        <div class="field"><label>Dst MAC</label><input id="m-eth-dst-mac" placeholder="aa:bb:cc:dd:ee:ff"/></div>
      </div>
      <div class="field-row">
        <div class="field"><label>EtherType</label><input id="m-eth-ethertype" placeholder="0x0800"/></div>
        <div class="field"><label>VLAN ID</label><input id="m-eth-vlan" type="number" min="0"/></div>
      </div>
      <div class="field-row">
        <div class="field"><label>Src IP</label><input id="m-eth-src-ip"/></div>
        <div class="field"><label>Dst IP</label><input id="m-eth-dst-ip"/></div>
      </div>
      <div class="field"><label>IP Version</label><input id="m-eth-ipver" type="number" min="0" max="6"/></div>
      <div class="field-hint">
        Ethernet frames have no packed flags field. VLAN ID <code>0</code> means untagged;
        any other value tags the frame with that VLAN.
      </div>
    </div>

    <div id="m-tcp-fields" style="display:none">
      <h3 style="font-size:12px;color:var(--muted);margin-top:12px">TCP metadata</h3>
      <div class="field-row">
        <div class="field"><label>Src IP</label><input id="m-tcp-src-ip"/></div>
        <div class="field"><label>Dst IP</label><input id="m-tcp-dst-ip"/></div>
      </div>
      <div class="field-row">
        <div class="field"><label>Src Port</label><input id="m-tcp-src-port" type="number" min="0" max="65535"/></div>
        <div class="field"><label>Dst Port</label><input id="m-tcp-dst-port" type="number" min="0" max="65535"/></div>
      </div>
      <div class="field-row">
        <div class="field"><label>IP Version</label><input id="m-tcp-ipver" type="number" min="0" max="6"/></div>
        <div class="field"><label>Conn Id (-1=new, -2=close)</label><input id="m-tcp-conn-id" type="number"/></div>
      </div>
      <div class="field-hint">
        TCP has no packed flags field either — connection lifecycle is carried entirely by Conn Id:
        <code>-1</code> opens a new connection, <code>-2</code> closes one, <code>&gt;=0</code> reuses an existing connection.
      </div>
    </div>

    <div id="m-pdu-fields" style="display:none">
      <h3 style="font-size:12px;color:var(--muted);margin-top:12px">PDU metadata</h3>
      <div class="field"><label>PDU Id</label><input id="m-pdu-id" placeholder="0x1 or 1"/></div>
      <div class="field-hint">PDU frames have no packed flags field — just the PDU Id shown above.</div>
    </div>

    <div class="modal-actions">
      <button onclick="closeModal()">Cancel</button>
      <button class="btn-primary" onclick="saveModal()">Save</button>
    </div>
  </div>
</div>

<div id="toast-container"></div>

<script>
// ── State ──────────────────────────────────────────────────────────────────
let frames = [];
let currentPath = null;
let selected = new Set();
let editingIndex = null;      // set when editing an existing frame
let insertAfterIndex = null;  // set when inserting a new frame

// ── API helpers ────────────────────────────────────────────────────────────
async function api(method, url, body) {
  const opts = {method, headers:{"Accept":"application/json"}};
  if (body !== undefined) {opts.headers["Content-Type"]="application/json"; opts.body=JSON.stringify(body);}
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

function toast(msg, type="info") {
  const el = document.createElement("div");
  el.className = "toast " + type; el.textContent = msg;
  document.getElementById("toast-container").appendChild(el);
  setTimeout(() => el.remove(), 3000);
}

function esc(s) {
  if (s == null) return "";
  return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")
    .replace(/"/g,"&quot;").replace(/'/g,"&#39;");
}

function parseIntFlexible(v) {
  if (v === undefined || v === null || v === "") return 0;
  v = String(v).trim();
  if (v.toLowerCase().startsWith("0x")) return parseInt(v, 16) || 0;
  return parseInt(v, 10) || 0;
}

// Splits a numeric string into 3-digit groups from the right and colors the
// last three (ms/µs/ns) so large epoch-nanosecond timestamps are easier to
// place visually. Pure string manipulation on purpose -- real epoch-ns
// values (~1.8e18) exceed JS's 53-bit safe-integer range, so this must never
// round-trip through parseInt()/Number(), or it silently loses precision.
function formatTimestampGroups(raw) {
  const digits = String(raw || "").replace(/[^0-9]/g, "");
  if (!digits) return "";
  const groups = [];
  for (let i = digits.length; i > 0; i -= 3) {
    groups.unshift(digits.slice(Math.max(0, i - 3), i));
  }
  const n = groups.length;
  return groups.map((g, idx) => {
    const fromRight = n - idx;
    const cls = fromRight === 1 ? "ts-ns" : fromRight === 2 ? "ts-us" : fromRight === 3 ? "ts-ms" : "ts-s";
    return `<span class="${cls}">${g}</span>`;
  }).join('<span class="ts-dot">.</span>');
}

function updateTsPreview() {
  const raw = document.getElementById("m-ts").value;
  document.getElementById("m-ts-preview").innerHTML = formatTimestampGroups(raw) || '<span class="ts-s">0</span>';
}

// ── File loading ──────────────────────────────────────────────────────────
async function refreshFileList() {
  try {
    const r = await api("GET","/api/trace/list");
    const sel = document.getElementById("file-select");
    const current = sel.value;
    sel.innerHTML = '<option value="">— select .trace file —</option>' +
      r.files.map(f => `<option value="${esc(f)}">${esc(f)}</option>`).join("");
    if (current && [...sel.options].some(o => o.value === current)) sel.value = current;
  } catch(e) {}
}

async function loadSelected() {
  const path = document.getElementById("file-select").value;
  if (!path) { toast("Select a file from the dropdown first","error"); return; }
  await _loadPath(path);
}

async function loadFile() {
  const fp = prompt("Enter full path to a .trace file:");
  if (!fp) return;
  await _loadPath(fp);
}

async function _loadPath(path) {
  try {
    const r = await api("GET","/api/trace/load?path=" + encodeURIComponent(path));
    currentPath = r.path;
    const fr = await api("GET","/api/frames");
    frames = fr.frames;
    selected.clear();
    renderTable();
    toast(`Loaded ${r.count} frames from ${path.split(/[\\/]/).pop()}`,"success");
  } catch(e) { toast("Load failed: " + e.message,"error"); }
}

async function newTrace() {
  await api("POST","/api/trace/new");
  frames = []; currentPath = null; selected.clear();
  renderTable();
  toast("New empty trace","success");
}

function showWarnings(warnings) {
  (warnings || []).forEach(w => toast(w, "error"));
}

async function saveFile() {
  if (!currentPath) { saveAs(); return; }
  try {
    const r = await api("POST","/api/trace/save", {path: currentPath});
    toast(`Saved ${r.count} frames to ${r.path}`,"success");
    showWarnings(r.warnings);
  } catch(e) { toast("Save failed: " + e.message,"error"); }
}

async function saveAs() {
  const name = prompt("Filename (relative paths are saved under the traces/ dir):", currentPath ? currentPath.split(/[\\/]/).pop() : "edited.trace");
  if (!name) return;
  try {
    const r = await api("POST","/api/trace/save", {path: name});
    currentPath = r.path;
    refreshFileList();
    toast(`Saved ${r.count} frames to ${r.path}`,"success");
    showWarnings(r.warnings);
  } catch(e) { toast("Save failed: " + e.message,"error"); }
}

async function pushToGateway() {
  const defaultId = currentPath ? currentPath.split(/[\\/]/).pop().replace(/\.[^.]+$/, "") : "edited";
  const traceId = prompt("Trace ID to import as on the gateway:", defaultId);
  if (!traceId) return;
  const gateway = prompt("Gateway address:", "localhost:50051");
  if (!gateway) return;
  try {
    const r = await api("POST", "/api/trace/push", {trace_id: traceId, gateway: gateway});
    toast(`Pushed ${r.count} frames to gateway as trace_id "${r.trace_id}". Run: boat replay start --trace ${r.trace_id}`,"success");
    showWarnings(r.warnings);
  } catch(e) { toast("Push failed: " + e.message,"error"); }
}

// ── Filtering + table rendering ────────────────────────────────────────────
function filteredFrames() {
  const busType = document.getElementById("filter-bus-type").value;
  const iface = (document.getElementById("filter-iface").value || "").toLowerCase();
  const canIdStr = (document.getElementById("filter-can-id").value || "").trim();
  const tsMin = document.getElementById("filter-ts-min").value;
  const tsMax = document.getElementById("filter-ts-max").value;
  const canId = canIdStr ? parseIntFlexible(canIdStr) : null;

  return frames.filter(f => {
    if (busType && f.bus_type !== busType) return false;
    if (iface && !(f.iface||"").toLowerCase().includes(iface)) return false;
    if (canId !== null && !(f.can && f.can.can_id === canId)) return false;
    if (tsMin !== "" && f.timestamp_ns < parseInt(tsMin)) return false;
    if (tsMax !== "" && f.timestamp_ns > parseInt(tsMax)) return false;
    return true;
  });
}

function clearFilters() {
  document.getElementById("filter-bus-type").value = "";
  document.getElementById("filter-iface").value = "";
  document.getElementById("filter-can-id").value = "";
  document.getElementById("filter-ts-min").value = "";
  document.getElementById("filter-ts-max").value = "";
  renderTable();
}

function summaryFor(f) {
  if (f.metadata_type === "can" && f.can) {
    let s = `ID ${f.can.can_id_hex} DLC ${f.can.dlc} Ch ${f.can.channel}`;
    if (f.can.flags) s += ` flags=0x${f.can.flags.toString(16)}`;
    return s;
  }
  if (f.metadata_type === "eth" && f.eth) {
    return `${f.eth.src_mac||'?'} → ${f.eth.dst_mac||'?'} et=0x${(f.eth.ethertype||0).toString(16)}`;
  }
  if (f.metadata_type === "tcp" && f.tcp) {
    return `${f.tcp.src_ip}:${f.tcp.src_port} → ${f.tcp.dst_ip}:${f.tcp.dst_port} conn=${f.tcp.conn_id}`;
  }
  if (f.metadata_type === "pdu" && f.pdu) {
    return `PduId ${f.pdu.pdu_id}`;
  }
  return "";
}

function renderTable() {
  const visible = filteredFrames();
  document.getElementById("empty-state").style.display = frames.length ? "none" : "block";
  document.getElementById("frame-table").style.display = frames.length ? "table" : "none";

  const tb = document.getElementById("frame-tbody");
  tb.innerHTML = visible.map(f => `
    <tr class="${selected.has(f.index) ? 'selected' : ''}">
      <td><input type="checkbox" ${selected.has(f.index)?'checked':''} onchange="onRowCheck(${f.index}, this.checked)"/></td>
      <td>${f.index}</td>
      <td>${esc(f.bus_type)}</td>
      <td>${esc(f.iface)}</td>
      <td>${f.timestamp_ns}</td>
      <td>${esc(summaryFor(f))}</td>
      <td class="payload-cell" title="${esc(f.payload)}">${esc(f.payload)}</td>
      <td>${Math.floor((f.payload||"").length/2)}</td>
      <td class="row-actions">
        <button onclick="openEditModal(${f.index})">Edit</button>
        <button onclick="openInsertModal(${f.index})">Insert After</button>
        <button class="btn-danger" onclick="deleteRow(${f.index})">Delete</button>
      </td>
    </tr>
  `).join("");

  document.getElementById("status-bar").textContent =
    `${visible.length} / ${frames.length} frames shown` + (currentPath ? ` — ${currentPath}` : " — unsaved") +
    (selected.size ? ` — ${selected.size} selected` : "");
  document.getElementById("delete-selected-btn").disabled = selected.size === 0;
}

function onRowCheck(index, checked) {
  if (checked) selected.add(index); else selected.delete(index);
  renderTable();
}

function toggleSelectAll(cb) {
  const visible = filteredFrames();
  if (cb.checked) visible.forEach(f => selected.add(f.index));
  else visible.forEach(f => selected.delete(f.index));
  renderTable();
}

// ── Row operations ─────────────────────────────────────────────────────────
async function deleteRow(index) {
  if (!confirm(`Delete frame #${index}?`)) return;
  try {
    await api("DELETE", "/api/frames/" + index);
    const fr = await api("GET","/api/frames");
    frames = fr.frames;
    selected.clear();
    renderTable();
    toast("Frame deleted","info");
  } catch(e) { toast("Delete failed: " + e.message,"error"); }
}

async function deleteSelected() {
  if (!selected.size) return;
  const ids = [...selected];
  if (!confirm(`Delete ${ids.length} selected frame(s)?`)) return;
  try {
    await api("POST", "/api/frames/delete-batch", {indices: ids});
    const fr = await api("GET","/api/frames");
    frames = fr.frames;
    selected.clear();
    renderTable();
    toast(`${ids.length} frame(s) deleted`,"info");
  } catch(e) { toast("Delete failed: " + e.message,"error"); }
}

// ── Modal: edit / insert ────────────────────────────────────────────────────
function openEditModal(index) {
  const f = frames.find(x => x.index === index);
  if (!f) return;
  editingIndex = index;
  insertAfterIndex = null;
  document.getElementById("modal-title").textContent = `Edit Frame #${index}`;
  fillModal(f);
  document.getElementById("modal-overlay").style.display = "flex";
}

function openInsertModal(afterIndex) {
  // Pre-fill from the adjacent frame as a convenience starting point, if any.
  // For "insert at start" (afterIndex < 0) there is no preceding frame, so
  // clone the frame currently at position 0 instead: the replay engine
  // schedules frames by absolute timestamp_ns and anchors its base tick to
  // the first record, using unsigned arithmetic that underflows (and hangs
  // that frame ~forever) if an inserted frame's timestamp comes out before
  // it -- defaulting to a timestamp_ns of 0 here would walk straight into
  // that. Reusing the first frame's own timestamp keeps the default safe;
  // the user can still edit it to whatever value they actually want.
  const base = frames.find(x => x.index === afterIndex) || (afterIndex < 0 ? frames[0] : null);
  editingIndex = null;
  insertAfterIndex = afterIndex;
  document.getElementById("modal-title").textContent =
    afterIndex < 0 ? "Insert Frame at Start" : `Insert Frame After #${afterIndex}`;
  fillModal(base || {bus_type:"CAN", iface:"", timestamp_ns:0, payload:"", metadata_type:"can",
    can:{can_id:0, dlc:0, flags:0, channel:1}});
  document.getElementById("modal-overlay").style.display = "flex";
}

function closeModal() {
  document.getElementById("modal-overlay").style.display = "none";
  editingIndex = null; insertAfterIndex = null;
}

function fillModal(f) {
  document.getElementById("m-bus-type").value = f.bus_type || "CAN";
  document.getElementById("m-iface").value = f.iface || "";
  document.getElementById("m-ts").value = f.timestamp_ns || "0";
  updateTsPreview();
  document.getElementById("m-payload").value = f.payload || "";

  const c = f.can || {};
  document.getElementById("m-can-id").value = c.can_id_hex || c.can_id || 0;
  document.getElementById("m-can-dlc").value = c.dlc || 0;
  document.getElementById("m-can-flags").value = c.flags || 0;
  document.getElementById("m-can-channel").value = c.channel || 0;

  const e = f.eth || {};
  document.getElementById("m-eth-src-mac").value = e.src_mac || "";
  document.getElementById("m-eth-dst-mac").value = e.dst_mac || "";
  document.getElementById("m-eth-ethertype").value = e.ethertype ? "0x" + e.ethertype.toString(16) : "";
  document.getElementById("m-eth-vlan").value = e.vlan_id || 0;
  document.getElementById("m-eth-src-ip").value = e.src_ip || "";
  document.getElementById("m-eth-dst-ip").value = e.dst_ip || "";
  document.getElementById("m-eth-ipver").value = e.ip_version || 0;

  const t = f.tcp || {};
  document.getElementById("m-tcp-src-ip").value = t.src_ip || "";
  document.getElementById("m-tcp-dst-ip").value = t.dst_ip || "";
  document.getElementById("m-tcp-src-port").value = t.src_port || 0;
  document.getElementById("m-tcp-dst-port").value = t.dst_port || 0;
  document.getElementById("m-tcp-ipver").value = t.ip_version || 0;
  document.getElementById("m-tcp-conn-id").value = t.conn_id ?? -1;

  const p = f.pdu || {};
  document.getElementById("m-pdu-id").value = p.pdu_id || 0;

  onModalBusTypeChange();
  checkDlcMismatch();  // surface pre-existing dlc/payload mismatches on open, without "fixing" them
}

function onModalBusTypeChange() {
  const bt = document.getElementById("m-bus-type").value;
  document.getElementById("m-can-fields").style.display = (bt==="CAN"||bt==="CANFD") ? "block" : "none";
  document.getElementById("m-eth-fields").style.display = bt==="ETHERNET" ? "block" : "none";
  document.getElementById("m-tcp-fields").style.display = bt==="TCP" ? "block" : "none";
  document.getElementById("m-pdu-fields").style.display = bt==="PDU" ? "block" : "none";
}

// DLC means "how many payload bytes actually get sent" everywhere in this
// codebase -- it is NOT an ISO 11898-1 DLC code (see frame.proto's
// CanMetadata.dlc comment). Auto-fill it from the payload by default so
// editing a frame can't silently create a dlc/payload mismatch; a manual
// edit to DLC afterward still works (e.g. to deliberately truncate), but
// gets flagged so it's clear it's no longer just "the payload length".
function onPayloadInput() {
  const payload = (document.getElementById("m-payload").value || "").replace(/\s+/g,"");
  document.getElementById("m-can-dlc").value = Math.floor(payload.length / 2);
  checkDlcMismatch();
}

function onDlcInput() {
  checkDlcMismatch();
}

function checkDlcMismatch() {
  const payload = (document.getElementById("m-payload").value || "").replace(/\s+/g,"");
  const payloadLen = Math.floor(payload.length / 2);
  const dlc = parseInt(document.getElementById("m-can-dlc").value) || 0;
  const warn = document.getElementById("m-dlc-warning");
  if (dlc === payloadLen) {
    warn.style.display = "none";
    return;
  }
  warn.style.display = "block";
  warn.textContent = dlc < payloadLen
    ? `DLC (${dlc}) is less than the payload (${payloadLen} bytes) -- only the first ${dlc} byte(s) will actually be sent, the rest of the payload is dropped on save/replay.`
    : `DLC (${dlc}) is more than the payload (${payloadLen} bytes) -- there's no data for the extra ${dlc - payloadLen} byte(s); they'll be sent as zero padding.`;
}

function collectModal() {
  const bt = document.getElementById("m-bus-type").value;
  const payload = (document.getElementById("m-payload").value || "").replace(/\s+/g,"");
  // Kept as a string end-to-end -- see formatTimestampGroups() above for why.
  const tsDigits = (document.getElementById("m-ts").value || "").replace(/[^0-9]/g, "");
  const frame = {
    bus_type: bt,
    iface: document.getElementById("m-iface").value || "",
    timestamp_ns: tsDigits || "0",
    payload: payload,
    payload_len: Math.floor(payload.length / 2),
  };
  if (bt === "CAN" || bt === "CANFD") {
    frame.metadata_type = "can";
    const canId = parseIntFlexible(document.getElementById("m-can-id").value);
    frame.can = {
      can_id: canId,
      can_id_hex: "0x" + canId.toString(16).toUpperCase(),
      dlc: parseInt(document.getElementById("m-can-dlc").value) || 0,
      flags: parseIntFlexible(document.getElementById("m-can-flags").value),
      channel: parseInt(document.getElementById("m-can-channel").value) || 0,
    };
  } else if (bt === "ETHERNET") {
    frame.metadata_type = "eth";
    frame.eth = {
      src_mac: document.getElementById("m-eth-src-mac").value || "",
      dst_mac: document.getElementById("m-eth-dst-mac").value || "",
      ethertype: parseIntFlexible(document.getElementById("m-eth-ethertype").value),
      vlan_id: parseInt(document.getElementById("m-eth-vlan").value) || 0,
      src_ip: document.getElementById("m-eth-src-ip").value || "",
      dst_ip: document.getElementById("m-eth-dst-ip").value || "",
      ip_version: parseInt(document.getElementById("m-eth-ipver").value) || 0,
    };
  } else if (bt === "TCP") {
    frame.metadata_type = "tcp";
    frame.tcp = {
      src_ip: document.getElementById("m-tcp-src-ip").value || "",
      dst_ip: document.getElementById("m-tcp-dst-ip").value || "",
      ip_version: parseInt(document.getElementById("m-tcp-ipver").value) || 0,
      src_port: parseInt(document.getElementById("m-tcp-src-port").value) || 0,
      dst_port: parseInt(document.getElementById("m-tcp-dst-port").value) || 0,
      conn_id: parseInt(document.getElementById("m-tcp-conn-id").value) || 0,
    };
  } else if (bt === "PDU") {
    frame.metadata_type = "pdu";
    frame.pdu = {pdu_id: parseIntFlexible(document.getElementById("m-pdu-id").value)};
  } else {
    frame.metadata_type = null;
  }
  return frame;
}

async function saveModal() {
  const frame = collectModal();
  try {
    if (editingIndex !== null) {
      await api("PUT", "/api/frames/" + editingIndex, frame);
      toast(`Frame #${editingIndex} updated`,"success");
    } else {
      const r = await api("POST", "/api/frames/insert", {after_index: insertAfterIndex, frame});
      toast(`Frame inserted at #${r.index}`,"success");
    }
    const fr = await api("GET","/api/frames");
    frames = fr.frames;
    closeModal();
    renderTable();
  } catch(e) { toast("Save failed: " + e.message,"error"); }
}

// ── Nav links ──────────────────────────────────────────────────────────────
(function() {
  const h = window.location.hostname, p = window.location.port;
  document.querySelectorAll('.nav-link').forEach(a => {
    a.href = 'http://' + h + ':' + a.dataset.port + '/';
    if (a.dataset.port === p) a.classList.add('active');
  });
})();

// ── Init ──────────────────────────────────────────────────────────────────
(async function init() {
  await refreshFileList();
  try {
    const fr = await api("GET","/api/frames");
    frames = fr.frames;
    currentPath = fr.path;
    renderTable();
  } catch(e) {
    renderTable();
  }
})();
</script>
</body>
</html>
"""

# ── Routes ──────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(HTML)


# ── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"BoAt Trace Editor → http://localhost:{_PORT}")
    uvicorn.run(app, host="0.0.0.0", port=_PORT, log_level="warning")
