"""
BoAt Admin — PySide6 desktop client for one or more launcher agents.

Run:
    pip install -r admin_gui/requirements.txt
    python3 admin_gui/main.py

Talks to any number of `ui/launcher_agent.py` instances over plain HTTP (add
each as a host below) -- no SSH, this app never touches a remote machine
directly, it only calls each host's own agent API. See
backlog/launcher_agent_backlog.md and AGENTS.md's "Launcher Agent" section.

Two tabs: Gateways (boat_gateway process lifecycle) and Nodes (script
processes under boat-platform/nodes/ -- see AGENTS.md). Both are per-host
agent-managed registries with the same create/edit/start/stop/delete shape,
kept as separate tables/dialogs since the domains genuinely differ (a node
has no port to allocate or ifaces/plugins of its own; it has a target
gateway via BOAT_HOST and arbitrary script-specific CLI args).

v1 scope: host list + aggregated instance table + start/stop/delete/create +
a log viewer for the selected instance. No interface-creation UI yet (the
agent doesn't expose that either -- see the backlog).
"""

from __future__ import annotations

import json
import os
import shlex
import sys
from typing import Optional, Tuple
from urllib.parse import urlparse

import yaml

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from agent_client import AgentClient, AgentError
from host_store import HostStore
import session

_POLL_INTERVAL_SEC = 2.0


def _mark(btn: QPushButton, css_class: str) -> QPushButton:
    """Tags a button with a dynamic `class` property the dark stylesheet's
    `QPushButton[class="..."]` selectors key off of -- "primary" for the
    main affirmative action in a row (Start), "danger" for anything
    destructive/disruptive (Stop, Down, Delete). Must be called before the
    widget is first shown (property is read when Qt computes its style,
    not on every change), which every call site here already satisfies --
    all buttons are built inside MainWindow.__init__(), before .show()."""
    btn.setProperty("class", css_class)
    return btn


# Dark theme, approximated from a mockup the user provided (sidebar nav +
# dark navy/charcoal app, blue "primary" accent, red "danger" accent, green
# for good status) -- applied once, app-wide, via QApplication.setStyleSheet()
# in main(). Uses Qt dynamic properties (`[class="primary"]`/`[class="danger"]`,
# set via _mark() above) rather than per-widget stylesheets, so every button/
# dialog picks it up automatically without needing to touch each call site's
# styling individually.
_DARK_STYLESHEET = """
QMainWindow, QWidget {
    background-color: #1b1d27;
    color: #e7e8ee;
    font-size: 13px;
}

QWidget#Sidebar {
    background-color: #14151d;
    border-right: 1px solid #2a2c38;
}

QLabel#AppTitle {
    color: #e7e8ee;
    font-size: 16px;
    font-weight: 600;
    padding: 18px 16px 14px 16px;
}

QListWidget#NavList {
    background-color: transparent;
    border: none;
    outline: none;
    padding: 4px 8px;
}
QListWidget#NavList::item {
    color: #a9acc0;
    padding: 10px 12px;
    border-radius: 8px;
    margin: 2px 4px;
}
QListWidget#NavList::item:selected {
    background-color: #2b3a63;
    color: #ffffff;
}
QListWidget#NavList::item:hover:!selected {
    background-color: #1e2029;
}

QLabel[class="muted"] {
    color: #8a8da3;
    font-size: 11px;
}

QPushButton {
    background-color: #262837;
    color: #e7e8ee;
    border: 1px solid #383b4d;
    border-radius: 6px;
    padding: 6px 14px;
}
QPushButton:hover {
    background-color: #2e3142;
}
QPushButton:pressed {
    background-color: #20222e;
}
QPushButton:disabled {
    color: #5c5f70;
    background-color: #1e2029;
    border-color: #262837;
}
QPushButton[class="primary"] {
    background-color: #3d6fe0;
    border-color: #3d6fe0;
    color: #ffffff;
    font-weight: 600;
}
QPushButton[class="primary"]:hover {
    background-color: #4d7ff0;
}
QPushButton[class="primary"]:pressed {
    background-color: #3459b8;
}
QPushButton[class="danger"] {
    background-color: #e0524f;
    border-color: #e0524f;
    color: #ffffff;
    font-weight: 600;
}
QPushButton[class="danger"]:hover {
    background-color: #ec6663;
}
QPushButton[class="danger"]:pressed {
    background-color: #bd4340;
}

QLineEdit, QComboBox, QPlainTextEdit, QTextEdit {
    background-color: #262837;
    color: #e7e8ee;
    border: 1px solid #383b4d;
    border-radius: 6px;
    padding: 4px 6px;
    selection-background-color: #3d6fe0;
}
QLineEdit:disabled, QComboBox:disabled {
    color: #6a6d80;
    background-color: #1e2029;
}
QComboBox::drop-down {
    border: none;
    width: 20px;
}
QComboBox QAbstractItemView {
    background-color: #262837;
    color: #e7e8ee;
    selection-background-color: #3d6fe0;
    outline: none;
}

QCheckBox {
    color: #e7e8ee;
    spacing: 6px;
}

QListWidget, QTreeWidget {
    background-color: #20222d;
    color: #e7e8ee;
    border: 1px solid #2a2c3a;
    border-radius: 8px;
    selection-background-color: #2b3a63;
}
QTreeWidget::item {
    padding: 3px 2px;
}

QTableWidget {
    background-color: #20222d;
    alternate-background-color: #252732;
    gridline-color: #2a2c3a;
    border: 1px solid #2a2c3a;
    border-radius: 8px;
    color: #e7e8ee;
}
QTableWidget::item {
    padding: 3px 6px;
}
QTableWidget::item:selected {
    background-color: #2b3a63;
    color: #ffffff;
}
QHeaderView::section {
    background-color: #14151d;
    color: #a9acc0;
    padding: 6px;
    border: none;
    border-bottom: 1px solid #2a2c3a;
    font-weight: 600;
}
QTableCornerButton::section {
    background-color: #14151d;
    border: none;
}

QTabWidget::pane {
    border: 1px solid #2a2c3a;
    border-radius: 8px;
}
QTabBar::tab {
    background: #1b1d27;
    color: #a9acc0;
    padding: 8px 14px;
}
QTabBar::tab:selected {
    background: #262837;
    color: #ffffff;
}

QDialog {
    background-color: #1b1d27;
}

QGroupBox {
    border: 1px solid #2a2c3a;
    border-radius: 8px;
    margin-top: 12px;
    padding-top: 12px;
    color: #e7e8ee;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: #a9acc0;
}

QMessageBox {
    background-color: #1b1d27;
}

QScrollBar:vertical, QScrollBar:horizontal {
    background: #1b1d27;
    border: none;
}
QScrollBar::handle {
    background: #383b4d;
    border-radius: 4px;
}
QScrollBar::handle:hover {
    background: #454862;
}
QScrollBar::add-line, QScrollBar::sub-line {
    height: 0px;
    width: 0px;
}
"""


def _format_interfaces(inst: dict) -> str:
    ifaces = list(inst.get("can_ifaces") or []) + list(inst.get("eth_ifaces") or [])
    return ", ".join(ifaces) if ifaces else "—"


def _format_plugins(inst: dict) -> str:
    """One entry per node_plugins item: the .so basename, plus the iface it's
    bound to (from its config, e.g. can_tp.so?{"iface": "vcan0"}) in brackets
    when present -- that's the "linked to" association the table shows."""
    parts = []
    for p in inst.get("node_plugins") or []:
        name = os.path.basename(p.get("path", "")) or p.get("path", "?")
        iface = (p.get("config") or {}).get("iface")
        parts.append(f"{name} [{iface}]" if iface else name)
    return ", ".join(parts) if parts else "—"


def _format_command_line(inst: dict) -> str:
    """The BOAT_* env vars + boat_gateway invocation that reproduces this
    instance from a shell -- lets a user copy what the admin GUI is doing
    into a script. Mirrors the env var names/format documented in
    boat-platform/README.md and AGENTS.md."""
    parts = []
    if inst.get("can_ifaces"):
        parts.append(f"BOAT_CAN_INTERFACES={','.join(inst['can_ifaces'])}")
    if inst.get("eth_ifaces"):
        parts.append(f"BOAT_ETH_INTERFACES={','.join(inst['eth_ifaces'])}")
    parts.append(f"BOAT_GRPC_PORT={inst.get('grpc_port', 50051)}")
    node_plugins = inst.get("node_plugins") or []
    if node_plugins:
        plugin_parts = []
        for p in node_plugins:
            cfg = p.get("config") or {}
            entry = p.get("path", "")
            if cfg:
                entry += "?" + json.dumps(cfg, separators=(",", ":"))
            plugin_parts.append(entry)
        parts.append(f"BOAT_NODE_PLUGINS={','.join(plugin_parts)}")
    if inst.get("tick_ms"):
        parts.append(f"BOAT_NODE_TICK_MS={inst['tick_ms']}")
    if inst.get("tick_us"):
        parts.append(f"BOAT_NODE_TICK_US={inst['tick_us']}")
    parts.append(inst.get("gateway_bin") or "./boat_gateway")
    return " ".join(parts)


def _format_node_script(node: dict) -> str:
    return os.path.basename(node.get("script_path", "")) or "—"


def _format_node_args(node: dict) -> str:
    args = node.get("extra_args") or []
    return " ".join(args) if args else "—"


def _format_node_command_line(node: dict) -> str:
    """The BOAT_HOST=... python3 <script> <args> invocation that reproduces
    this node from a shell -- same idea as _format_command_line() above, for
    the Nodes tab. extra_args goes through shlex.join() (not a plain space
    join) so an arg containing a space round-trips correctly when pasted
    back via _parse_node_command_line()."""
    parts = []
    if node.get("target_host"):
        parts.append(f"BOAT_HOST={node['target_host']}")
    parts.append(f"python3 {node.get('script_path', '')}")
    if node.get("extra_args"):
        parts.append(shlex.join(node["extra_args"]))
    return " ".join(parts)


def _parse_node_command_line(text: str) -> dict:
    """Reverse of _format_node_command_line() -- parse a pasted
    BOAT_HOST=... python3 <script> <args> line back into New/Edit-Node-
    dialog fields. Uses shlex (not the brace-aware tokenizer above) since
    node extra_args can contain quoted, space-including values and have no
    JSON-with-braces to protect the way gateway plugin configs do."""
    try:
        tokens = shlex.split(text.strip())
    except ValueError as e:
        raise ValueError(f"couldn't parse the command line: {e}") from e
    if not tokens:
        raise ValueError("empty command line")

    target_host = ""
    i = 0
    while i < len(tokens) and "=" in tokens[i] and not tokens[i].startswith("-"):
        key, _, value = tokens[i].partition("=")
        if key == "BOAT_HOST":
            target_host = value
        i += 1
    if i >= len(tokens):
        raise ValueError("couldn't find the node script (expected after any BOAT_HOST=... prefix)")

    # Skip a leading Python interpreter token if present (python3, python,
    # python3.11, or a full/relative path to one of those).
    stem = os.path.basename(tokens[i])
    if stem.endswith(".exe"):
        stem = stem[:-4]
    if stem in ("python3", "python") or (stem.startswith("python3.") and stem[8:].isdigit()):
        i += 1
    if i >= len(tokens):
        raise ValueError("couldn't find the node script path")

    return {"target_host": target_host, "script_path": tokens[i], "extra_args": tokens[i + 1:]}


def _format_test_run_manifest(run: dict) -> str:
    return os.path.basename(run.get("manifest_path", "")) or "—"


def _format_test_run_env(run: dict) -> str:
    """The env_config_path override, or "(default)" when the run just
    uses whatever environment_config its manifest itself points at --
    distinguishing "explicitly this one" from "manifest's own choice" is
    the point, not just showing a blank cell."""
    path = run.get("env_config_path")
    return os.path.basename(path) if path else "(default)"


def _format_test_run_args(run: dict) -> str:
    args = run.get("extra_args") or []
    return " ".join(args) if args else "—"


def _format_test_run_result(run: dict) -> str:
    result = run.get("result")
    return result if result else "—"


_VERDICT_COLORS = {
    "PASS": QColor("#46b285"),
    "FAIL": QColor("#e0524f"),
    "ERROR": QColor("#e0524f"),
    "RUNNING": QColor("#e0a83d"),
    "SKIPPED": QColor("#8a8da3"),
}

_STATUS_GOOD = QColor("#46b285")
_STATUS_MUTED = QColor("#8a8da3")
_STATUS_BAD = QColor("#e0524f")


def _process_status_color(status: str) -> Optional[QColor]:
    """Color for a gateway/node/test-run process-lifecycle status cell --
    shared across all three tables since they use the same status string
    shape ("running"/"stopped"/"exited:N", see NodeInstance/GatewayInstance/
    TestRunInstance.status in launcher_agent.py). None for "stopped" (or
    anything unrecognized) -- default text color, not a badge-worthy
    state, matching how the mockup only badges the notable states."""
    if status == "running":
        return _STATUS_GOOD
    if status.startswith("exited:"):
        code = status.split(":", 1)[1]
        return _STATUS_GOOD if code == "0" else _STATUS_BAD
    return None


def _bool_color(text: str) -> Optional[QColor]:
    """Color for a rendered "Yes"/"No" cell (the Managed column)."""
    if text == "Yes":
        return _STATUS_GOOD
    if text == "No":
        return _STATUS_MUTED
    return None


def _format_can_phase(phase: dict) -> str:
    """One bittiming phase (see ui/launcher_agent.py's _parse_can_phase())
    as `<bitrate> bps, <sample point>% SP`."""
    text = f"{phase['bitrate']} bps"
    if "sample_point_pct" in phase:
        text += f", {phase['sample_point_pct']}% SP"
    return text


def _format_can_config_cell(iface: dict) -> str:
    """The Interfaces table's CAN Config column -- "virtual" for vcan
    (which has no real bitrate/FD to report, and shouldn't be presented
    as if it did -- see CanConfigDialog's own guard for the same reasoning
    on the write side), "<bitrate> bps[, SP%][ / FD <dbitrate> bps[, SP%]]"
    for a real, already-configured CAN link, "—" for anything else
    (ether/veth/loopback/other, or a CAN link that's never been
    configured at all)."""
    if iface.get("type") == "vcan":
        return "virtual"
    cfg = iface.get("can_config")
    nominal = (cfg or {}).get("nominal")
    if not nominal:
        return "—"
    text = _format_can_phase(nominal)
    if cfg.get("fd"):
        data = cfg.get("data")
        text += f" / FD {_format_can_phase(data)}" if data else " / FD"
    return text


