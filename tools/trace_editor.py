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
import struct
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

# ── Ethernet L3/L4 packet construction (Payload = full IP packet) ─────────────
#
# For ETHERNET frames, Frame.payload is the *entire* L3 packet starting at
# the IP header (matching what TraceReplayer.convert_to_binary() produces
# from imported pcaps) -- EthMetadata only carries L2 addressing plus a
# convenience copy of the IP addresses, not ports or protocol. These helpers
# let the UI present a guided UDP/ICMP form (ports, type/code, app data)
# instead of requiring the whole packet to be hand-built as raw hex, using
# the same header layout and checksum algorithm as the pcap-import path
# (TraceReplayer._reconstruct_ip4_packet/_reconstruct_ip6_packet).

def _ip_checksum(data: bytes) -> int:
    s = 0
    for i in range(0, len(data), 2):
        word = (data[i] << 8) | (data[i + 1] if i + 1 < len(data) else 0)
        s += word
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return (~s) & 0xFFFF

def _build_ipv4_header(src_ip: bytes, dst_ip: bytes, protocol: int, payload_len: int) -> bytes:
    total_len = 20 + payload_len
    header = struct.pack("!BBHHHBBH4s4s", 0x45, 0, total_len, 0, 0, 64, protocol, 0, src_ip, dst_ip)
    csum = _ip_checksum(header)
    return header[:10] + struct.pack("!H", csum) + header[12:]

def _build_ipv6_header(src_ip: bytes, dst_ip: bytes, next_header: int, payload_len: int) -> bytes:
    if len(src_ip) != 16 or len(dst_ip) != 16:
        raise ValueError("IPv6 addresses must be exactly 16 bytes")
    return struct.pack("!IHBB", 0x60000000, payload_len, next_header, 64) + src_ip + dst_ip

def _build_udp_packet(ip_version: int, src_ip: bytes, dst_ip: bytes,
                       src_port: int, dst_port: int, data: bytes) -> bytes:
    udp_len = 8 + len(data)
    udp_header = struct.pack("!HHHH", src_port, dst_port, udp_len, 0) + data
    if ip_version == 6:
        pseudo = src_ip + dst_ip + struct.pack("!I", udp_len) + b"\x00\x00\x00" + struct.pack("!B", 17)
        ip_header = _build_ipv6_header(src_ip, dst_ip, 17, udp_len)
    else:
        pseudo = src_ip + dst_ip + b"\x00" + struct.pack("!BH", 17, udp_len)
        ip_header = _build_ipv4_header(src_ip, dst_ip, 17, udp_len)
    csum = _ip_checksum(pseudo + udp_header)
    if csum == 0:
        csum = 0xFFFF  # RFC 768: an all-zero computed checksum is transmitted as all-ones
    udp_segment = udp_header[:6] + struct.pack("!H", csum) + udp_header[8:]
    return ip_header + udp_segment

def _build_icmp_packet(ip_version: int, src_ip: bytes, dst_ip: bytes,
                        icmp_type: int, icmp_code: int, identifier: int,
                        sequence: int, data: bytes) -> bytes:
    next_header = 58 if ip_version == 6 else 1  # ICMPv6 vs ICMPv4
    icmp_header = struct.pack("!BBHHH", icmp_type, icmp_code, 0, identifier, sequence) + data
    if ip_version == 6:
        # ICMPv6 checksum includes the IPv6 pseudo-header (RFC 4443); ICMPv4 does not.
        pseudo = src_ip + dst_ip + struct.pack("!I", len(icmp_header)) + b"\x00\x00\x00" + struct.pack("!B", next_header)
        csum = _ip_checksum(pseudo + icmp_header)
        ip_header = _build_ipv6_header(src_ip, dst_ip, next_header, len(icmp_header))
    else:
        csum = _ip_checksum(icmp_header)
        ip_header = _build_ipv4_header(src_ip, dst_ip, next_header, len(icmp_header))
    icmp_segment = icmp_header[:2] + struct.pack("!H", csum) + icmp_header[4:]
    return ip_header + icmp_segment

