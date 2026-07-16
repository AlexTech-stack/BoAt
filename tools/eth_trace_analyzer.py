"""
BoAt Platform — Ethernet Trace Analyzer
Read .pcap captures, identify protocols (VLAN/EtherType, DoIP, SOME/IP),
reconstruct TCP sessions, and classify UDP flows as cyclic vs event-driven.
Run:  python3 tools/eth_trace_analyzer.py
Open: http://localhost:8090
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "boat-platform" / "sdk" / "python"))

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from boat.eth_trace_analyzer import EthTraceAnalyzer

_PORT = int(os.environ.get("BOAT_ETH_ANALYZER_PORT", "8090"))
_CONFIG_DIR = Path(__file__).resolve().parent.parent / "boat-platform" / "config"
_RECORDINGS_DIR = Path(__file__).resolve().parent.parent / "boat-platform" / "traces"
# EthernetPcapReader only parses the classic pcap global header
# (DLT_EN10MB) -- .pcapng is a different, block-based format it doesn't
# understand, so it's deliberately not listed as supported here.
_SUPPORTED_SUFFIXES = (".pcap",)

app = FastAPI()

# ── API routes ──────────────────────────────────────────────────────────────

@app.get("/api/pcap/list")
def api_pcap_list():
    files = []
    for d in [_RECORDINGS_DIR, _CONFIG_DIR, Path("/tmp"), Path.home(), Path.home() / "traces", Path.home() / "traces" / "pcap"]:
        try:
            for f in Path(d).glob("*.pcap"):
                files.append(str(f))
        except Exception:
            pass
    files = sorted(set(files))[:200]
    return {"files": files}

@app.post("/api/pcap/analyze")
def api_pcap_analyze(body: dict):
    """Single-pass bulk analysis: EtherType/VLAN histograms, node
    inventory, UDP flow stats (cyclic/event classification, SOME/IP
    recognition), TCP session reconstruction (client/server roles), and a
    DoIP server / SOME/IP service-ID+method-ID catalog."""
    path = body.get("path", "")
    fp = Path(path).expanduser()
    if not fp.exists():
        raise HTTPException(404, f"File not found: {fp}")
    if fp.suffix.lower() not in _SUPPORTED_SUFFIXES:
        raise HTTPException(400, f"Unsupported format: {fp.suffix}. Supported: .pcap")

    t0 = time.perf_counter()
    try:
        analyzer = EthTraceAnalyzer(str(fp))
        analysis = analyzer.analyze()
    except Exception as e:
        raise HTTPException(400, f"Analysis failed: {e}")
    elapsed = time.perf_counter() - t0

    summary = analyzer.to_summary(analysis)
    summary["file_name"] = fp.name
    summary["file_size"] = fp.stat().st_size
    summary["elapsed_s"] = round(elapsed, 2)
    return summary

# ── HTML ────────────────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>BoAt — Ethernet Trace Analyzer</title>
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
.layout { display:flex; height:calc(100vh - 78px); }
.sidebar {
  width:340px; min-width:340px; background:var(--panel); border-right:1px solid var(--border);
  display:flex; flex-direction:column; overflow:hidden;
}
.sidebar-toolbar { padding:8px; display:flex; gap:4px; border-bottom:1px solid var(--border); flex-wrap:wrap; }
.sidebar-toolbar input { flex:1; padding:4px 8px; background:var(--bg); border:1px solid var(--border); border-radius:4px; color:var(--text); font-family:var(--mono); font-size:12px; }
button.btn { padding:5px 10px; border:1px solid var(--border); border-radius:4px; background:var(--bg); color:var(--text); cursor:pointer; font-size:12px; }
button.btn:hover { background:var(--panel); }
.btn-primary { color:var(--blue) !important; border-color:var(--blue) !important; }
.btn-primary:hover { background:rgba(88,166,255,0.1) !important; }
.btn-add { color:var(--green) !important; border-color:var(--green) !important; }
.btn-add:hover { background:rgba(63,185,80,0.1) !important; }
.main { flex:1; overflow-y:auto; padding:16px; }
.pane { max-width:1200px; margin:0 auto; }
h2 { font-size:16px; font-weight:600; margin:0 0 12px; }
h3 { font-size:14px; font-weight:600; margin:20px 0 8px; color:var(--text); display:flex; align-items:center; gap:8px; }
h3 .hint { font-size:11px; color:var(--muted); font-weight:400; }
table { width:100%; border-collapse:collapse; font-size:12px; }
th { text-align:left; padding:6px 8px; border-bottom:2px solid var(--border); color:var(--muted); font-weight:600; font-size:11px; position:sticky; top:0; background:var(--bg); white-space:nowrap; }
td { padding:5px 8px; border-bottom:1px solid var(--border); font-family:var(--mono); font-size:11px; }
tr:hover td { background:rgba(88,166,255,0.03); }
.stat-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:8px; margin-bottom:16px; }
.stat-card { background:var(--bg); border:1px solid var(--border); border-radius:6px; padding:12px; text-align:center; }
.stat-card .value { font-size:22px; font-weight:700; color:var(--blue); font-family:var(--mono); }
.stat-card .label { font-size:11px; color:var(--muted); margin-top:2px; }
.empty-state { text-align:center; padding:60px 20px; color:var(--muted); }
.empty-state h2 { font-size:20px; margin-bottom:8px; }
.empty-state p { font-size:14px; margin-bottom:16px; }
.badge { display:inline-block; padding:1px 5px; border-radius:3px; font-size:10px; font-weight:600; }
.badge-cyclic { background:rgba(63,185,80,0.15); color:var(--green); }
.badge-bursty { background:rgba(210,153,34,0.15); color:var(--yellow); }
.badge-doip { background:rgba(210,168,255,0.15); color:var(--purple); }
.badge-someip { background:rgba(88,166,255,0.15); color:var(--blue); }
.badge-mcast { background:rgba(255,166,87,0.15); color:var(--orange); }
.section { background:var(--bg); border:1px solid var(--border); border-radius:6px; padding:12px; margin-bottom:16px; overflow-x:auto; }
#toast-container {
  position:fixed; bottom:20px; right:20px; z-index:9999;
  display:flex; flex-direction:column-reverse; gap:8px; align-items:flex-end;
}
.toast { padding:10px 20px; border-radius:6px; font-size:13px; max-width:420px; animation:fadeIn 0.2s; }
.toast.info { background:var(--blue); color:#fff; }
.toast.error { background:var(--red); color:#fff; }
.toast.success { background:var(--green); color:#fff; }
@keyframes fadeIn { from{opacity:0;transform:translateY(10px)} to{opacity:1;transform:translateY(0)} }
.spinner { border:2px solid var(--border); border-top-color:var(--blue); border-radius:50%; animation:spin 0.8s linear infinite; }
@keyframes spin { to{transform:rotate(360deg)} }
::-webkit-scrollbar { width:4px; height:4px; }
::-webkit-scrollbar-track { background:transparent; }
::-webkit-scrollbar-thumb { background:var(--border); border-radius:2px; }
</style>
</head>
<body>

<header>
  <span class="logo">⛵ BoAt</span>
  <span class="subtitle">Ethernet Trace Analyzer</span>
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
  <a class="nav-link" data-port="8089">Trace Editor</a>
  <a class="nav-link" data-port="8090" style="color:var(--blue)">Eth Analyzer</a>
</nav>

<div class="layout">
  <div class="sidebar">
    <div class="sidebar-toolbar">
      <input id="file-path" type="text" placeholder="/path/to/capture.pcap"/>
      <button class="btn btn-primary" onclick="browseFile()">Browse</button>
    </div>
    <div style="padding:8px;border-bottom:1px solid var(--border)">
      <button class="btn btn-add" id="analyze-btn" onclick="runAnalyze()" style="width:100%">Analyze</button>
      <div id="progress" style="display:none;align-items:center;gap:6px;font-size:11px;color:var(--muted);margin-top:8px">
        <div class="spinner" style="width:12px;height:12px;border-width:2px;display:inline-block"></div>
        <span>Reading capture...</span>
      </div>
    </div>
    <div id="file-list" style="flex:1;overflow-y:auto;padding:8px;font-size:12px"></div>
  </div>

  <div class="main" id="main-content">
    <div class="empty-state" id="empty-state">
      <h2>No capture analyzed</h2>
      <p>Enter a .pcap path and click Analyze.</p>
    </div>
    <div class="pane" id="results" style="display:none"></div>
  </div>
</div>

<div id="toast-container"></div>

<script>
const params = new URLSearchParams(location.search);
document.querySelectorAll("#panel-nav .nav-link").forEach(a => {
  const port = a.dataset.port;
  a.href = `${location.protocol}//${location.hostname}:${port}/`;
});

function toast(msg, type="info") {
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = msg;
  document.getElementById("toast-container").appendChild(el);
  const duration = Math.min(8000, Math.max(3000, msg.length * 60));
  setTimeout(() => el.remove(), duration);
}

async function api(method, url, body) {
  const opts = { method, headers: {"Content-Type": "application/json"} };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const r = await fetch(url, opts);
  if (!r.ok) {
    let msg = `HTTP ${r.status}`;
    try { const j = await r.json(); msg = j.detail || msg; } catch(e) {}
    throw new Error(msg);
  }
  return r.json();
}

async function loadFileList() {
  try {
    const r = await api("GET", "/api/pcap/list");
    const div = document.getElementById("file-list");
    if (!r.files.length) { div.innerHTML = '<div style="color:var(--muted)">No .pcap files found</div>'; return; }
    div.innerHTML = r.files.map(f => `<div style="padding:4px 0;cursor:pointer;color:var(--muted);word-break:break-all" onclick="document.getElementById('file-path').value='${f.replace(/\\/g,"\\\\")}'; runAnalyze()">${f}</div>`).join("");
  } catch(e) {}
}
loadFileList();

function browseFile() {
  toast("Enter the full path in the text field, then click Analyze", "info");
}

let lastResult = null;

async function runAnalyze() {
  const path = document.getElementById("file-path").value.trim();
  if (!path) { toast("Enter a .pcap path first", "error"); return; }
  document.getElementById("analyze-btn").disabled = true;
  document.getElementById("progress").style.display = "flex";
  try {
    lastResult = await api("POST", "/api/pcap/analyze", {path});
    renderResults();
    document.getElementById("empty-state").style.display = "none";
    document.getElementById("results").style.display = "block";
    toast(`Analyzed in ${lastResult.elapsed_s}s: ${lastResult.file_name} — ${lastResult.total_frames.toLocaleString()} frames, ${lastResult.duration_s}s span`, "success");
    (lastResult.warnings || []).forEach(w => toast(w, "info"));
  } catch(e) {
    toast("Analysis failed: " + e.message, "error");
  } finally {
    document.getElementById("analyze-btn").disabled = false;
    document.getElementById("progress").style.display = "none";
  }
}

function fmtVlans(vlans) {
  return vlans && vlans.length ? vlans.map(v => `<span class="badge" style="background:rgba(255,166,87,0.12);color:var(--orange)">${v}</span>`).join(" ") : "—";
}

function renderResults() {
  const r = lastResult;
  const el = document.getElementById("results");

  const nodeCount = r.nodes.length;
  const udpCount = r.udp_flows.length;
  const tcpCount = r.tcp_sessions.length;
  const doipCount = r.doip_servers.length;
  const someipCount = r.someip_catalog.length;

  let html = `
    <div class="stat-grid">
      <div class="stat-card"><div class="value">${r.total_frames.toLocaleString()}</div><div class="label">Total Frames</div></div>
      <div class="stat-card"><div class="value">${r.duration_s}s</div><div class="label">Capture Span</div></div>
      <div class="stat-card"><div class="value">${nodeCount}</div><div class="label">IP Nodes</div></div>
      <div class="stat-card"><div class="value">${udpCount}</div><div class="label">UDP Flows</div></div>
      <div class="stat-card"><div class="value">${tcpCount}</div><div class="label">TCP Sessions</div></div>
      <div class="stat-card"><div class="value">${doipCount}</div><div class="label">DoIP Servers</div></div>
      <div class="stat-card"><div class="value">${someipCount}</div><div class="label">SOME/IP Service+Method IDs</div></div>
      <div class="stat-card"><div class="value">${(r.file_size/1024/1024).toFixed(1)} MB</div><div class="label">File Size</div></div>
    </div>
  `;

  html += `<div class="section"><h3>EtherType / VLAN breakdown</h3><div style="display:flex;gap:24px;flex-wrap:wrap">
    <table style="width:auto;min-width:260px"><thead><tr><th>EtherType</th><th>Name</th><th>Frames</th></tr></thead><tbody>
      ${r.ethertypes.map(e => `<tr><td>${e.ethertype}</td><td>${e.name}</td><td>${e.count.toLocaleString()}</td></tr>`).join("")}
    </tbody></table>
    <table style="width:auto;min-width:200px"><thead><tr><th>VLAN ID</th><th>Frames</th></tr></thead><tbody>
      ${r.vlans.length ? r.vlans.map(v => `<tr><td>${v.vlan_id}</td><td>${v.count.toLocaleString()}</td></tr>`).join("") : '<tr><td colspan="2" style="color:var(--muted)">no VLAN tags</td></tr>'}
    </tbody></table>
    <table style="width:auto;min-width:200px"><thead><tr><th>IP Protocol</th><th>Frames</th></tr></thead><tbody>
      ${r.ip_protocols.map(p => `<tr><td>${p.proto}</td><td>${p.count.toLocaleString()}</td></tr>`).join("")}
    </tbody></table>
  </div></div>`;

  html += `<div class="section"><h3>DoIP servers <span class="hint">confirmed via SYN-ACK on port 13400 -- an ECU inventory, effectively for free</span></h3>
    ${r.doip_servers.length ? `<table><thead><tr><th>IP</th></tr></thead><tbody>${r.doip_servers.map(ip => `<tr><td>${ip}</td></tr>`).join("")}</tbody></table>` : '<div style="color:var(--muted)">none found</div>'}
  </div>`;

  html += `<div class="section"><h3>Nodes <span class="hint">by IP address, sorted by total traffic</span></h3>
    <table><thead><tr><th></th><th>IP</th><th>Sent</th><th>Received</th><th>VLANs seen</th><th></th></tr></thead><tbody>
    ${r.nodes.slice(0, 60).map(n => `<tr>
      <td></td>
      <td>${n.ip}</td>
      <td>${n.frames_sent.toLocaleString()}</td>
      <td>${n.frames_received.toLocaleString()}</td>
      <td>${fmtVlans(n.vlan_ids)}</td>
      <td>${n.is_multicast ? '<span class="badge badge-mcast">multicast</span>' : ''}</td>
    </tr>`).join("")}
    </tbody></table>
    ${r.nodes.length > 60 ? `<div style="color:var(--muted);padding-top:6px">... and ${r.nodes.length-60} more</div>` : ''}
  </div>`;

  html += `<div class="section"><h3>UDP flows <span class="hint">Cyclic = consistent inter-frame timing (low jitter); Bursty = irregular/multiplexed</span></h3>
    <table><thead><tr><th>Src</th><th>Dst</th><th>Frames</th><th>Bytes</th><th>Cycle</th><th>Type</th><th>Tags</th></tr></thead><tbody>
    ${r.udp_flows.slice(0, 80).map(f => `<tr>
      <td>${f.src_ip}:${f.src_port}</td>
      <td>${f.dst_ip}:${f.dst_port}</td>
      <td>${f.frame_count.toLocaleString()}</td>
      <td>${(f.byte_count/1024).toFixed(1)} KB</td>
      <td>${f.cycle_time_ms ? f.cycle_time_ms.toFixed(3) + " ms" : "—"}</td>
      <td><span class="badge ${f.send_type === 'Cyclic' ? 'badge-cyclic' : 'badge-bursty'}">${f.send_type}</span></td>
      <td>
        ${f.is_multicast_dst ? '<span class="badge badge-mcast">multicast</span>' : ''}
        ${f.is_doip_port ? '<span class="badge badge-doip">DoIP</span>' : ''}
        ${f.is_someip_sd ? '<span class="badge badge-someip">SOME/IP-SD</span>' : ''}
        ${f.is_someip_like ? '<span class="badge badge-someip">SOME/IP</span>' : ''}
      </td>
    </tr>`).join("")}
    </tbody></table>
    ${r.udp_flows.length > 80 ? `<div style="color:var(--muted);padding-top:6px">... and ${r.udp_flows.length-80} more</div>` : ''}
  </div>`;

  html += `<div class="section"><h3>TCP sessions <span class="hint">client/server roles from observed SYN / SYN-ACK; "unknown" means the handshake wasn't captured</span></h3>
    <table><thead><tr><th>Client</th><th>Server</th><th>Role</th><th>Frames</th><th>Bytes c&rarr;s</th><th>Bytes s&rarr;c</th><th>Tags</th></tr></thead><tbody>
    ${r.tcp_sessions.slice(0, 80).map(s => `<tr>
      <td>${s.client || s.endpoint_a}</td>
      <td>${s.server || s.endpoint_b}</td>
      <td style="color:${s.role_confidence === 'confirmed' ? 'var(--green)' : 'var(--muted)'}">${s.role_confidence}</td>
      <td>${s.total_frames.toLocaleString()}</td>
      <td>${(s.bytes_a_to_b/1024).toFixed(1)} KB</td>
      <td>${(s.bytes_b_to_a/1024).toFixed(1)} KB</td>
      <td>${s.is_doip ? '<span class="badge badge-doip">DoIP</span>' : ''}</td>
    </tr>`).join("")}
    </tbody></table>
    ${r.tcp_sessions.length > 80 ? `<div style="color:var(--muted);padding-top:6px">... and ${r.tcp_sessions.length-80} more</div>` : ''}
  </div>`;

  html += `<div class="section"><h3>SOME/IP service catalog <span class="hint">Service+Method ID pairs recognized by header shape, not semantic meaning</span></h3>
    ${r.someip_catalog.length ? `<table><thead><tr><th>Service ID</th><th>Method ID</th><th>Frames sampled matching</th></tr></thead><tbody>
      ${r.someip_catalog.slice(0, 60).map(c => `<tr><td>${c.service_id}</td><td>${c.method_id}</td><td>${c.count}</td></tr>`).join("")}
    </tbody></table>` : '<div style="color:var(--muted)">none found</div>'}
  </div>`;

  el.innerHTML = html;
}

if (params.get("path")) {
  document.getElementById("file-path").value = params.get("path");
  runAnalyze();
}
</script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def index(path: Optional[str] = Query(None)):
    return HTML

if __name__ == "__main__":
    print(f"BoAt Ethernet Trace Analyzer starting on http://0.0.0.0:{_PORT}")
    uvicorn.run(app, host="0.0.0.0", port=_PORT, log_level="warning")