def _format_can_config_tooltip(iface: dict) -> str:
    """Full detail (prop_seg/phase_seg1/phase_seg2/sjw for each phase) for
    the CAN Config cell's tooltip -- the compact cell text above covers
    bitrate/sample-point/FD, which is what fits without cluttering the
    table; this is for "and/or seg1 seg2 and sjw etc." on hover instead."""
    if iface.get("type") == "vcan":
        return "Virtual CAN -- no bitrate or CAN FD configuration (the kernel has nothing to set)."
    cfg = iface.get("can_config")
    nominal = (cfg or {}).get("nominal")
    if not nominal:
        return ""

    def _phase_line(label: str, phase: dict) -> str:
        parts = [f"{label}: {phase['bitrate']} bps"]
        if "sample_point_pct" in phase:
            parts.append(f"sample point {phase['sample_point_pct']}%")
        segs = [f"{k}={phase[key]}" for key, k in (
            ("prop_seg", "prop_seg"), ("phase_seg1", "seg1"),
            ("phase_seg2", "seg2"), ("sjw", "sjw"),
        ) if key in phase]
        if segs:
            parts.append(", ".join(segs))
        return " — ".join(parts)

    lines = [_phase_line("Nominal", nominal)]
    if cfg.get("fd") and cfg.get("data"):
        lines.append(_phase_line("Data (FD)", cfg["data"]))
    return "\n".join(lines)


def _format_test_report_entry(entry: dict) -> str:
    """Human-readable rendering of one test's report.json content for the
    TestReportDialog's detail pane -- steps and their assertions, plus
    which raw artifact files exist alongside it (report.html/.junit.xml/
    stdout/stderr -- not fetched here, just flagged as present, since
    actually browsing those means reaching the agent's host some other
    way, same situation as the Report directory field itself)."""
    report = entry.get("report")
    if report is None:
        return f"Folder: {entry.get('folder', '?')}\n\n{entry.get('error', 'no report.json')}"

    test = report.get("test") or {}
    execu = report.get("execution") or {}
    lines = [
        f"Folder: {entry.get('folder', '?')}",
        f"Test:   {test.get('id', '?')} — {test.get('name', '')}",
    ]
    if test.get("description"):
        lines.append(f"        {test['description']}")
    lines.append(f"Verdict: {report.get('verdict', '?')}")
    if execu.get("duration_ms") is not None:
        lines.append(f"Duration: {execu['duration_ms']}ms")
    if report.get("summary"):
        lines.append(f"Summary: {report['summary']}")
    lines.append("")

    steps = report.get("steps", [])
    for step in steps:
        lines.append(f"Step {step.get('id', '?')}: {step.get('name', '')} [{step.get('verdict', '?')}]")
        assertions = step.get("assertions", [])
        for a in assertions:
            lines.append(f"    [{a.get('result', '?')}] {a.get('expression', '')}"
                          f" — expected={a.get('expected', '')} actual={a.get('actual', '')}")
        if not assertions:
            lines.append("    (no assertions recorded)")
        lines.append("")
    if not steps:
        lines.append("(no steps recorded)")
        lines.append("")

    artifacts = [name for name, present in (
        ("report.html", entry.get("has_html")),
        ("report.junit.xml", entry.get("has_junit")),
        ("stdout.txt", entry.get("has_stdout")),
        ("stderr.txt", entry.get("has_stderr")),
    ) if present]
    if artifacts:
        lines.append(f"Also on disk in this folder (agent's host): {', '.join(artifacts)}")

    return "\n".join(lines)


_KNOWN_ENV_VARS = {
    "BOAT_CAN_INTERFACES", "BOAT_ETH_INTERFACES", "BOAT_GRPC_PORT",
    "BOAT_NODE_PLUGINS", "BOAT_NODE_TICK_MS", "BOAT_NODE_TICK_US",
}


def _tokenize_command_line(text: str) -> list:
    """Split on whitespace, except inside {...} -- a pasted plugin config
    might have spaces (e.g. {"iface": "vcan0"}) even though this app's own
    _format_command_line() emits compact JSON without them."""
    tokens = []
    depth = 0
    current: list = []
    for ch in text:
        if ch == "{":
            depth += 1
            current.append(ch)
        elif ch == "}":
            depth = max(0, depth - 1)
            current.append(ch)
        elif ch.isspace() and depth == 0:
            if current:
                tokens.append("".join(current))
                current = []
        else:
            current.append(ch)
    if current:
        tokens.append("".join(current))
    return tokens


def _parse_plugins_value(value: str) -> list:
    """path?{json},path2?{json2},path3(no config) -- reverse of
    _format_command_line()'s BOAT_NODE_PLUGINS construction. Splits on
    commas that are NOT inside a {...} span, since a plugin's own config
    can contain commas (multiple keys)."""
    parts = []
    depth = 0
    current: list = []
    for ch in value:
        if ch == "{":
            depth += 1
            current.append(ch)
        elif ch == "}":
            depth = max(0, depth - 1)
            current.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current))

    plugins = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if "?" in part:
            path, _, cfg_str = part.partition("?")
            try:
                cfg = json.loads(cfg_str)
            except json.JSONDecodeError as e:
                raise ValueError(f"invalid plugin config JSON for '{path}': {e}") from e
            plugins.append({"path": path, "config": cfg})
        else:
            plugins.append({"path": part, "config": {}})
    return plugins


def _parse_command_line(text: str) -> dict:
    """Reverse of _format_command_line() -- parse a pasted
    BOAT_CAN_INTERFACES=... BOAT_NODE_PLUGINS=... ./boat_gateway line back
    into New/Edit-Instance-dialog fields. Raises ValueError with a clear
    message on anything it can't make sense of."""
    tokens = _tokenize_command_line(text.strip())
    if not tokens:
        raise ValueError("empty command line")

    result = {
        "can_ifaces": [], "eth_ifaces": [], "node_plugins": [],
        "grpc_port": None, "tick_ms": None, "tick_us": None, "gateway_bin": None,
    }
    for tok in tokens:
        key, sep, value = tok.partition("=")
        if sep and key in _KNOWN_ENV_VARS:
            if key == "BOAT_CAN_INTERFACES":
                result["can_ifaces"] = [s for s in value.split(",") if s]
            elif key == "BOAT_ETH_INTERFACES":
                result["eth_ifaces"] = [s for s in value.split(",") if s]
            elif key == "BOAT_GRPC_PORT":
                try:
                    result["grpc_port"] = int(value)
                except ValueError as e:
                    raise ValueError(f"BOAT_GRPC_PORT is not a valid integer: '{value}'") from e
            elif key == "BOAT_NODE_PLUGINS":
                result["node_plugins"] = _parse_plugins_value(value)
            elif key == "BOAT_NODE_TICK_MS":
                result["tick_ms"] = int(value) if value.isdigit() else None
            elif key == "BOAT_NODE_TICK_US":
                result["tick_us"] = int(value) if value.isdigit() else None
            continue
        if sep:
            # An unrecognized VAR=value token (some other env var prefix) --
            # skip it rather than mistaking it for the binary path.
            continue
        # First token with no '=' is the gateway binary; anything after is
        # ignored (trailing args aren't part of this format).
        result["gateway_bin"] = tok
        break

    if result["gateway_bin"] is None:
        raise ValueError("couldn't find the boat_gateway binary path "
                          "(expected as a token with no '=', e.g. at the end)")
    return result


class PollWorker(QThread):
    """Background thread: polls every configured host's /api/instances,
    /api/nodes, /api/test-runs, and /api/interfaces (and the selected
    instance's/node's/test run's log, if any) on a fixed interval, emitting
    results back to the UI thread via signals."""

    snapshot_ready = Signal(dict)        # {host_url: {"name":..., "ok":bool, "instances":[...], "error":str|None}}
    log_ready = Signal(str, list)        # (instance_id, log_lines)
    node_snapshot_ready = Signal(dict)   # {host_url: {"name":..., "ok":bool, "nodes":[...], "error":str|None}}
    node_log_ready = Signal(str, list)   # (node_id, log_lines)
    test_run_snapshot_ready = Signal(dict)  # {host_url: {"name":..., "ok":bool, "runs":[...], "error":str|None}}
    test_run_log_ready = Signal(str, list)  # (run_id, log_lines)
    interfaces_ready = Signal(dict)      # {host_url: {"name":..., "ok":bool, "interfaces":[...], "error":str|None}}

    def __init__(self, get_hosts, get_selected, get_selected_node, get_selected_test_run,
                 interval: float = _POLL_INTERVAL_SEC, parent=None):
        super().__init__(parent)
        self._get_hosts = get_hosts
        self._get_selected = get_selected
        self._get_selected_node = get_selected_node
        self._get_selected_test_run = get_selected_test_run
        self._interval = interval
        self._running = True

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        while self._running:
            snapshot = {}
            node_snapshot = {}
            test_run_snapshot = {}
            iface_snapshot = {}
            for host in self._get_hosts():
                client = AgentClient(host["url"])
                try:
                    instances = client.list_instances()
                    snapshot[host["url"]] = {"name": host["name"], "ok": True, "instances": instances, "error": None}
                except AgentError as e:
                    snapshot[host["url"]] = {"name": host["name"], "ok": False, "instances": [], "error": str(e)}
                try:
                    nodes = client.list_nodes()
                    node_snapshot[host["url"]] = {"name": host["name"], "ok": True, "nodes": nodes, "error": None}
                except AgentError as e:
                    node_snapshot[host["url"]] = {"name": host["name"], "ok": False, "nodes": [], "error": str(e)}
                try:
                    runs = client.list_test_runs()
                    test_run_snapshot[host["url"]] = {"name": host["name"], "ok": True, "runs": runs, "error": None}
                except AgentError as e:
                    test_run_snapshot[host["url"]] = {"name": host["name"], "ok": False, "runs": [], "error": str(e)}
                try:
                    interfaces = client.list_interfaces()
                    iface_snapshot[host["url"]] = {"name": host["name"], "ok": True, "interfaces": interfaces, "error": None}
                except AgentError as e:
                    iface_snapshot[host["url"]] = {"name": host["name"], "ok": False, "interfaces": [], "error": str(e)}
            if self._running:
                self.snapshot_ready.emit(snapshot)
                self.node_snapshot_ready.emit(node_snapshot)
                self.test_run_snapshot_ready.emit(test_run_snapshot)
                self.interfaces_ready.emit(iface_snapshot)

            selected = self._get_selected()
            if selected and self._running:
                host_url, inst_id = selected
                try:
                    log = AgentClient(host_url).get_log(inst_id)
                    self.log_ready.emit(inst_id, log)
                except AgentError:
                    pass

            selected_node = self._get_selected_node()
            if selected_node and self._running:
                host_url, node_id = selected_node
                try:
                    log = AgentClient(host_url).get_node_log(node_id)
                    self.node_log_ready.emit(node_id, log)
                except AgentError:
                    pass

            selected_test_run = self._get_selected_test_run()
            if selected_test_run and self._running:
                host_url, run_id = selected_test_run
                try:
                    log = AgentClient(host_url).get_test_run_log(run_id)
                    self.test_run_log_ready.emit(run_id, log)
                except AgentError:
                    pass

            for _ in range(int(self._interval * 10)):
                if not self._running:
                    break
                self.msleep(100)


class ListPicker(QWidget):
    """An editable combo box (dropdown of known choices, but free text is
    always accepted too) plus an Add/Remove-backed list of accumulated
    string values. Used for CAN/Eth interfaces: pick from what the host
    actually has, or type one that doesn't exist yet (e.g. before creating
    it in ui/launcher.py)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        row = QHBoxLayout()
        self.combo = QComboBox()
        self.combo.setEditable(True)
        row.addWidget(self.combo, 1)
        add_btn = QPushButton("+ Add")
        add_btn.clicked.connect(self.add_current)
        row.addWidget(add_btn)
        layout.addLayout(row)

        self.list_widget = QListWidget()
        self.list_widget.setFixedHeight(60)
        layout.addWidget(self.list_widget)
        remove_btn = QPushButton("Remove selected")
        remove_btn.clicked.connect(self.remove_selected)
        layout.addWidget(remove_btn)

    def set_choices(self, choices: list) -> None:
        current = self.combo.currentText()
        self.combo.clear()
        self.combo.addItems(choices)
        self.combo.setCurrentText(current)

    def add_current(self) -> None:
        text = self.combo.currentText().strip()
        if not text:
            return
        if text in self.values():
            return
        self.add_value(text)
        self.combo.setCurrentText("")

    def add_value(self, text: str) -> None:
        """Programmatic add, bypassing the combo -- used to pre-fill the
        Edit dialog from an existing instance's current interfaces."""
        self.list_widget.addItem(text)

    def remove_selected(self) -> None:
        for item in self.list_widget.selectedItems():
            self.list_widget.takeItem(self.list_widget.row(item))

    def values(self) -> list:
        return [self.list_widget.item(i).text() for i in range(self.list_widget.count())]