def _parse_ip_l4(payload: bytes, ip_version: int) -> Optional[dict[str, Any]]:
    """Best-effort parse of an existing raw payload as IPv4/IPv6 + UDP/ICMP.

    Returns None if it doesn't look like one of those (e.g. TCP, or not even
    a valid IP packet) -- the caller falls back to raw hex editing.
    """
    try:
        if ip_version == 6:
            if len(payload) < 40:
                return None
            next_header = payload[6]
            l4 = payload[40:]
            src_ip, dst_ip = _ip_to_str(payload[8:24]), _ip_to_str(payload[24:40])
            udp_proto, icmp_proto = 17, 58
        else:
            if len(payload) < 20:
                return None
            ihl = (payload[0] & 0x0F) * 4
            if ihl < 20 or ihl > len(payload):
                return None
            next_header = payload[9]
            l4 = payload[ihl:]
            src_ip, dst_ip = _ip_to_str(payload[12:16]), _ip_to_str(payload[16:20])
            udp_proto, icmp_proto = 17, 1

        if next_header == udp_proto and len(l4) >= 8:
            src_port, dst_port = struct.unpack("!HH", l4[0:4])
            return {
                "protocol": "udp", "src_ip": src_ip, "dst_ip": dst_ip,
                "src_port": src_port, "dst_port": dst_port,
                "data": l4[8:].hex().upper(),
            }
        if next_header == icmp_proto and len(l4) >= 8:
            icmp_type, icmp_code, _csum, identifier, sequence = struct.unpack("!BBHHH", l4[0:8])
            return {
                "protocol": "icmp", "src_ip": src_ip, "dst_ip": dst_ip,
                "icmp_type": icmp_type, "icmp_code": icmp_code,
                "identifier": identifier, "sequence": sequence,
                "data": l4[8:].hex().upper(),
            }
    except Exception:
        pass
    return None

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
    # /tmp is where FlatFileTraceStore actually writes imported traces
    # (server-side path is hardcoded as /tmp/<trace_id>.trace in
    # replay_service_impl.cpp), so that's the most useful default place to
    # look -- alongside traces/ (this tool's own save location) and $HOME.
    for d in [Path("/tmp"), _EXPORT_DIR, Path.home()]:
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

@app.post("/api/eth/build")
def api_eth_build(body: dict):
    """Build a full IP+UDP or IP+ICMP packet from structured fields.

    Returns the resulting bytes as hex, meant to be stored directly as the
    frame's payload (see the module docstring above _ip_checksum for why
    Ethernet frames carry the whole L3 packet as payload).
    """
    ip_version = int(body.get("ip_version") or 4)
    try:
        src_ip = _ip_from_str(body.get("src_ip") or "")
        dst_ip = _ip_from_str(body.get("dst_ip") or "")
    except ValueError as e:
        raise HTTPException(400, f"Invalid IP address: {e}")
    if not src_ip or not dst_ip:
        raise HTTPException(400, "src_ip and dst_ip are required")
    expected_len = 16 if ip_version == 6 else 4
    if len(src_ip) != expected_len or len(dst_ip) != expected_len:
        raise HTTPException(400, f"src_ip/dst_ip must be valid IPv{ip_version} addresses")

    try:
        data = bytes.fromhex((body.get("data") or "").replace(" ", ""))
    except ValueError:
        raise HTTPException(400, "Invalid hex in 'data'")

    protocol = body.get("protocol")
    try:
        if protocol == "udp":
            packet = _build_udp_packet(ip_version, src_ip, dst_ip,
                                        int(body.get("src_port") or 0), int(body.get("dst_port") or 0), data)
        elif protocol == "icmp":
            packet = _build_icmp_packet(ip_version, src_ip, dst_ip,
                                         int(body.get("icmp_type") or 0), int(body.get("icmp_code") or 0),
                                         int(body.get("identifier") or 0), int(body.get("sequence") or 0), data)
        else:
            raise HTTPException(400, f"Unknown protocol: {protocol!r} (expected 'udp' or 'icmp')")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Failed to build packet: {e}")

    return {"payload": packet.hex().upper()}

@app.post("/api/eth/parse")
def api_eth_parse(body: dict):
    """Best-effort parse an existing Ethernet payload as IP+UDP/ICMP, for
    pre-filling the guided form when opening an already-existing frame."""
    ip_version = int(body.get("ip_version") or 4)
    try:
        payload = bytes.fromhex((body.get("payload") or "").replace(" ", ""))
    except ValueError:
        raise HTTPException(400, "Invalid hex payload")
    return {"parsed": _parse_ip_l4(payload, ip_version)}

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
  <label>Gateway <input id="gateway-addr" placeholder="localhost:50051" style="width:150px" onchange="saveGatewayCookie()"/></label>
  <button class="btn-add" onclick="pushToGateway()">Push to Gateway</button>
  <a href="/howto" target="_blank" style="color:var(--muted);font-size:12px;text-decoration:none;padding:5px 10px;border:1px solid var(--border);border-radius:4px">Help</a>
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
      <h3 style="font-size:12px;color:var(--muted);margin-top:12px">Ethernet metadata (L2)</h3>
      <div class="field-row">
        <div class="field"><label>Src MAC</label><input id="m-eth-src-mac" placeholder="aa:bb:cc:dd:ee:ff"/></div>
        <div class="field"><label>Dst MAC</label><input id="m-eth-dst-mac" placeholder="aa:bb:cc:dd:ee:ff"/></div>
      </div>
      <div class="field-row">
        <div class="field"><label>EtherType</label><input id="m-eth-ethertype" placeholder="0x0800"/></div>
        <div class="field"><label>VLAN ID</label><input id="m-eth-vlan" type="number" min="0"/></div>
      </div>
      <div class="field-hint">
        Ethernet frames have no packed flags field. VLAN ID <code>0</code> means untagged;
        any other value tags the frame with that VLAN (metadata only, not spliced into the packet bytes below).
        <strong>EtherType is set independently here</strong> and is never auto-filled or overwritten by the IP
        Version / L4 Protocol choices below -- set it yourself to match: <code>0x0800</code> for IPv4,
        <code>0x86DD</code> for IPv6. A mismatch (e.g. IPv6 payload with EtherType left at IPv4) will build a
        packet a real receiver can't parse correctly.
      </div>

      <h3 style="font-size:12px;color:var(--muted);margin-top:12px">IP / transport layer</h3>
      <div class="field-row">
        <div class="field"><label>IP Version</label>
          <select id="m-eth-ipver" onchange="onEthFieldChange()">
            <option value="4">IPv4</option>
            <option value="6">IPv6</option>
          </select>
        </div>
        <div class="field"><label>L4 Protocol</label>
          <select id="m-eth-protocol" onchange="onEthProtocolChange()">
            <option value="">None (edit raw packet bytes below)</option>
            <option value="udp">UDP</option>
            <option value="icmp">ICMP</option>
          </select>
        </div>
      </div>
      <div class="field-row">
        <div class="field"><label>Src IP</label><input id="m-eth-src-ip" oninput="onEthFieldChange()"/></div>
        <div class="field"><label>Dst IP</label><input id="m-eth-dst-ip" oninput="onEthFieldChange()"/></div>
      </div>
      <div class="field-hint">
        <code>Frame.payload</code> for Ethernet is the whole IP packet, not just an application payload -- there's
        no separate header/data split at the protocol level. Pick UDP or ICMP here to fill in the header fields
        below and have the full packet (with correct length/checksum) built for you; pick "None" to edit the raw
        packet bytes directly (needed for TCP -- this codebase sends TCP through its own connection-oriented
        plugin, not as raw frames, so a guided TCP form wouldn't be replayable here anyway).
      </div>

      <div id="m-eth-udp-fields" style="display:none">
        <div class="field-row">
          <div class="field"><label>Src Port</label><input id="m-eth-src-port" type="number" min="0" max="65535" oninput="onEthFieldChange()"/></div>
          <div class="field"><label>Dst Port</label><input id="m-eth-dst-port" type="number" min="0" max="65535" oninput="onEthFieldChange()"/></div>
        </div>
      </div>

      <div id="m-eth-icmp-fields" style="display:none">
        <div class="field-row">
          <div class="field"><label>Type</label><input id="m-eth-icmp-type" type="number" min="0" max="255" oninput="onEthFieldChange()"/></div>
          <div class="field"><label>Code</label><input id="m-eth-icmp-code" type="number" min="0" max="255" oninput="onEthFieldChange()"/></div>
        </div>
        <div class="field-row">
          <div class="field"><label>Identifier</label><input id="m-eth-icmp-id" type="number" min="0" max="65535" oninput="onEthFieldChange()"/></div>
          <div class="field"><label>Sequence</label><input id="m-eth-icmp-seq" type="number" min="0" max="65535" oninput="onEthFieldChange()"/></div>
        </div>
        <div class="field-hint">
          Type/code: IPv4 echo request = <code>8</code>/<code>0</code>, echo reply = <code>0</code>/<code>0</code>.
          IPv6 echo request = <code>128</code>/<code>0</code>, echo reply = <code>129</code>/<code>0</code>.
        </div>
      </div>

      <div id="m-eth-appdata-field" class="field" style="display:none">
        <label>Application Data (hex)</label>
        <textarea id="m-eth-appdata" oninput="onEthFieldChange()" placeholder="Bytes after the UDP/ICMP header, if any"></textarea>
      </div>

      <div id="m-eth-preview-wrap" style="display:none">
        <div class="field-hint">
          Payload (hex) above is now <strong>built automatically</strong> from these fields --
          IP header + <span id="m-eth-preview-proto"></span> header + Application Data, with length and
          checksum computed for you. It's read-only while a protocol is selected; switch L4 Protocol to
          "None" to take over editing it directly.
        </div>
        <div id="m-eth-build-error" class="field-hint" style="display:none;border-color:var(--red);color:var(--red)"></div>
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

// Gateway address is remembered across sessions/reloads via a cookie, since
// it's normally constant for a given setup -- no reason to retype it on
// every push. Trace ID stays a prompt since it's per-push, not per-session.
const GATEWAY_COOKIE = "boat_trace_editor_gateway";

function setCookie(name, value, days) {
  const maxAge = (days || 365) * 24 * 60 * 60;
  document.cookie = `${name}=${encodeURIComponent(value)}; max-age=${maxAge}; path=/; SameSite=Lax`;
}

function getCookie(name) {
  const match = document.cookie.match(new RegExp("(?:^|; )" + name + "=([^;]*)"));
  return match ? decodeURIComponent(match[1]) : null;
}

function saveGatewayCookie() {
  const val = document.getElementById("gateway-addr").value.trim();
  if (val) setCookie(GATEWAY_COOKIE, val);
}

async function pushToGateway() {
  const defaultId = currentPath ? currentPath.split(/[\\/]/).pop().replace(/\.[^.]+$/, "") : "edited";
  const traceId = prompt("Trace ID to import as on the gateway:", defaultId);
  if (!traceId) return;
  const gateway = document.getElementById("gateway-addr").value.trim() || "localhost:50051";
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
async function openEditModal(index) {
  const f = frames.find(x => x.index === index);
  if (!f) return;
  editingIndex = index;
  insertAfterIndex = null;
  document.getElementById("modal-title").textContent = `Edit Frame #${index}`;
  document.getElementById("modal-overlay").style.display = "flex";
  await fillModal(f);
}

async function openInsertModal(afterIndex) {
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
  document.getElementById("modal-overlay").style.display = "flex";
  await fillModal(base || {bus_type:"CAN", iface:"", timestamp_ns:0, payload:"", metadata_type:"can",
    can:{can_id:0, dlc:0, flags:0, channel:1}});
}

function closeModal() {
  document.getElementById("modal-overlay").style.display = "none";
  editingIndex = null; insertAfterIndex = null;
}

async function fillModal(f) {
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
  document.getElementById("m-eth-ipver").value = String(e.ip_version || 4);
  await fillEthProtocolFields(f.bus_type, e.ip_version || 4, f.payload || "");

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

// ── Ethernet guided UDP/ICMP packet builder ─────────────────────────────────
// Frame.payload for ETHERNET frames is the whole IP packet (see the
// field-hint text above m-eth-udp-fields). Selecting a protocol here builds
// that packet server-side (/api/eth/build, sharing the same header/checksum
// logic as pcap import) and writes it into the shared #m-payload field,
// which is otherwise the raw-hex editor used by every other bus type.

function onEthProtocolChange() {
  const proto = document.getElementById("m-eth-protocol").value;
  document.getElementById("m-eth-udp-fields").style.display = proto === "udp" ? "block" : "none";
  document.getElementById("m-eth-icmp-fields").style.display = proto === "icmp" ? "block" : "none";
  document.getElementById("m-eth-appdata-field").style.display = proto ? "block" : "none";
  document.getElementById("m-eth-preview-wrap").style.display = proto ? "block" : "none";
  document.getElementById("m-eth-preview-proto").textContent = proto.toUpperCase();
  const payloadEl = document.getElementById("m-payload");
  payloadEl.readOnly = !!proto;
  payloadEl.style.opacity = proto ? "0.65" : "1";
  if (proto) rebuildEthPacket();
}

function onEthFieldChange() {
  if (document.getElementById("m-eth-protocol").value) rebuildEthPacket();
}

async function rebuildEthPacket() {
  const proto = document.getElementById("m-eth-protocol").value;
  if (!proto) return;
  const body = {
    ip_version: parseInt(document.getElementById("m-eth-ipver").value) || 4,
    protocol: proto,
    src_ip: document.getElementById("m-eth-src-ip").value || "",
    dst_ip: document.getElementById("m-eth-dst-ip").value || "",
    data: (document.getElementById("m-eth-appdata").value || "").replace(/\s+/g,""),
  };
  if (proto === "udp") {
    body.src_port = parseInt(document.getElementById("m-eth-src-port").value) || 0;
    body.dst_port = parseInt(document.getElementById("m-eth-dst-port").value) || 0;
  } else if (proto === "icmp") {
    body.icmp_type = parseInt(document.getElementById("m-eth-icmp-type").value) || 0;
    body.icmp_code = parseInt(document.getElementById("m-eth-icmp-code").value) || 0;
    body.identifier = parseInt(document.getElementById("m-eth-icmp-id").value) || 0;
    body.sequence = parseInt(document.getElementById("m-eth-icmp-seq").value) || 0;
  }
  const errEl = document.getElementById("m-eth-build-error");
  try {
    const r = await api("POST", "/api/eth/build", body);
    document.getElementById("m-payload").value = r.payload;
    errEl.style.display = "none";
  } catch(e) {
    errEl.style.display = "block";
    errEl.textContent = "Could not build packet: " + e.message;
  }
}

// Auto-detects an existing ETHERNET frame's payload as UDP/ICMP (via
// /api/eth/parse) and pre-fills the guided fields; leaves L4 Protocol on
// "None" (raw) for anything else (TCP, or payloads that just aren't a
// recognizable IP packet), so real bytes are never hidden or reinterpreted
// incorrectly.
async function fillEthProtocolFields(busType, ipVersion, payloadHex) {
  document.getElementById("m-eth-protocol").value = "";
  onEthProtocolChange();
  if (busType !== "ETHERNET" || !payloadHex) return;
  try {
    const r = await api("POST", "/api/eth/parse", {ip_version: ipVersion, payload: payloadHex});
    const p = r.parsed;
    if (!p) return;
    if (p.protocol === "udp") {
      document.getElementById("m-eth-src-port").value = p.src_port;
      document.getElementById("m-eth-dst-port").value = p.dst_port;
    } else if (p.protocol === "icmp") {
      document.getElementById("m-eth-icmp-type").value = p.icmp_type;
      document.getElementById("m-eth-icmp-code").value = p.icmp_code;
      document.getElementById("m-eth-icmp-id").value = p.identifier;
      document.getElementById("m-eth-icmp-seq").value = p.sequence;
    } else {
      return;
    }
    document.getElementById("m-eth-appdata").value = p.data;
    document.getElementById("m-eth-protocol").value = p.protocol;
    onEthProtocolChange();
  } catch(e) { /* leave in raw mode on any parse failure */ }
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
  document.getElementById("gateway-addr").value = getCookie(GATEWAY_COOKIE) || "localhost:50051";
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

@app.get("/howto", response_class=HTMLResponse)
def howto():
    howto_path = Path(__file__).resolve().parent / "trace_editor_howto.html"
    return HTMLResponse(howto_path.read_text(encoding="utf-8"))


# ── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"BoAt Trace Editor → http://localhost:{_PORT}")
    uvicorn.run(app, host="0.0.0.0", port=_PORT, log_level="warning")