class PluginListPicker(QWidget):
    """Same idea as ListPicker, but each entry is a discovered plugin .so
    path plus an optional JSON config (e.g. {"iface": "vcan0"}), stored
    structured (not re-parsed from display text).

    "Plugin config" builds one input field per key the selected plugin's
    config schema declares (see GET /api/host/info's "plugins"[]
    "config_schema", read from a <name>.schema.json sidecar file next to
    the .so -- see cmake/BoAtPlugin.cmake -- since a compiled .so has
    nothing to import/introspect the way a node script's build_parser()
    does; this is that same per-argument-fields idea, just sourced from a
    static file instead of live reflection). A plugin with no sidecar
    schema just has an empty/hidden group here -- the flat JSON config
    field below remains the only way to configure it, exactly like before
    this feature existed. Field type per schema entry's "type": "bool" ->
    QCheckBox, a "enum" list -> QComboBox of those choices, "array" -> a
    comma-separated QLineEdit split into a JSON list (of "item_type",
    default "string") on submit, everything else -> QLineEdit with an
    "e.g. <default>" placeholder falling back to "help" text.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        path_row = QHBoxLayout()
        self.combo = QComboBox()
        self.combo.setEditable(True)
        path_row.addWidget(self.combo, 1)
        add_btn = QPushButton("+ Add")
        add_btn.clicked.connect(self.add_current)
        path_row.addWidget(add_btn)
        layout.addLayout(path_row)

        self.config_group = QGroupBox("Plugin config")
        self.config_form = QFormLayout(self.config_group)
        self.config_group.setVisible(False)
        layout.addWidget(self.config_group)
        self._config_widgets: dict = {}   # key -> QCheckBox | QComboBox | QLineEdit
        self._config_specs: dict = {}     # key -> schema spec dict (type/item_type/enum)

        self.config_edit = QLineEdit()
        self.config_edit.setPlaceholderText(
            'anything not covered above, e.g. {"extra_key": 1}'
        )
        layout.addWidget(self.config_edit)

        self.list_widget = QListWidget()
        self.list_widget.setFixedHeight(70)
        layout.addWidget(self.list_widget)
        remove_btn = QPushButton("Remove selected")
        remove_btn.clicked.connect(self.remove_selected)
        layout.addWidget(remove_btn)

        self.combo.currentIndexChanged.connect(self._rebuild_config_fields)

    def set_choices(self, plugins: list) -> None:
        """plugins: [{"path", "config_schema"}, ...] from GET
        /api/host/info's "plugins" (see launcher_agent.py's
        _discover_plugins())."""
        current = self.combo.currentText()
        self.combo.clear()
        for p in plugins:
            self.combo.addItem(p["path"], p.get("config_schema") or {})
        self.combo.setCurrentText(current)
        self._rebuild_config_fields()

    def _rebuild_config_fields(self) -> None:
        """Rebuilds the "Plugin config" group from the selected combo
        item's schema -- called whenever the combo selection changes
        (currentIndexChanged), and once after add_current() clears the
        combo back to empty (so leftover fields from the just-added
        plugin don't linger for the next one). NOT called from inside
        add_current() itself before collecting values -- see that
        method's comment for why that would be actively wrong here."""
        while self.config_form.rowCount():
            self.config_form.removeRow(0)
        self._config_widgets = {}
        self._config_specs = {}

        idx = self.combo.currentIndex()
        schema = self.combo.itemData(idx) if idx >= 0 else None
        schema = schema or {}
        for key, spec in schema.items():
            typ = spec.get("type", "string")
            help_text = spec.get("help") or ""
            default = spec.get("default")
            enum = spec.get("enum")
            self._config_specs[key] = spec
            if typ == "bool":
                w = QCheckBox()
                w.setChecked(bool(default))
                if help_text:
                    w.setToolTip(help_text)
            elif enum:
                w = QComboBox()
                w.addItems([str(v) for v in enum])
                if default is not None:
                    found = w.findText(str(default))
                    if found >= 0:
                        w.setCurrentIndex(found)
                if help_text:
                    w.setToolTip(help_text)
            else:
                w = QLineEdit()
                if typ == "array" and isinstance(default, list):
                    example = ",".join(str(v) for v in default)
                else:
                    example = default
                w.setPlaceholderText(f"e.g. {example}" if example not in (None, "") else help_text)
                if help_text:
                    w.setToolTip(help_text)
            self.config_form.addRow(key, w)
            self._config_widgets[key] = w

        self.config_group.setVisible(bool(schema))

    def _collect_config_from_fields(self) -> dict:
        """Turns the current per-key field values into a config dict,
        typed per each key's schema spec. Raises ValueError (with a
        message naming the offending key) on a bad int/array entry --
        add_current() turns that into a warning dialog rather than
        silently sending garbage to the gateway."""
        cfg: dict = {}
        for key, w in self._config_widgets.items():
            spec = self._config_specs.get(key, {})
            typ = spec.get("type", "string")
            if typ == "bool":
                cfg[key] = w.isChecked()
                continue
            if isinstance(w, QComboBox):
                cfg[key] = w.currentText()
                continue
            text = w.text().strip()
            if not text:
                continue  # left blank -- plugin's own default applies
            if typ == "int":
                try:
                    cfg[key] = int(text, 0)
                except ValueError:
                    raise ValueError(f'"{key}" must be a whole number, got {text!r}')
            elif typ == "array":
                item_type = spec.get("item_type", "string")
                items = [v.strip() for v in text.split(",") if v.strip()]
                if item_type == "int":
                    try:
                        items = [int(v, 0) for v in items]
                    except ValueError:
                        raise ValueError(f'"{key}" items must all be whole numbers')
                cfg[key] = items
            else:
                cfg[key] = text
        return cfg

    def add_current(self) -> None:
        path = self.combo.currentText().strip()
        if not path:
            return
        # No defensive _rebuild_config_fields() here, unlike NewNodeDialog's
        # equivalent pattern -- unlike there, calling it here would destroy
        # and recreate every field *before* reading it, silently discarding
        # whatever the user just typed/checked. currentIndexChanged already
        # keeps these fields in sync with the combo selection at all times;
        # there's nothing to resync at submit time. (Caveat: typing a
        # custom path that doesn't match any known plugin leaves whatever
        # fields were last shown attached to it -- a pre-existing, minor
        # edge case of combo.setEditable(True), not something this method
        # should paper over by wiping real input.)
        try:
            cfg = self._collect_config_from_fields()
        except ValueError as e:
            QMessageBox.warning(self, "Invalid plugin config", str(e))
            return
        cfg_text = self.config_edit.text().strip()
        if cfg_text:
            try:
                extra = json.loads(cfg_text)
            except json.JSONDecodeError as e:
                QMessageBox.warning(self, "Invalid plugin config", f"Not valid JSON: {e}")
                return
            cfg.update(extra)
        self.add_value(path, cfg)
        self.combo.setCurrentText("")
        self.config_edit.clear()
        self._rebuild_config_fields()

    def add_value(self, path: str, config: dict) -> None:
        """Programmatic add, bypassing the combo/config-field parsing --
        used to pre-fill the Edit dialog from an existing instance's
        current node_plugins (already-structured, no JSON text to parse)."""
        label = os.path.basename(path) or path
        if config:
            label += f"  {json.dumps(config)}"
        item = QListWidgetItem(label)
        item.setData(Qt.UserRole, {"path": path, "config": config})
        self.list_widget.addItem(item)

    def remove_selected(self) -> None:
        for item in self.list_widget.selectedItems():
            self.list_widget.takeItem(self.list_widget.row(item))

    def values(self) -> list:
        return [self.list_widget.item(i).data(Qt.UserRole) for i in range(self.list_widget.count())]


class NewInstanceDialog(QDialog):
    """Doubles as the Edit dialog: pass `existing` (an instance dict from
    the table) + `existing_host_url` to pre-fill every field from its
    current definition, lock the host (an instance can't move between
    agents), and change the title/submit semantics accordingly --
    MainWindow.edit_selected() calls update_instance() instead of
    create_instance() with the same result_payload()."""

    def __init__(self, hosts: list, parent=None, existing: Optional[dict] = None,
                 existing_host_url: Optional[str] = None):
        super().__init__(parent)
        editing = existing is not None
        self.setWindowTitle("Edit Gateway Instance" if editing else "New Gateway Instance")
        self.resize(560, 600)
        layout = QFormLayout(self)

        paste_row = QHBoxLayout()
        self.paste_edit = QLineEdit()
        self.paste_edit.setPlaceholderText(
            'Paste a BOAT_CAN_INTERFACES=... BOAT_NODE_PLUGINS=... ./boat_gateway line here'
        )
        paste_row.addWidget(self.paste_edit, 1)
        parse_btn = QPushButton("Parse && Fill")
        parse_btn.clicked.connect(self._parse_and_fill)
        paste_row.addWidget(parse_btn)
        layout.addRow("From command line:", paste_row)

        self.host_combo = QComboBox()
        for h in hosts:
            self.host_combo.addItem(f"{h['name']} ({h['url']})", h["url"])
        if editing and existing_host_url:
            idx = self.host_combo.findData(existing_host_url)
            if idx >= 0:
                self.host_combo.setCurrentIndex(idx)
            self.host_combo.setEnabled(False)
        layout.addRow("Host:", self.host_combo)

        self.name_edit = QLineEdit(existing.get("name", "") if editing else "")
        layout.addRow("Name:", self.name_edit)

        self.can_picker = ListPicker()
        layout.addRow("CAN interfaces:", self.can_picker)

        self.eth_picker = ListPicker()
        layout.addRow("Eth interfaces:", self.eth_picker)

        self.plugin_picker = PluginListPicker()
        layout.addRow("Node plugins:", self.plugin_picker)

        self.port_edit = QLineEdit()
        self.port_edit.setPlaceholderText("auto")
        layout.addRow("gRPC port:", self.port_edit)

        self.tick_ms_edit = QLineEdit("1")
        layout.addRow("Node tick (ms):", self.tick_ms_edit)

        self.tick_us_edit = QLineEdit()
        self.tick_us_edit.setPlaceholderText("leave blank unless you need sub-ms precision")
        layout.addRow("Node tick (µs):", self.tick_us_edit)

        tick_note = QLabel(
            "This is the interval at which the gateway's main loop runs, and thus how often "
            "it processes messages. "
            "The lower the value, the more CPU it will use, but the more responsive it will be. "
            "BOAT_NODE_TICK_US overrides BOAT_NODE_TICK_MS when both are set. "
        )
        tick_note.setWordWrap(True)
        tick_note.setStyleSheet("color: gray; font-size: 11px;")
        layout.addRow("", tick_note)

        self.gw_bin_edit = QLineEdit()
        self.gw_bin_edit.setPlaceholderText("default build/debug path on that host")
        layout.addRow("Gateway binary:", self.gw_bin_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        _mark(buttons.button(QDialogButtonBox.Ok), "primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

        # Populate the pickers' dropdowns from the initially-selected host,
        # and again whenever the host selection changes.
        self.host_combo.currentIndexChanged.connect(self._reload_host_info)
        self._reload_host_info()

        if editing:
            for iface in existing.get("can_ifaces", []):
                self.can_picker.add_value(iface)
            for iface in existing.get("eth_ifaces", []):
                self.eth_picker.add_value(iface)
            for p in existing.get("node_plugins", []):
                self.plugin_picker.add_value(p["path"], p.get("config") or {})
            # Pre-filling the current port means "leave unchanged" round-trips
            # correctly -- InstanceRegistry.update() excludes this instance's
            # own current port from its collision check.
            if existing.get("grpc_port"):
                self.port_edit.setText(str(existing["grpc_port"]))
            # tick_ms_edit already defaults to "1" (the gateway's own
            # compiled-in default) -- only override it when this instance
            # has its own explicit value saved.
            if existing.get("tick_ms"):
                self.tick_ms_edit.setText(str(existing["tick_ms"]))
            if existing.get("tick_us"):
                self.tick_us_edit.setText(str(existing["tick_us"]))
            if existing.get("gateway_bin"):
                self.gw_bin_edit.setText(existing["gateway_bin"])

    def selected_host_url(self) -> str:
        return self.host_combo.currentData()

    def _parse_and_fill(self) -> None:
        text = self.paste_edit.text().strip()
        if not text:
            return
        try:
            parsed = _parse_command_line(text)
        except ValueError as e:
            QMessageBox.warning(self, "Parse failed", str(e))
            return
        # Replace whatever's already in the pickers -- pasting is a "start
        # fresh from this" action, not a merge.
        self.can_picker.list_widget.clear()
        self.eth_picker.list_widget.clear()
        self.plugin_picker.list_widget.clear()
        for iface in parsed["can_ifaces"]:
            self.can_picker.add_value(iface)
        for iface in parsed["eth_ifaces"]:
            self.eth_picker.add_value(iface)
        for p in parsed["node_plugins"]:
            self.plugin_picker.add_value(p["path"], p["config"])
        self.port_edit.setText(str(parsed["grpc_port"]) if parsed["grpc_port"] is not None else "")
        self.tick_ms_edit.setText(str(parsed["tick_ms"]) if parsed["tick_ms"] is not None else "")
        self.tick_us_edit.setText(str(parsed["tick_us"]) if parsed["tick_us"] is not None else "")
        self.gw_bin_edit.setText(parsed["gateway_bin"] or "")
        self.paste_edit.clear()

    def _reload_host_info(self) -> None:
        url = self.selected_host_url()
        if not url:
            return
        try:
            info = AgentClient(url).host_info()
        except AgentError:
            # Host unreachable right now -- leave dropdowns as-is; manual
            # entry (the combo boxes are editable) still works regardless.
            return
        ifaces = info.get("interfaces", [])
        can_names = [i["name"] for i in ifaces if i.get("type") in ("vcan", "can")]
        eth_names = [i["name"] for i in ifaces if i.get("type") in ("veth", "ether", "eth-virtual", "eth-raw")]
        self.can_picker.set_choices(can_names)
        self.eth_picker.set_choices(eth_names)
        self.plugin_picker.set_choices(info.get("plugins", []))

    def result_payload(self) -> dict:
        port_text = self.port_edit.text().strip()
        tick_ms_text = self.tick_ms_edit.text().strip()
        tick_us_text = self.tick_us_edit.text().strip()
        return {
            "name": self.name_edit.text().strip(),
            "can_ifaces": self.can_picker.values(),
            "eth_ifaces": self.eth_picker.values(),
            "node_plugins": self.plugin_picker.values(),
            "grpc_port": int(port_text) if port_text else None,
            "tick_ms": int(tick_ms_text) if tick_ms_text else None,
            "tick_us": int(tick_us_text) if tick_us_text else None,
            "gateway_bin": self.gw_bin_edit.text().strip() or None,
        }


class NewNodeDialog(QDialog):
    """New/Edit dialog for a node instance -- same doubles-as-Edit pattern as
    NewInstanceDialog. The Script combo is populated from the selected
    host's GET /api/node-scripts (boat-platform/nodes/*.py) and shows each
    script's module docstring below it.

    "Script arguments" builds one input field per CLI argument the script's
    build_parser() declares (see the "args" schema returned by
    /api/node-scripts, introspected agent-side) -- a QCheckBox for boolean
    flags (action="store_true"/"store_false"), a QLineEdit with the
    argument's default shown as an "e.g. ..." placeholder for everything
    else. --address is never among them (that's the Target gateway field
    below); the group is rebuilt from scratch whenever the Script selection
    changes. A script with no module-level build_parser() (or one whose
    introspection failed for any reason -- see launcher_agent.py's
    _introspect_node_args()) simply has an empty args schema, so this group
    stays empty/hidden and Extra args below is the only way to pass
    anything -- exactly today's behavior, unregressed.

    Extra args below remains a free-text field parsed with shlex.split()
    on submit, now specifically as the escape hatch for anything the
    per-argument fields don't cover: a flag genuinely not in the script's
    schema, or fields left blank on purpose. On submit, populated
    per-argument fields are prepended to whatever's typed in Extra args.
    In Edit mode, existing extra_args are walked and any recognized
    --flag [value] pairs are pulled back into their matching field,
    leaving only the unrecognized leftovers in Extra args.

    Target gateway is a dropdown of that same host's own gateway instances
    (from GET /api/instances) rather than a free-text "host:port" field --
    a node's process is spawned by the agent on its *own* host, same as any
    gateway instance it's pointed at there, so the address is always
    reachable as localhost:<port>; the "Host:" field above already picked
    which machine, spelling out an IP/hostname again in Target gateway was
    redundant and easy to get wrong. Typing a bare port number normalizes
    to localhost:<port> too; a full "host:port" is still accepted verbatim
    for the rarer case of pointing a node at a gateway on a *different*
    machine."""

    def __init__(self, hosts: list, parent=None, existing: Optional[dict] = None,
                 existing_host_url: Optional[str] = None):
        super().__init__(parent)
        self._hosts = hosts  # kept for _reload_target_hosts() to query every
                              # configured host, not just the selected one
        editing = existing is not None
        self.setWindowTitle("Edit Node" if editing else "New Node")
        self.resize(560, 480)
        layout = QFormLayout(self)

        paste_row = QHBoxLayout()
        self.paste_edit = QLineEdit()
        self.paste_edit.setPlaceholderText(
            'Paste a BOAT_HOST=... python3 <script> <args> line here'
        )
        paste_row.addWidget(self.paste_edit, 1)
        parse_btn = QPushButton("Parse && Fill")
        parse_btn.clicked.connect(self._parse_and_fill)
        paste_row.addWidget(parse_btn)
        layout.addRow("From command line:", paste_row)

        self.host_combo = QComboBox()
        for h in hosts:
            self.host_combo.addItem(f"{h['name']} ({h['url']})", h["url"])
        if editing and existing_host_url:
            idx = self.host_combo.findData(existing_host_url)
            if idx >= 0:
                self.host_combo.setCurrentIndex(idx)
            self.host_combo.setEnabled(False)
        layout.addRow("Host:", self.host_combo)

        self.name_edit = QLineEdit(existing.get("name", "") if editing else "")
        layout.addRow("Name:", self.name_edit)

        self.script_combo = QComboBox()
        layout.addRow("Script:", self.script_combo)

        self.script_doc_label = QLabel("")
        self.script_doc_label.setWordWrap(True)
        self.script_doc_label.setStyleSheet("color: gray; font-size: 11px;")
        layout.addRow("", self.script_doc_label)

        self.target_host_combo = QComboBox()
        self.target_host_combo.setEditable(True)
        self.target_host_combo.lineEdit().setPlaceholderText(
            "pick a gateway below, or type its port (e.g. 50052)"
        )
        layout.addRow("Target gateway:", self.target_host_combo)

        # One field per script argument, rebuilt whenever the Script
        # selection changes -- see _rebuild_arg_fields(). Empty/hidden for
        # a script with no discoverable build_parser().
        self.args_group = QGroupBox("Script arguments")
        self.args_form = QFormLayout(self.args_group)
        self.args_group.setVisible(False)
        layout.addRow(self.args_group)
        self._arg_widgets: dict = {}   # flag ("--iface") -> QCheckBox | QLineEdit
        self._arg_is_flag: dict = {}   # flag -> bool (True = boolean store_true/store_false)

        self.extra_args_edit = QLineEdit("")
        self.extra_args_edit.setPlaceholderText(
            'anything not covered above, e.g. --some-flag value'
        )
        layout.addRow("Extra args:", self.extra_args_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        _mark(buttons.button(QDialogButtonBox.Ok), "primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

        self.host_combo.currentIndexChanged.connect(self._reload_scripts)
        self.host_combo.currentIndexChanged.connect(self._reload_target_hosts)
        self.script_combo.currentIndexChanged.connect(self._update_doc_label)
        self.script_combo.currentIndexChanged.connect(self._rebuild_arg_fields)
        self._reload_scripts()
        self._reload_target_hosts()

        if editing:
            idx = self.script_combo.findData(existing.get("script_path", ""))
            if idx >= 0:
                self.script_combo.setCurrentIndex(idx)
            self._update_doc_label()
            self._rebuild_arg_fields()  # defensive re-run: guarantees the
                                         # fields match this script even if
                                         # setCurrentIndex() above didn't
                                         # actually change the index (and so
                                         # didn't fire currentIndexChanged)
            leftover = self._prefill_arg_fields(existing.get("extra_args", []))
            self.extra_args_edit.setText(shlex.join(leftover))
            self.target_host_combo.setCurrentText(existing.get("target_host", ""))

    def selected_host_url(self) -> str:
        return self.host_combo.currentData()

    def _reload_scripts(self) -> None:
        url = self.selected_host_url()
        if not url:
            return
        try:
            scripts = AgentClient(url).list_node_scripts()
        except AgentError:
            # Host unreachable right now -- leave the combo as-is.
            return
        current = self.script_combo.currentData()
        self.script_combo.clear()
        for s in scripts:
            label = s["name"]
            if s.get("interactive"):
                label += "  (interactive -- can't run headlessly)"
            self.script_combo.addItem(label, s["path"])
            self.script_combo.setItemData(self.script_combo.count() - 1, s.get("docstring", ""), Qt.UserRole + 1)
            self.script_combo.setItemData(self.script_combo.count() - 1, s.get("args", []), Qt.UserRole + 2)
        if current:
            idx = self.script_combo.findData(current)
            if idx >= 0:
                self.script_combo.setCurrentIndex(idx)
        self._update_doc_label()
        self._rebuild_arg_fields()

    def _reload_target_hosts(self) -> None:
        """Populate from *every* configured host's gateway instances, not
        just the one this node will run on -- a node can target a gateway
        on a different machine, as long as it's network-reachable. A
        same-host entry resolves to localhost:<port> (robust, no DNS
        needed, and correct: the node and that gateway are the exact same
        machine). A cross-host entry resolves to that *other* host's own
        address instead, parsed from its agent URL -- localhost would be
        wrong there, since from the node's point of view (running on this
        dialog's selected Host) "localhost" means itself, not the other
        machine."""
        node_host_url = self.selected_host_url()
        current = self.target_host_combo.currentText()
        self.target_host_combo.clear()
        for h in self._hosts:
            try:
                instances = AgentClient(h["url"]).list_instances()
            except AgentError:
                continue  # that host's agent unreachable right now -- skip it
            same_host = (h["url"] == node_host_url)
            if same_host:
                addr_host = "localhost"
                tag = ""
            else:
                addr_host = urlparse(h["url"]).hostname or h["url"]
                tag = f"[{h['name']}] "
            for inst in instances:
                addr = f"{addr_host}:{inst['grpc_port']}"
                self.target_host_combo.addItem(f"{tag}{inst['name']} — {addr} ({inst['status']})", addr)
        self.target_host_combo.setCurrentText(current)

    def _update_doc_label(self) -> None:
        idx = self.script_combo.currentIndex()
        doc = self.script_combo.itemData(idx, Qt.UserRole + 1) if idx >= 0 else None
        self.script_doc_label.setText(doc or "")

    def _rebuild_arg_fields(self) -> None:
        """Rebuilds the "Script arguments" group from the selected script's
        args schema (see /api/node-scripts' "args", stashed on the combo
        item at construction time). Values are NOT preserved across a
        rebuild -- a different script has unrelated arguments -- Edit mode
        re-populates fresh via _prefill_arg_fields() right after this
        runs."""
        while self.args_form.rowCount():
            self.args_form.removeRow(0)
        self._arg_widgets = {}
        self._arg_is_flag = {}

        idx = self.script_combo.currentIndex()
        specs = self.script_combo.itemData(idx, Qt.UserRole + 2) if idx >= 0 else None
        specs = specs or []
        for spec in specs:
            flag = spec.get("flag")
            if not flag:
                continue
            is_flag = bool(spec.get("is_flag"))
            help_text = spec.get("help") or ""
            self._arg_is_flag[flag] = is_flag
            if is_flag:
                w = QCheckBox()
                w.setChecked(bool(spec.get("default")))
                if help_text:
                    w.setToolTip(help_text)
            else:
                w = QLineEdit()
                default = spec.get("default")
                w.setPlaceholderText(f"e.g. {default}" if default not in (None, "") else help_text)
                if help_text:
                    w.setToolTip(help_text)
            self.args_form.addRow(flag, w)
            self._arg_widgets[flag] = w

        self.args_group.setVisible(bool(specs))

    def _prefill_arg_fields(self, extra_args: list) -> list:
        """Walks an existing node's extra_args, pulling any --flag [value]
        pair the current script's schema recognizes into its matching
        dynamic field, and returns the leftover tokens (an argument the
        schema doesn't know about -- e.g. saved by an older version of the
        script, or genuinely free-form) for the flat Extra args field."""
        leftover: list = []
        i = 0
        while i < len(extra_args):
            tok = extra_args[i]
            if tok in self._arg_widgets:
                w = self._arg_widgets[tok]
                if self._arg_is_flag.get(tok):
                    w.setChecked(True)
                    i += 1
                elif i + 1 < len(extra_args):
                    w.setText(extra_args[i + 1])
                    i += 2
                else:
                    i += 1  # flag with no following value -- drop it, nothing to fill
            else:
                leftover.append(tok)
                i += 1
        return leftover

    def _parse_and_fill(self) -> None:
        text = self.paste_edit.text().strip()
        if not text:
            return
        try:
            parsed = _parse_node_command_line(text)
        except ValueError as e:
            QMessageBox.warning(self, "Parse failed", str(e))
            return
        if parsed["target_host"]:
            self.target_host_combo.setCurrentText(parsed["target_host"])
        # Select the parsed script in the combo, adding it if it isn't
        # among this host's currently-discovered ones (e.g. pasted before
        # the host finished loading, or genuinely not under this host's
        # boat-platform/nodes/).
        idx = self.script_combo.findData(parsed["script_path"])
        if idx < 0:
            self.script_combo.addItem(os.path.basename(parsed["script_path"]), parsed["script_path"])
            idx = self.script_combo.count() - 1
        self.script_combo.setCurrentIndex(idx)
        self._update_doc_label()
        self._rebuild_arg_fields()  # defensive re-run, same reasoning as in __init__
        leftover = self._prefill_arg_fields(parsed["extra_args"])
        self.extra_args_edit.setText(shlex.join(leftover))
        self.paste_edit.clear()

    def result_payload(self) -> dict:
        script_path = self.script_combo.currentData()
        if not script_path:
            raise ValueError("select a node script")

        # Populated per-argument fields first, then whatever's typed in
        # the flat Extra args field -- if both happen to set the same
        # flag (e.g. the user filled --iface above AND typed "--iface
        # vcan1" below), the script itself will just take the last one on
        # its command line, same as any argparse invocation would.
        extra_args: list = []
        for flag, w in self._arg_widgets.items():
            if self._arg_is_flag.get(flag):
                if w.isChecked():
                    extra_args.append(flag)
            else:
                val = w.text().strip()
                if val:
                    extra_args.append(flag)
                    extra_args.append(val)
        try:
            extra_args += shlex.split(self.extra_args_edit.text().strip())
        except ValueError as e:
            raise ValueError(f"invalid extra args: {e}") from e

        # currentText() on an editable combo is whatever's in the line
        # edit -- for a *picked* dropdown entry that's the full display
        # label ("main — localhost:50051 (running)"), not the plain
        # address stored as that item's data. Only trust currentData() when
        # the displayed text still matches the selected item's label
        # exactly (i.e. the user picked it and didn't then retype);
        # otherwise treat the text as free-form entry.
        target_text = self.target_host_combo.currentText().strip()
        idx = self.target_host_combo.currentIndex()
        if idx >= 0 and target_text == self.target_host_combo.itemText(idx):
            target_host = self.target_host_combo.itemData(idx)
        elif target_text.isdigit():
            target_host = f"localhost:{target_text}"
        else:
            target_host = target_text

        return {
            "name": self.name_edit.text().strip(),
            "script_path": script_path,
            "target_host": target_host,
            "extra_args": extra_args,
        }


class NewTestRunDialog(QDialog):
    """New/Edit dialog for a test run -- one invocation of `boat test run
    <manifest.json>` (the automated CI-style HIL suite runner, not the
    manual test/*.md TestSuite). Same doubles-as-Edit pattern as
    NewInstanceDialog/NewNodeDialog.

    Manifest and Environment dropdowns are both populated from the
    selected host's GET /api/test-manifests / /api/test-environments
    (both discovered by scanning boat-platform/config/tests/ on that
    host) -- unlike a node's Target gateway, an environment config is a
    *local file* read by `boat test run` on the same host, so there's no
    cross-host resolution to do here the way Nodes' Target gateway
    dropdown needs.

    Selecting a manifest pre-selects its own declared environment_config
    in the Environment dropdown (still overridable) -- mirrors `boat test
    run <manifest> --config <override>`'s own semantics: the manifest's
    environment_config is the default, an explicit --config overrides it.

    Extra args is a free-text field (shlex-split on submit) for anything
    else `boat test run` accepts (--stop-on-failure, --parallel N,
    --preflight, --no-html, --allure DIR, --trace-format, --recorder-url,
    -v/--verbose) -- deliberately flat rather than one field per flag,
    matching Nodes' Extra Args: the flag surface here is modest and fixed
    (it's the same `boat test run` CLI regardless of which manifest is
    picked), so there's no per-manifest argument schema to introspect and
    build fields from the way node scripts have."""

    def __init__(self, hosts: list, parent=None, existing: Optional[dict] = None,
                 existing_host_url: Optional[str] = None):
        super().__init__(parent)
        editing = existing is not None
        self.setWindowTitle("Edit Test Run" if editing else "New Test Run")
        self.resize(560, 420)
        layout = QFormLayout(self)

        self.host_combo = QComboBox()
        for h in hosts:
            self.host_combo.addItem(f"{h['name']} ({h['url']})", h["url"])
        if editing and existing_host_url:
            idx = self.host_combo.findData(existing_host_url)
            if idx >= 0:
                self.host_combo.setCurrentIndex(idx)
            self.host_combo.setEnabled(False)
        layout.addRow("Host:", self.host_combo)

        self.name_edit = QLineEdit(existing.get("name", "") if editing else "")
        layout.addRow("Name:", self.name_edit)

        self.manifest_combo = QComboBox()
        layout.addRow("Manifest:", self.manifest_combo)

        self.manifest_doc_label = QLabel("")
        self.manifest_doc_label.setWordWrap(True)
        self.manifest_doc_label.setStyleSheet("color: gray; font-size: 11px;")
        layout.addRow("", self.manifest_doc_label)

        self.env_combo = QComboBox()
        layout.addRow("Environment:", self.env_combo)

        self.env_doc_label = QLabel("")
        self.env_doc_label.setWordWrap(True)
        self.env_doc_label.setStyleSheet("color: gray; font-size: 11px;")
        layout.addRow("", self.env_doc_label)

        self.extra_args_edit = QLineEdit(
            shlex.join(existing.get("extra_args", [])) if editing else ""
        )
        self.extra_args_edit.setPlaceholderText(
            '--stop-on-failure --parallel 2 --preflight -v'
        )
        layout.addRow("Extra args:", self.extra_args_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        _mark(buttons.button(QDialogButtonBox.Ok), "primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

        self.host_combo.currentIndexChanged.connect(self._reload_manifests_and_envs)
        self.manifest_combo.currentIndexChanged.connect(self._on_manifest_changed)
        self.env_combo.currentIndexChanged.connect(self._update_env_doc_label)
        self._reload_manifests_and_envs()

        if editing:
            idx = self.manifest_combo.findData(existing.get("manifest_path", ""))
            if idx >= 0:
                self.manifest_combo.setCurrentIndex(idx)
            self._on_manifest_changed()  # defensive re-run -- see NewNodeDialog's
                                          # equivalent comment on why this matters
                                          # even when setCurrentIndex() didn't change
                                          # anything (index already matched)
            env_path = existing.get("env_config_path", "")
            if env_path:
                idx = self.env_combo.findData(env_path)
                if idx >= 0:
                    self.env_combo.setCurrentIndex(idx)  # overrides the manifest-driven default
            self._update_env_doc_label()

    def selected_host_url(self) -> str:
        return self.host_combo.currentData()

    def _reload_manifests_and_envs(self) -> None:
        url = self.selected_host_url()
        if not url:
            return
        try:
            client = AgentClient(url)
            manifests = client.list_test_manifests()
            environments = client.list_test_environments()
        except AgentError:
            return  # host unreachable right now -- leave the combos as-is

        current_manifest = self.manifest_combo.currentData()
        self.manifest_combo.clear()
        for m in manifests:
            label = f"{m['name']}  ({m.get('test_count', 0)} test(s))"
            self.manifest_combo.addItem(label, m["path"])
            idx = self.manifest_combo.count() - 1
            self.manifest_combo.setItemData(idx, m.get("description", ""), Qt.UserRole + 1)
            self.manifest_combo.setItemData(idx, m.get("environment_config", ""), Qt.UserRole + 2)
        if current_manifest:
            idx = self.manifest_combo.findData(current_manifest)
            if idx >= 0:
                self.manifest_combo.setCurrentIndex(idx)

        current_env = self.env_combo.currentData()
        self.env_combo.clear()
        self.env_combo.addItem("(manifest default)", "")
        for e in environments:
            self.env_combo.addItem(f"{e['name']}  ({e.get('gateway_address', '?')})", e["path"])
            self.env_combo.setItemData(self.env_combo.count() - 1, e.get("description", ""), Qt.UserRole + 1)
        if current_env:
            idx = self.env_combo.findData(current_env)
            if idx >= 0:
                self.env_combo.setCurrentIndex(idx)

        self._on_manifest_changed()

    def _on_manifest_changed(self) -> None:
        """Selecting a manifest pre-selects its own declared
        environment_config in the Environment dropdown, if that config is
        one this host actually discovered -- still overridable afterward,
        matching `boat test run`'s own --config semantics (the manifest's
        environment_config is the default, an explicit override wins)."""
        self._update_manifest_doc_label()
        idx = self.manifest_combo.currentIndex()
        default_env = self.manifest_combo.itemData(idx, Qt.UserRole + 2) if idx >= 0 else None
        if default_env:
            env_idx = self.env_combo.findData(default_env)
            if env_idx >= 0:
                self.env_combo.setCurrentIndex(env_idx)
        self._update_env_doc_label()

    def _update_manifest_doc_label(self) -> None:
        idx = self.manifest_combo.currentIndex()
        doc = self.manifest_combo.itemData(idx, Qt.UserRole + 1) if idx >= 0 else None
        self.manifest_doc_label.setText(doc or "")

    def _update_env_doc_label(self) -> None:
        idx = self.env_combo.currentIndex()
        doc = self.env_combo.itemData(idx, Qt.UserRole + 1) if idx >= 0 else None
        self.env_doc_label.setText(doc or "")

    def result_payload(self) -> dict:
        manifest_path = self.manifest_combo.currentData()
        if not manifest_path:
            raise ValueError("select a manifest")
        env_config_path = self.env_combo.currentData() or ""
        try:
            extra_args = shlex.split(self.extra_args_edit.text().strip())
        except ValueError as e:
            raise ValueError(f"invalid extra args: {e}") from e
        return {
            "name": self.name_edit.text().strip(),
            "manifest_path": manifest_path,
            "env_config_path": env_config_path,
            "extra_args": extra_args,
        }


class TestReportDialog(QDialog):
    """Renders a test run's report.json content inline -- fetched from the
    agent's GET /api/test-runs/{id}/report, which reads report.json off
    its own disk (one per manifest test entry, under report_dir) and hands
    back the parsed content directly. Deliberately not a filesystem
    browser: the whole point is this works from any client regardless of
    whether admin_gui happens to be running on the same host as the agent
    -- the same federated-host situation the Report directory field's own
    "no Open button" is about (see AGENTS.md's Launcher Agent section and
    ui/launcher_agent.py's _read_test_run_report())."""

    def __init__(self, client: AgentClient, run_id: str, run_name: str, parent=None):
        super().__init__(parent)
        self.client = client
        self.run_id = run_id
        self.setWindowTitle(f"Test Report — {run_name or run_id}")
        self.resize(820, 560)

        layout = QVBoxLayout(self)

        self.summary_label = QLabel("Loading…")
        layout.addWidget(self.summary_label)

        splitter = QSplitter(Qt.Vertical)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["Test", "Verdict", "Duration", "Summary"])
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)
        splitter.addWidget(self.tree)

        self.detail_view = QPlainTextEdit()
        self.detail_view.setReadOnly(True)
        splitter.addWidget(self.detail_view)
        splitter.setSizes([220, 340])
        layout.addWidget(splitter, 1)

        btns = QHBoxLayout()
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.reload)
        btns.addWidget(refresh_btn)
        btns.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btns.addWidget(close_btn)
        layout.addLayout(btns)

        self._entries: list = []
        self.reload()

    def reload(self) -> None:
        try:
            data = self.client.get_test_run_report(self.run_id)
        except AgentError as e:
            self.summary_label.setText(f"Failed to load report: {e}")
            self.tree.clear()
            self.detail_view.clear()
            self._entries = []
            return

        self._entries = data.get("tests", [])
        self.tree.clear()

        if not data.get("exists"):
            self.summary_label.setText(
                f"No report directory yet at {data.get('report_dir') or '(none)'} "
                f"-- start the run first."
            )
            self.detail_view.clear()
            return

        passed = sum(1 for e in self._entries if (e.get("report") or {}).get("verdict") == "PASS")
        total = len(self._entries)
        self.summary_label.setText(
            f"{data.get('report_dir')} — {passed}/{total} passed"
            if total else f"{data.get('report_dir')} — no test folders yet"
        )

        for entry in self._entries:
            report = entry.get("report")
            if report is None:
                item = QTreeWidgetItem([entry.get("folder", "?"), "?", "", entry.get("error", "")])
                self.tree.addTopLevelItem(item)
                continue
            test = report.get("test") or {}
            execu = report.get("execution") or {}
            verdict = report.get("verdict", "?")
            duration = execu.get("duration_ms")
            item = QTreeWidgetItem([
                test.get("id", entry.get("folder", "?")),
                verdict,
                f"{duration}ms" if duration is not None else "",
                report.get("summary", ""),
            ])
            color = _VERDICT_COLORS.get(verdict)
            if color:
                for col in range(4):
                    item.setForeground(col, color)
            self.tree.addTopLevelItem(item)

        for col in range(4):
            self.tree.resizeColumnToContents(col)

        if self._entries:
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        else:
            self.detail_view.clear()

    def _on_selection_changed(self) -> None:
        current = self.tree.currentItem()
        idx = self.tree.indexOfTopLevelItem(current) if current else -1
        if idx < 0 or idx >= len(self._entries):
            self.detail_view.clear()
            return
        self.detail_view.setPlainText(_format_test_report_entry(self._entries[idx]))


_MAX_IFNAME_LEN = 15  # Linux IFNAMSIZ - 1, a kernel-enforced hard limit --
                      # mirrors the same constant in ui/launcher_agent.py.
                      # Checked here too so a too-long name (especially a
                      # veth name whose auto-generated "_peer" suffix pushes
                      # it over) gets a clear message before the network
                      # round trip, not just after the agent's own 400.


class NewInterfaceDialog(QDialog):
    """New vcan or veth interface -- picks a host + a name, exercising the
    agent's POST /api/interfaces/vcan|veth (the same `ip link add`/
    `modprobe vcan` commands ui/launcher.py's own equivalent endpoints
    run)."""

    def __init__(self, kind: str, hosts: list, parent=None):
        super().__init__(parent)
        assert kind in ("vcan", "veth")
        self.kind = kind
        self.setWindowTitle(f"New {kind} interface")
        layout = QFormLayout(self)

        self.host_combo = QComboBox()
        for h in hosts:
            self.host_combo.addItem(f"{h['name']} ({h['url']})", h["url"])
        layout.addRow("Host:", self.host_combo)

        self.name_edit = QLineEdit(f"{kind}0")
        layout.addRow("Name:", self.name_edit)

        if kind == "veth":
            self.peer_label = QLabel("")
            self.peer_label.setStyleSheet("color: gray; font-size: 11px;")
            layout.addRow("", self.peer_label)
            self.name_edit.textChanged.connect(self._update_peer_label)
            self._update_peer_label()

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        _mark(buttons.button(QDialogButtonBox.Ok), "primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def _update_peer_label(self) -> None:
        name = self.name_edit.text().strip() or "veth0"
        peer = f"{name}_peer"
        if len(peer) > _MAX_IFNAME_LEN:
            self.peer_label.setText(
                f"Peer end would be '{peer}' ({len(peer)} chars) -- over Linux's "
                f"{_MAX_IFNAME_LEN}-char interface name limit. Use "
                f"{_MAX_IFNAME_LEN - len('_peer')} characters or fewer."
            )
            self.peer_label.setStyleSheet("color: #c62828; font-size: 11px;")
        else:
            self.peer_label.setText(f"Peer end will be created as: {peer}")
            self.peer_label.setStyleSheet("color: gray; font-size: 11px;")

    def selected_host_url(self) -> str:
        return self.host_combo.currentData()

    def result_name(self) -> str:
        name = self.name_edit.text().strip()
        if not name:
            raise ValueError("enter an interface name")
        max_len = _MAX_IFNAME_LEN - len("_peer") if self.kind == "veth" else _MAX_IFNAME_LEN
        if len(name) > max_len:
            raise ValueError(
                f"'{name}' is too long ({len(name)} chars) -- Linux interface "
                f"names are capped at {_MAX_IFNAME_LEN} characters"
                + (f", and a veth peer name gets '_peer' appended" if self.kind == "veth" else "")
            )
        return name


class CanConfigDialog(QDialog):
    """Bitrate (+ optional CAN FD data-bitrate) for an existing type-can
    link -- `ip link set <name> {up|down} type can bitrate <b> [dbitrate
    <d> fd {on|off}]`, the exact commands boat_cli/bus_setup_context.py's
    "Physical CAN" section documents. Works on any type-can interface,
    virtual or physical -- vcan has no real bitrate and the kernel will
    reject it; that failure surfaces the same way any other configure
    error does, not special-cased here.

    `current`, when available (GET /api/interfaces/{name}/can-config --
    None for vcan or anything that isn't a real CAN link), pre-fills every
    field with the interface's *actual* current state instead of fixed
    defaults -- found missing the hard way: a real PEAK PCAN-USB Pro FD
    already running CAN FD at 500000/2000000 showed this dialog with CAN
    FD unchecked and 500000/2000000 as if those were just placeholder
    defaults, not what the hardware was actually doing."""

    def __init__(self, host_name: str, iface_name: str, current: Optional[dict] = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Configure CAN — {iface_name}")
        layout = QFormLayout(self)

        layout.addRow("Host:", QLabel(host_name))
        layout.addRow("Interface:", QLabel(iface_name))

        if current is not None:
            current_text = f"Current: {current['bitrate']} bps"
            current_text += (f", FD data bitrate {current['dbitrate']} bps" if current.get("fd")
                              else " (classic CAN, no FD)")
        else:
            current_text = "Current config unknown (not a real CAN interface, or couldn't be read)"
        current_label = QLabel(current_text)
        current_label.setWordWrap(True)
        current_label.setStyleSheet("color: gray; font-size: 11px;")
        layout.addRow("", current_label)

        self.bitrate_edit = QLineEdit(str(current["bitrate"]) if current else "500000")
        layout.addRow("Bitrate:", self.bitrate_edit)

        self.fd_check = QCheckBox("CAN FD")
        self.fd_check.setChecked(bool(current and current.get("fd")))
        layout.addRow("", self.fd_check)

        default_dbitrate = current.get("dbitrate") if current else None
        self.dbitrate_edit = QLineEdit(str(default_dbitrate) if default_dbitrate else "2000000")
        self.dbitrate_edit.setEnabled(self.fd_check.isChecked())
        layout.addRow("Data bitrate (FD):", self.dbitrate_edit)
        self.fd_check.toggled.connect(self.dbitrate_edit.setEnabled)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        _mark(buttons.button(QDialogButtonBox.Ok), "primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def result_payload(self) -> tuple:
        try:
            bitrate = int(self.bitrate_edit.text().strip())
        except ValueError as e:
            raise ValueError(f"invalid bitrate: {self.bitrate_edit.text()!r}") from e
        fd = self.fd_check.isChecked()
        dbitrate = None
        if fd:
            try:
                dbitrate = int(self.dbitrate_edit.text().strip())
            except ValueError as e:
                raise ValueError(f"invalid data bitrate: {self.dbitrate_edit.text()!r}") from e
        return bitrate, dbitrate, fd


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("BoAt Admin")
        self.resize(1200, 760)

        self.host_store = HostStore()
        self._snapshot: dict = {}
        self._selected: Optional[Tuple[str, str]] = None  # (host_url, instance_id)
        self._node_snapshot: dict = {}
        self._selected_node: Optional[Tuple[str, str]] = None  # (host_url, node_id)
        self._test_run_snapshot: dict = {}
        self._selected_test_run: Optional[Tuple[str, str]] = None  # (host_url, run_id)
        self._iface_snapshot: dict = {}
        self._selected_iface: Optional[Tuple[str, str]] = None  # (host_url, iface_name)

        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Sidebar navigation -- one page per "kind of thing" the agent
        # manages (gateway instances, nodes, test runs, interfaces), plus
        # Settings for host management (Add/Remove Host, Save/Load Session
        # -- host *definitions*, not any one page's own data, so they live
        # apart from the per-kind pages rather than pinned above all of
        # them the way they used to be). A QListWidget + QStackedWidget
        # pair rather than QTabWidget: gives full control over the
        # sidebar's look (icons, selected-item pill highlight) that
        # QTabBar's own styling can't easily reach. ──
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(200)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(0)

        title = QLabel("⛵  BoAt Admin")
        title.setObjectName("AppTitle")
        sidebar_layout.addWidget(title)

        self.nav_list = QListWidget()
        self.nav_list.setObjectName("NavList")
        self.nav_list.setFocusPolicy(Qt.NoFocus)
        sidebar_layout.addWidget(self.nav_list, 1)
        root.addWidget(sidebar)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(16, 16, 16, 16)
        self.stack = QStackedWidget()
        content_layout.addWidget(self.stack)
        root.addWidget(content, 1)

        pages = [
            ("Gateway", "♕", self._build_gateways_tab()),
            ("Nodes", "♘", self._build_nodes_tab()),
            ("Test Runs", "♗", self._build_test_runs_tab()),
            ("Interfaces", "♖", self._build_interfaces_tab()),
            ("Settings", "♙", self._build_settings_tab()),
        ]
        for label, icon, widget in pages:
            self.nav_list.addItem(QListWidgetItem(f"   {icon}   {label}"))
            self.stack.addWidget(widget)
        self.nav_list.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav_list.setCurrentRow(0)

        self.statusBar()
        self.refresh_host_list()

        self.worker = PollWorker(self.host_store.list, lambda: self._selected,
                                  lambda: self._selected_node, lambda: self._selected_test_run)
        self.worker.snapshot_ready.connect(self.on_snapshot)
        self.worker.log_ready.connect(self.on_log)
        self.worker.node_snapshot_ready.connect(self.on_node_snapshot)
        self.worker.node_log_ready.connect(self.on_node_log)
        self.worker.test_run_snapshot_ready.connect(self.on_test_run_snapshot)
        self.worker.test_run_log_ready.connect(self.on_test_run_log)
        self.worker.interfaces_ready.connect(self.on_interfaces_snapshot)
        self.worker.start()

    def _build_gateways_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # ── Hosts (shared setup the rest of this page's data depends on --
        # lives here rather than on its own Settings page; see add_host()/
        # remove_host()/refresh_host_list() below) ──
        host_bar = QHBoxLayout()
        host_bar.addWidget(QLabel("Hosts:"))
        self.host_list = QListWidget()
        self.host_list.setFixedHeight(80)
        self.host_list.setSelectionMode(QAbstractItemView.SingleSelection)
        host_bar.addWidget(self.host_list, 1)
        host_btns = QVBoxLayout()
        add_host_btn = QPushButton("+ Add Host")
        add_host_btn.clicked.connect(self.add_host)
        remove_host_btn = QPushButton("Remove Host")
        remove_host_btn.clicked.connect(self.remove_host)
        save_session_btn = QPushButton("Save Session…")
        save_session_btn.clicked.connect(self.save_session)
        load_session_btn = QPushButton("Load Session…")
        load_session_btn.clicked.connect(self.load_session)
        host_btns.addWidget(add_host_btn)
        host_btns.addWidget(remove_host_btn)
        host_btns.addWidget(save_session_btn)
        host_btns.addWidget(load_session_btn)
        host_bar.addLayout(host_btns)
        layout.addLayout(host_bar)

        # ── Instance table ──
        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels(
            ["Host", "Name", "ID", "Port", "Status", "PID", "Managed", "Interfaces", "Plugins", "Uptime"]
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self.on_selection_changed)
        layout.addWidget(self.table, 2)

        # ── Actions ──
        actions = QHBoxLayout()
        new_btn = QPushButton("New Instance…")
        new_btn.clicked.connect(self.new_instance)
        edit_btn = QPushButton("Edit…")
        edit_btn.clicked.connect(self.edit_selected)
        start_btn = _mark(QPushButton("Start"), "primary")
        start_btn.clicked.connect(self.start_selected)
        stop_btn = _mark(QPushButton("Stop"), "danger")
        stop_btn.clicked.connect(self.stop_selected)
        delete_btn = _mark(QPushButton("Delete"), "danger")
        delete_btn.clicked.connect(self.delete_selected)
        for b in (new_btn, edit_btn, start_btn, stop_btn, delete_btn):
            actions.addWidget(b)
        actions.addStretch(1)
        layout.addLayout(actions)

        # ── Log viewer ──
        layout.addWidget(QLabel("Log (selected instance):"))
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        layout.addWidget(self.log_view, 1)

        # ── Equivalent command line ──
        layout.addWidget(QLabel("Equivalent command line (selected instance):"))
        cmd_row = QHBoxLayout()
        self.cmdline_view = QLineEdit()
        self.cmdline_view.setReadOnly(True)
        self.cmdline_view.setPlaceholderText("Select an instance to see the equivalent shell command")
        cmd_row.addWidget(self.cmdline_view, 1)
        copy_btn = QPushButton("Copy")
        copy_btn.clicked.connect(self._copy_command_line)
        cmd_row.addWidget(copy_btn)
        layout.addLayout(cmd_row)

        return tab

    def _build_nodes_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # ── Node table ──
        self.node_table = QTableWidget(0, 9)
        self.node_table.setHorizontalHeaderLabels(
            ["Host", "Name", "ID", "Script", "Target Host", "Status", "PID", "Extra Args", "Uptime"]
        )
        self.node_table.horizontalHeader().setStretchLastSection(True)
        self.node_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.node_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.node_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.node_table.itemSelectionChanged.connect(self.on_node_selection_changed)
        layout.addWidget(self.node_table, 2)

        # ── Actions ──
        actions = QHBoxLayout()
        new_btn = QPushButton("New Node…")
        new_btn.clicked.connect(self.new_node)
        edit_btn = QPushButton("Edit…")
        edit_btn.clicked.connect(self.edit_node_selected)
        start_btn = _mark(QPushButton("Start"), "primary")
        start_btn.clicked.connect(self.start_node_selected)
        stop_btn = _mark(QPushButton("Stop"), "danger")
        stop_btn.clicked.connect(self.stop_node_selected)
        delete_btn = _mark(QPushButton("Delete"), "danger")
        delete_btn.clicked.connect(self.delete_node_selected)
        for b in (new_btn, edit_btn, start_btn, stop_btn, delete_btn):
            actions.addWidget(b)
        actions.addStretch(1)
        layout.addLayout(actions)

        # ── Log viewer ──
        layout.addWidget(QLabel("Log (selected node):"))
        self.node_log_view = QPlainTextEdit()
        self.node_log_view.setReadOnly(True)
        self.node_log_view.setMaximumBlockCount(2000)
        layout.addWidget(self.node_log_view, 1)

        # ── Equivalent command line ──
        layout.addWidget(QLabel("Equivalent command line (selected node):"))
        cmd_row = QHBoxLayout()
        self.node_cmdline_view = QLineEdit()
        self.node_cmdline_view.setReadOnly(True)
        self.node_cmdline_view.setPlaceholderText("Select a node to see the equivalent shell command")
        cmd_row.addWidget(self.node_cmdline_view, 1)
        copy_btn = QPushButton("Copy")
        copy_btn.clicked.connect(self._copy_node_command_line)
        cmd_row.addWidget(copy_btn)
        layout.addLayout(cmd_row)

        return tab

    def _build_test_runs_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # ── Test run table ──
        self.test_run_table = QTableWidget(0, 9)
        self.test_run_table.setHorizontalHeaderLabels(
            ["Host", "Name", "ID", "Manifest", "Environment", "Result", "Status", "PID", "Uptime"]
        )
        self.test_run_table.horizontalHeader().setStretchLastSection(True)
        self.test_run_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.test_run_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.test_run_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.test_run_table.itemSelectionChanged.connect(self.on_test_run_selection_changed)
        layout.addWidget(self.test_run_table, 2)

        # ── Actions ──
        actions = QHBoxLayout()
        new_btn = QPushButton("New Test Run…")
        new_btn.clicked.connect(self.new_test_run)
        edit_btn = QPushButton("Edit…")
        edit_btn.clicked.connect(self.edit_test_run_selected)
        start_btn = _mark(QPushButton("Start"), "primary")
        start_btn.clicked.connect(self.start_test_run_selected)
        stop_btn = _mark(QPushButton("Stop"), "danger")
        stop_btn.clicked.connect(self.stop_test_run_selected)
        report_btn = QPushButton("View Report")
        report_btn.clicked.connect(self.view_test_run_report_selected)
        delete_btn = _mark(QPushButton("Delete"), "danger")
        delete_btn.clicked.connect(self.delete_test_run_selected)
        for b in (new_btn, edit_btn, start_btn, stop_btn, report_btn, delete_btn):
            actions.addWidget(b)
        actions.addStretch(1)
        layout.addLayout(actions)

        # ── Log viewer ──
        layout.addWidget(QLabel("Log (selected test run):"))
        self.test_run_log_view = QPlainTextEdit()
        self.test_run_log_view.setReadOnly(True)
        self.test_run_log_view.setMaximumBlockCount(2000)
        layout.addWidget(self.test_run_log_view, 1)

        # ── Report directory ──
        # Just the path, not an "Open" button: report_dir is a path on the
        # *agent's* host filesystem, which in the general federated case
        # (see AGENTS.md's Launcher Agent section) isn't the machine this
        # app is running on -- QDesktopServices.openUrl() on it would try
        # to open a local path that may not exist here at all. Showing it
        # as selectable/copyable text is honest about what's actually
        # reachable from a remote client; browse it however this host is
        # otherwise reached (ssh, a shared mount, ...).
        layout.addWidget(QLabel("Report directory (selected test run, relative to boat-platform/ on that host):"))
        report_row = QHBoxLayout()
        self.test_run_report_view = QLineEdit()
        self.test_run_report_view.setReadOnly(True)
        self.test_run_report_view.setPlaceholderText("Select a test run to see where its reports are written")
        report_row.addWidget(self.test_run_report_view, 1)
        copy_report_btn = QPushButton("Copy")
        copy_report_btn.clicked.connect(self._copy_test_run_report_dir)
        report_row.addWidget(copy_report_btn)
        layout.addLayout(report_row)

        return tab

    def _build_interfaces_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # ── Interface table ──
        self.iface_table = QTableWidget(0, 7)
        self.iface_table.setHorizontalHeaderLabels(
            ["Host", "Name", "Type", "CAN Config", "Up", "Operstate", "MAC"]
        )
        self.iface_table.horizontalHeader().setStretchLastSection(True)
        self.iface_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.iface_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.iface_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.iface_table.itemSelectionChanged.connect(self.on_iface_selection_changed)
        layout.addWidget(self.iface_table, 1)

        # ── Actions ──
        actions = QHBoxLayout()
        new_vcan_btn = QPushButton("New vcan…")
        new_vcan_btn.clicked.connect(self.new_vcan)
        new_veth_btn = QPushButton("New veth…")
        new_veth_btn.clicked.connect(self.new_veth)
        can_config_btn = QPushButton("Configure CAN…")
        can_config_btn.clicked.connect(self.configure_can_selected)
        up_btn = _mark(QPushButton("Up"), "primary")
        up_btn.clicked.connect(self.interface_up_selected)
        down_btn = _mark(QPushButton("Down"), "danger")
        down_btn.clicked.connect(self.interface_down_selected)
        delete_btn = _mark(QPushButton("Delete"), "danger")
        delete_btn.clicked.connect(self.delete_interface_selected)
        for b in (new_vcan_btn, new_veth_btn, can_config_btn, up_btn, down_btn, delete_btn):
            actions.addWidget(b)
        actions.addStretch(1)
        layout.addLayout(actions)

        note = QLabel(
            "Delete only applies to vcan/veth -- the interfaces this tool can "
            "create. Up/Down and Configure CAN act on any interface, "
            "including physical hardware -- double-check the selection and "
            "host before using them, especially on a box with a gateway "
            "actively using a real CAN interface."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(note)

        return tab

    def _build_settings_tab(self) -> QWidget:
        """Empty for now, by request -- host management (Add/Remove Host,
        Save/Load Session) moved back to the Gateway page (see
        _build_gateways_tab()), reverting the earlier move that pulled it
        out onto its own page."""
        return QWidget()

    def closeEvent(self, event) -> None:
        self.worker.stop()
        self.worker.wait(2000)
        super().closeEvent(event)

    # ── Hosts ────────────────────────────────────────────────────────────

    def refresh_host_list(self) -> None:
        self.host_list.clear()
        for h in self.host_store.list():
            self.host_list.addItem(f"○ {h['name']} — {h['url']}")

    def add_host(self) -> None:
        name, ok = QInputDialog.getText(self, "Add Host", "Display name:")
        if not ok or not name.strip():
            return
        url, ok = QInputDialog.getText(self, "Add Host", "Agent URL (e.g. agn-testcomputer:8090):")
        if not ok or not url.strip():
            return
        try:
            self.host_store.add(name.strip(), url.strip())
        except ValueError as e:
            QMessageBox.warning(self, "Add Host", str(e))
            return
        self.refresh_host_list()

    def remove_host(self) -> None:
        idx = self.host_list.currentRow()
        if idx < 0:
            QMessageBox.information(self, "Remove Host", "Select a host in the list first.")
            return
        host = self.host_store.list()[idx]
        self.host_store.remove(host["url"])
        self.refresh_host_list()

    # ── Session save/load ───────────────────────────────────────────────

    def save_session(self) -> None:
        """Snapshots the current hosts + their agent-managed instance,
        node, and test-run *definitions* (not externally-discovered
        instances -- see session.py) to a YAML file, docker-compose-style."""
        path, _ = QFileDialog.getSaveFileName(self, "Save Session", "session.yaml", "YAML files (*.yaml *.yml)")
        if not path:
            return
        try:
            session.save_session(path, self.host_store.list(), self._snapshot,
                                  self._node_snapshot, self._test_run_snapshot)
        except OSError as e:
            QMessageBox.warning(self, "Save Session", f"Failed to write file: {e}")
            return
        QMessageBox.information(self, "Save Session", f"Saved to {path}")

    def load_session(self) -> None:
        """Adds every host in the file (skipping ones already present) and
        re-creates every saved instance/node/test-run definition, left
        **stopped** -- review the tables and Start what you want. A recipe
        replay, not a resume: each loaded instance/node/test run gets a
        fresh id, not the one it had when saved."""
        path, _ = QFileDialog.getOpenFileName(self, "Load Session", "", "YAML files (*.yaml *.yml)")
        if not path:
            return
        try:
            hosts_to_add, instances_created, nodes_created, test_runs_created, errors = session.load_session(path)
        except (OSError, yaml.YAMLError) as e:
            QMessageBox.warning(self, "Load Session", f"Failed to read file: {e}")
            return
        added = 0
        for h in hosts_to_add:
            try:
                self.host_store.add(h["name"], h["url"])
                added += 1
            except ValueError:
                pass  # already present -- fine, reuse the existing entry
        self.refresh_host_list()
        msg = (f"Session loaded: {added} new host(s) added, {instances_created} "
               f"instance(s), {nodes_created} node(s), and {test_runs_created} "
               f"test run(s) created (stopped -- start them from the tables).")
        if errors:
            msg += "\n\nSome items failed:\n" + "\n".join(errors)
            QMessageBox.warning(self, "Load Session", msg)
        else:
            QMessageBox.information(self, "Load Session", msg)

    # ── Poll callbacks ───────────────────────────────────────────────────

    def on_snapshot(self, snapshot: dict) -> None:
        self._snapshot = snapshot
        self.rebuild_table()
        self.host_list.clear()
        for h in self.host_store.list():
            data = snapshot.get(h["url"])
            dot = "●" if data and data["ok"] else "○"
            self.host_list.addItem(f"{dot} {h['name']} — {h['url']}")

    def rebuild_table(self) -> None:
        rows = []
        for host_url, data in self._snapshot.items():
            for inst in data.get("instances", []):
                rows.append((host_url, data["name"], inst))

        self.table.blockSignals(True)
        self.table.setRowCount(len(rows))
        select_row = None
        for r, (host_url, host_name, inst) in enumerate(rows):
            key = (host_url, inst["id"])
            if self._selected == key:
                select_row = r
            uptime = f"{inst['uptime_sec']:.0f}s" if inst.get("uptime_sec") is not None else "—"
            values = [
                host_name, inst["name"], inst["id"], str(inst["grpc_port"]),
                inst["status"], str(inst["pid"] or "—"),
                "Yes" if inst.get("managed", True) else "No",
                _format_interfaces(inst), _format_plugins(inst), uptime,
            ]
            for c, v in enumerate(values):
                item = QTableWidgetItem(v)
                item.setData(Qt.UserRole, key)
                if c == 4:  # Status
                    color = _process_status_color(v)
                elif c == 6:  # Managed
                    color = _bool_color(v)
                else:
                    color = None
                if color:
                    item.setForeground(color)
                self.table.setItem(r, c, item)
        if select_row is not None:
            self.table.selectRow(select_row)
        elif self._selected is not None:
            # The previously-selected instance no longer appears in this
            # snapshot (stopped-and-vanished external row, deleted, etc).
            # Signals are blocked here, so Qt's own selection model was
            # never told anything changed -- left alone, it would keep
            # whatever row index was highlighted before, now showing
            # completely different data at that index while self._selected
            # still pointed at the old, no-longer-existent id. That's a
            # real correctness hazard: an action button reads self._selected,
            # not what's visually highlighted, so it could act on a stale id
            # that (in a bigger table) might by coincidence still resolve to
            # a *different*, still-live instance. Explicitly clear both the
            # visual selection and the tracked id so nothing stale survives
            # a rebuild.
            self.table.clearSelection()
            self._selected = None
            self.log_view.clear()
        self.table.blockSignals(False)
        # Interfaces/Plugins can be the widest cells (multiple entries,
        # iface annotations) -- size every column to its actual content
        # instead of the default even split, which truncated them.
        self.table.resizeColumnsToContents()
        # Recompute in case the selected instance's own config changed
        # (e.g. just edited) even though the selection itself didn't.
        self._update_command_line()

    def find_instance(self, host_url: str, instance_id: str) -> Optional[dict]:
        data = self._snapshot.get(host_url)
        if not data:
            return None
        for inst in data.get("instances", []):
            if inst["id"] == instance_id:
                return inst
        return None

    def on_selection_changed(self) -> None:
        items = self.table.selectedItems()
        if not items:
            self._selected = None
            self._update_command_line()
            return
        key = items[0].data(Qt.UserRole)
        if key != self._selected:
            self.log_view.clear()
        self._selected = key
        self._update_command_line()

    def _update_command_line(self) -> None:
        if not self._selected:
            self.cmdline_view.clear()
            return
        host_url, inst_id = self._selected
        inst = self.find_instance(host_url, inst_id)
        self.cmdline_view.setText(_format_command_line(inst) if inst else "")

    def _copy_command_line(self) -> None:
        text = self.cmdline_view.text()
        if text:
            QApplication.clipboard().setText(text)

    def on_log(self, instance_id: str, log_lines: list) -> None:
        if not self._selected or self._selected[1] != instance_id:
            return
        self.log_view.setPlainText("\n".join(f"{l['ts']}  {l['text']}" for l in log_lines))
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ── Instance actions ─────────────────────────────────────────────────

    def _selected_client_and_id(self):
        if not self._selected:
            QMessageBox.information(self, "No selection", "Select an instance in the table first.")
            return None
        host_url, inst_id = self._selected
        return AgentClient(host_url), inst_id

    @staticmethod
    def _is_external(inst_id: str) -> bool:
        return inst_id.startswith("external:")

    def _warn_if_external(self, inst_id: str, action: str) -> bool:
        """Discovered-but-not-agent-managed rows (see the "Managed" column)
        only support Stop -- a plain signal by pid, which works regardless
        of who spawned the process. Everything else needs the agent's own
        stored definition, which these rows don't have. Returns True (and
        shows a message) if the action should be aborted."""
        if self._is_external(inst_id):
            QMessageBox.information(
                self, "Not managed",
                f"This gateway wasn't started by this agent (see the Managed "
                f"column), so it can't be {action} here. Stop still works.",
            )
            return True
        return False

    def start_selected(self) -> None:
        res = self._selected_client_and_id()
        if not res:
            return
        client, inst_id = res
        if self._warn_if_external(inst_id, "started"):
            return
        try:
            client.start_instance(inst_id)
        except AgentError as e:
            QMessageBox.warning(self, "Start failed", str(e))

    def stop_selected(self) -> None:
        res = self._selected_client_and_id()
        if not res:
            return
        client, inst_id = res
        try:
            client.stop_instance(inst_id)
        except AgentError as e:
            QMessageBox.warning(self, "Stop failed", str(e))

    def delete_selected(self) -> None:
        res = self._selected_client_and_id()
        if not res:
            return
        client, inst_id = res
        if self._warn_if_external(inst_id, "deleted"):
            return
        if QMessageBox.question(self, "Delete", "Delete this instance definition?") != QMessageBox.Yes:
            return
        try:
            client.delete_instance(inst_id)
        except AgentError as e:
            QMessageBox.warning(self, "Delete failed", str(e))

    def new_instance(self) -> None:
        hosts = self.host_store.list()
        if not hosts:
            QMessageBox.information(self, "New Instance", "Add a host first.")
            return
        dlg = NewInstanceDialog(hosts, self)
        if dlg.exec() != QDialog.Accepted:
            return
        try:
            payload = dlg.result_payload()
        except ValueError as e:
            QMessageBox.warning(self, "New Instance", str(e))
            return
        client = AgentClient(dlg.selected_host_url())
        try:
            client.create_instance(**payload)
        except AgentError as e:
            QMessageBox.warning(self, "Create failed", str(e))

    def edit_selected(self) -> None:
        if not self._selected:
            QMessageBox.information(self, "No selection", "Select an instance in the table first.")
            return
        host_url, inst_id = self._selected
        if self._warn_if_external(inst_id, "edited"):
            return
        inst = self.find_instance(host_url, inst_id)
        if inst is None:
            QMessageBox.warning(self, "Edit", "Instance not found (it may have just been removed).")
            return
        hosts = self.host_store.list()
        dlg = NewInstanceDialog(hosts, self, existing=inst, existing_host_url=host_url)
        if dlg.exec() != QDialog.Accepted:
            return
        payload = dlg.result_payload()
        client = AgentClient(host_url)
        try:
            client.update_instance(inst_id, **payload)
        except AgentError as e:
            # Most commonly the agent's 409 if the instance was started in
            # the gap between opening this dialog and clicking OK.
            QMessageBox.warning(self, "Edit failed", str(e))

    # ── Node poll callbacks ──────────────────────────────────────────────

    def on_node_snapshot(self, snapshot: dict) -> None:
        self._node_snapshot = snapshot
        self.rebuild_node_table()

    def rebuild_node_table(self) -> None:
        rows = []
        for host_url, data in self._node_snapshot.items():
            for node in data.get("nodes", []):
                rows.append((host_url, data["name"], node))

        self.node_table.blockSignals(True)
        self.node_table.setRowCount(len(rows))
        select_row = None
        for r, (host_url, host_name, node) in enumerate(rows):
            key = (host_url, node["id"])
            if self._selected_node == key:
                select_row = r
            uptime = f"{node['uptime_sec']:.0f}s" if node.get("uptime_sec") is not None else "—"
            values = [
                host_name, node["name"], node["id"], _format_node_script(node),
                node.get("target_host") or "—", node["status"], str(node["pid"] or "—"),
                _format_node_args(node), uptime,
            ]
            for c, v in enumerate(values):
                item = QTableWidgetItem(v)
                item.setData(Qt.UserRole, key)
                if c == 5:  # Status
                    color = _process_status_color(v)
                    if color:
                        item.setForeground(color)
                self.node_table.setItem(r, c, item)
        if select_row is not None:
            self.node_table.selectRow(select_row)
        elif self._selected_node is not None:
            # Same stale-selection hazard as rebuild_table() for the
            # gateway table (see its comment) -- clear both the visual
            # selection and the tracked id when the previously-selected
            # node no longer appears in this snapshot, rather than letting
            # a leftover row index silently point at different node's data.
            self.node_table.clearSelection()
            self._selected_node = None
            self.node_log_view.clear()
        self.node_table.blockSignals(False)
        self.node_table.resizeColumnsToContents()
        self._update_node_command_line()

    def find_node(self, host_url: str, node_id: str) -> Optional[dict]:
        data = self._node_snapshot.get(host_url)
        if not data:
            return None
        for node in data.get("nodes", []):
            if node["id"] == node_id:
                return node
        return None

    def on_node_selection_changed(self) -> None:
        items = self.node_table.selectedItems()
        if not items:
            self._selected_node = None
            self._update_node_command_line()
            return
        key = items[0].data(Qt.UserRole)
        if key != self._selected_node:
            self.node_log_view.clear()
        self._selected_node = key
        self._update_node_command_line()

    def _update_node_command_line(self) -> None:
        if not self._selected_node:
            self.node_cmdline_view.clear()
            return
        host_url, node_id = self._selected_node
        node = self.find_node(host_url, node_id)
        self.node_cmdline_view.setText(_format_node_command_line(node) if node else "")

    def _copy_node_command_line(self) -> None:
        text = self.node_cmdline_view.text()
        if text:
            QApplication.clipboard().setText(text)

    def on_node_log(self, node_id: str, log_lines: list) -> None:
        if not self._selected_node or self._selected_node[1] != node_id:
            return
        self.node_log_view.setPlainText("\n".join(f"{l['ts']}  {l['text']}" for l in log_lines))
        sb = self.node_log_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ── Node actions ─────────────────────────────────────────────────────

    def _selected_node_client_and_id(self):
        if not self._selected_node:
            QMessageBox.information(self, "No selection", "Select a node in the table first.")
            return None
        host_url, node_id = self._selected_node
        return AgentClient(host_url), node_id

    def start_node_selected(self) -> None:
        res = self._selected_node_client_and_id()
        if not res:
            return
        client, node_id = res
        try:
            client.start_node(node_id)
        except AgentError as e:
            QMessageBox.warning(self, "Start failed", str(e))

    def stop_node_selected(self) -> None:
        res = self._selected_node_client_and_id()
        if not res:
            return
        client, node_id = res
        try:
            client.stop_node(node_id)
        except AgentError as e:
            QMessageBox.warning(self, "Stop failed", str(e))

    def delete_node_selected(self) -> None:
        res = self._selected_node_client_and_id()
        if not res:
            return
        client, node_id = res
        if QMessageBox.question(self, "Delete", "Delete this node definition?") != QMessageBox.Yes:
            return
        try:
            client.delete_node(node_id)
        except AgentError as e:
            QMessageBox.warning(self, "Delete failed", str(e))

    def new_node(self) -> None:
        hosts = self.host_store.list()
        if not hosts:
            QMessageBox.information(self, "New Node", "Add a host first.")
            return
        dlg = NewNodeDialog(hosts, self)
        if dlg.exec() != QDialog.Accepted:
            return
        try:
            payload = dlg.result_payload()
        except ValueError as e:
            QMessageBox.warning(self, "New Node", str(e))
            return
        client = AgentClient(dlg.selected_host_url())
        try:
            client.create_node(**payload)
        except AgentError as e:
            QMessageBox.warning(self, "Create failed", str(e))

    def edit_node_selected(self) -> None:
        if not self._selected_node:
            QMessageBox.information(self, "No selection", "Select a node in the table first.")
            return
        host_url, node_id = self._selected_node
        node = self.find_node(host_url, node_id)
        if node is None:
            QMessageBox.warning(self, "Edit", "Node not found (it may have just been removed).")
            return
        hosts = self.host_store.list()
        dlg = NewNodeDialog(hosts, self, existing=node, existing_host_url=host_url)
        if dlg.exec() != QDialog.Accepted:
            return
        try:
            payload = dlg.result_payload()
        except ValueError as e:
            QMessageBox.warning(self, "Edit", str(e))
            return
        client = AgentClient(host_url)
        try:
            client.update_node(node_id, **payload)
        except AgentError as e:
            QMessageBox.warning(self, "Edit failed", str(e))

    # ── Test runs ────────────────────────────────────────────────────────

    def on_test_run_snapshot(self, snapshot: dict) -> None:
        self._test_run_snapshot = snapshot
        self.rebuild_test_run_table()

    def rebuild_test_run_table(self) -> None:
        rows = []
        for host_url, data in self._test_run_snapshot.items():
            for run in data.get("runs", []):
                rows.append((host_url, data["name"], run))

        self.test_run_table.blockSignals(True)
        self.test_run_table.setRowCount(len(rows))
        select_row = None
        for r, (host_url, host_name, run) in enumerate(rows):
            key = (host_url, run["id"])
            if self._selected_test_run == key:
                select_row = r
            uptime = f"{run['uptime_sec']:.0f}s" if run.get("uptime_sec") is not None else "—"
            values = [
                host_name, run["name"], run["id"], _format_test_run_manifest(run),
                _format_test_run_env(run), _format_test_run_result(run), run["status"],
                str(run["pid"] or "—"), uptime,
            ]
            for c, v in enumerate(values):
                item = QTableWidgetItem(v)
                item.setData(Qt.UserRole, key)
                if c == 5:  # Result
                    color = _VERDICT_COLORS.get(v)
                elif c == 6:  # Status
                    color = _process_status_color(v)
                else:
                    color = None
                if color:
                    item.setForeground(color)
                self.test_run_table.setItem(r, c, item)
        if select_row is not None:
            self.test_run_table.selectRow(select_row)
        elif self._selected_test_run is not None:
            # Same stale-selection hazard as rebuild_table()/rebuild_node_table()
            # -- clear both the visual selection and the tracked id when the
            # previously-selected run no longer appears in this snapshot.
            self.test_run_table.clearSelection()
            self._selected_test_run = None
            self.test_run_log_view.clear()
        self.test_run_table.blockSignals(False)
        self.test_run_table.resizeColumnsToContents()
        self._update_test_run_report_dir()

    def find_test_run(self, host_url: str, run_id: str) -> Optional[dict]:
        data = self._test_run_snapshot.get(host_url)
        if not data:
            return None
        for run in data.get("runs", []):
            if run["id"] == run_id:
                return run
        return None

    def on_test_run_selection_changed(self) -> None:
        items = self.test_run_table.selectedItems()
        if not items:
            self._selected_test_run = None
            self._update_test_run_report_dir()
            return
        key = items[0].data(Qt.UserRole)
        if key != self._selected_test_run:
            self.test_run_log_view.clear()
        self._selected_test_run = key
        self._update_test_run_report_dir()

    def _update_test_run_report_dir(self) -> None:
        if not self._selected_test_run:
            self.test_run_report_view.clear()
            return
        host_url, run_id = self._selected_test_run
        run = self.find_test_run(host_url, run_id)
        self.test_run_report_view.setText((run or {}).get("report_dir", ""))

    def _copy_test_run_report_dir(self) -> None:
        text = self.test_run_report_view.text()
        if text:
            QApplication.clipboard().setText(text)

    def on_test_run_log(self, run_id: str, log_lines: list) -> None:
        if not self._selected_test_run or self._selected_test_run[1] != run_id:
            return
        self.test_run_log_view.setPlainText("\n".join(f"{l['ts']}  {l['text']}" for l in log_lines))
        sb = self.test_run_log_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ── Test run actions ─────────────────────────────────────────────────

    def _selected_test_run_client_and_id(self):
        if not self._selected_test_run:
            QMessageBox.information(self, "No selection", "Select a test run in the table first.")
            return None
        host_url, run_id = self._selected_test_run
        return AgentClient(host_url), run_id

    def start_test_run_selected(self) -> None:
        res = self._selected_test_run_client_and_id()
        if not res:
            return
        client, run_id = res
        try:
            client.start_test_run(run_id)
        except AgentError as e:
            QMessageBox.warning(self, "Start failed", str(e))

    def stop_test_run_selected(self) -> None:
        res = self._selected_test_run_client_and_id()
        if not res:
            return
        client, run_id = res
        try:
            client.stop_test_run(run_id)
        except AgentError as e:
            QMessageBox.warning(self, "Stop failed", str(e))

    def delete_test_run_selected(self) -> None:
        res = self._selected_test_run_client_and_id()
        if not res:
            return
        client, run_id = res
        if QMessageBox.question(self, "Delete", "Delete this test run definition?") != QMessageBox.Yes:
            return
        try:
            client.delete_test_run(run_id)
        except AgentError as e:
            QMessageBox.warning(self, "Delete failed", str(e))

    def view_test_run_report_selected(self) -> None:
        res = self._selected_test_run_client_and_id()
        if not res:
            return
        client, run_id = res
        host_url, _ = self._selected_test_run
        run = self.find_test_run(host_url, run_id)
        run_name = (run or {}).get("name", "")
        dlg = TestReportDialog(client, run_id, run_name, self)
        dlg.exec()

    def new_test_run(self) -> None:
        hosts = self.host_store.list()
        if not hosts:
            QMessageBox.information(self, "New Test Run", "Add a host first.")
            return
        dlg = NewTestRunDialog(hosts, self)
        if dlg.exec() != QDialog.Accepted:
            return
        try:
            payload = dlg.result_payload()
        except ValueError as e:
            QMessageBox.warning(self, "New Test Run", str(e))
            return
        client = AgentClient(dlg.selected_host_url())
        try:
            client.create_test_run(**payload)
        except AgentError as e:
            QMessageBox.warning(self, "Create failed", str(e))

    def edit_test_run_selected(self) -> None:
        if not self._selected_test_run:
            QMessageBox.information(self, "No selection", "Select a test run in the table first.")
            return
        host_url, run_id = self._selected_test_run
        run = self.find_test_run(host_url, run_id)
        if run is None:
            QMessageBox.warning(self, "Edit", "Test run not found (it may have just been removed).")
            return
        hosts = self.host_store.list()
        dlg = NewTestRunDialog(hosts, self, existing=run, existing_host_url=host_url)
        if dlg.exec() != QDialog.Accepted:
            return
        try:
            payload = dlg.result_payload()
        except ValueError as e:
            QMessageBox.warning(self, "Edit", str(e))
            return
        client = AgentClient(host_url)
        try:
            client.update_test_run(run_id, **payload)
        except AgentError as e:
            QMessageBox.warning(self, "Edit failed", str(e))

    # ── Interfaces ────────────────────────────────────────────────────────

    def on_interfaces_snapshot(self, snapshot: dict) -> None:
        self._iface_snapshot = snapshot
        self.rebuild_iface_table()

    def rebuild_iface_table(self) -> None:
        rows = []
        for host_url, data in self._iface_snapshot.items():
            for iface in data.get("interfaces", []):
                rows.append((host_url, data["name"], iface))

        self.iface_table.blockSignals(True)
        self.iface_table.setRowCount(len(rows))
        select_row = None
        for r, (host_url, host_name, iface) in enumerate(rows):
            key = (host_url, iface["name"])
            if self._selected_iface == key:
                select_row = r
            values = [
                host_name, iface["name"], iface.get("type", "?"),
                _format_can_config_cell(iface),
                "up" if iface.get("up") else "down",
                iface.get("operstate", "?"), iface.get("mac", ""),
            ]
            for c, v in enumerate(values):
                item = QTableWidgetItem(v)
                item.setData(Qt.UserRole, key)
                if c == 4:  # Up
                    color = _STATUS_GOOD if v == "up" else _STATUS_MUTED
                    item.setForeground(color)
                elif c == 3:  # CAN Config
                    tooltip = _format_can_config_tooltip(iface)
                    if tooltip:
                        item.setToolTip(tooltip)
                self.iface_table.setItem(r, c, item)
        if select_row is not None:
            self.iface_table.selectRow(select_row)
        elif self._selected_iface is not None:
            # Same stale-selection hazard as rebuild_table()/rebuild_node_table()/
            # rebuild_test_run_table() -- clear both the visual selection and
            # the tracked key when the previously-selected interface no
            # longer appears in this snapshot (e.g. it was just deleted).
            self.iface_table.clearSelection()
            self._selected_iface = None
        self.iface_table.blockSignals(False)
        self.iface_table.resizeColumnsToContents()

    def find_interface(self, host_url: str, name: str) -> Optional[dict]:
        data = self._iface_snapshot.get(host_url)
        if not data:
            return None
        for iface in data.get("interfaces", []):
            if iface["name"] == name:
                return iface
        return None

    def on_iface_selection_changed(self) -> None:
        items = self.iface_table.selectedItems()
        self._selected_iface = items[0].data(Qt.UserRole) if items else None

    # ── Interface actions ────────────────────────────────────────────────

    def _selected_iface_client_and_name(self):
        if not self._selected_iface:
            QMessageBox.information(self, "No selection", "Select an interface in the table first.")
            return None
        host_url, name = self._selected_iface
        return AgentClient(host_url), name

    def new_vcan(self) -> None:
        hosts = self.host_store.list()
        if not hosts:
            QMessageBox.information(self, "New vcan", "Add a host first.")
            return
        dlg = NewInterfaceDialog("vcan", hosts, self)
        if dlg.exec() != QDialog.Accepted:
            return
        try:
            name = dlg.result_name()
        except ValueError as e:
            QMessageBox.warning(self, "New vcan", str(e))
            return
        client = AgentClient(dlg.selected_host_url())
        try:
            client.create_vcan(name)
        except AgentError as e:
            QMessageBox.warning(self, "Create failed", str(e))

    def new_veth(self) -> None:
        hosts = self.host_store.list()
        if not hosts:
            QMessageBox.information(self, "New veth", "Add a host first.")
            return
        dlg = NewInterfaceDialog("veth", hosts, self)
        if dlg.exec() != QDialog.Accepted:
            return
        try:
            name = dlg.result_name()
        except ValueError as e:
            QMessageBox.warning(self, "New veth", str(e))
            return
        client = AgentClient(dlg.selected_host_url())
        try:
            client.create_veth(name)
        except AgentError as e:
            QMessageBox.warning(self, "Create failed", str(e))

    def interface_up_selected(self) -> None:
        res = self._selected_iface_client_and_name()
        if not res:
            return
        client, name = res
        try:
            client.interface_up(name)
        except AgentError as e:
            QMessageBox.warning(self, "Up failed", str(e))

    def interface_down_selected(self) -> None:
        res = self._selected_iface_client_and_name()
        if not res:
            return
        client, name = res
        if QMessageBox.question(
            self, "Bring interface down",
            f"Bring '{name}' down? If this is a physical interface in "
            f"active use (e.g. by a running gateway), this will disrupt it.",
        ) != QMessageBox.Yes:
            return
        try:
            client.interface_down(name)
        except AgentError as e:
            QMessageBox.warning(self, "Down failed", str(e))

    def configure_can_selected(self) -> None:
        res = self._selected_iface_client_and_name()
        if not res:
            return
        client, name = res
        host_url, _ = self._selected_iface
        iface = self.find_interface(host_url, name)
        kind = (iface or {}).get("type")
        if kind == "vcan":
            # vcan genuinely has no bitrate/CAN FD to configure -- the
            # kernel has nothing to set (the same "RTNETLINK answers:
            # Operation not supported" a POST here used to surface
            # confusingly). Refuse client-side with a clear message
            # instead of opening a dialog pre-filled with fixed defaults
            # that look like real config but aren't -- this was reported
            # directly: "a virtual can shall not show any [baudrate], as
            # it does now when clicking on configure can."
            QMessageBox.information(
                self, "Configure CAN",
                f"'{name}' is a virtual CAN interface -- it has no real "
                f"bitrate or CAN FD configuration to set.",
            )
            return
        if kind != "can":
            QMessageBox.information(
                self, "Configure CAN",
                f"'{name}' is a {kind or 'unknown'} interface, not CAN -- nothing to configure here.",
            )
            return
        host_name = self._iface_snapshot.get(host_url, {}).get("name", host_url)
        current = client.get_can_config(name)  # None if unreadable -- dialog falls back to defaults
        dlg = CanConfigDialog(host_name, name, current, self)
        if dlg.exec() != QDialog.Accepted:
            return
        try:
            bitrate, dbitrate, fd = dlg.result_payload()
        except ValueError as e:
            QMessageBox.warning(self, "Configure CAN", str(e))
            return
        try:
            client.configure_can(name, bitrate, dbitrate, fd)
        except AgentError as e:
            QMessageBox.warning(self, "Configure failed", str(e))

    def delete_interface_selected(self) -> None:
        res = self._selected_iface_client_and_name()
        if not res:
            return
        client, name = res
        host_url, _ = self._selected_iface
        iface = self.find_interface(host_url, name)
        kind = (iface or {}).get("type")
        if kind not in ("vcan", "veth"):
            QMessageBox.information(
                self, "Delete",
                f"'{name}' is a {kind or 'unknown'} interface -- only vcan/veth "
                f"interfaces created by this tool can be deleted here.",
            )
            return
        if QMessageBox.question(self, "Delete", f"Delete {kind} interface '{name}'?") != QMessageBox.Yes:
            return
        try:
            if kind == "vcan":
                client.delete_vcan(name)
            else:
                client.delete_veth(name)
        except AgentError as e:
            QMessageBox.warning(self, "Delete failed", str(e))


def main() -> None:
    app = QApplication(sys.argv)
    app.setStyleSheet(_DARK_STYLESHEET)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
