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

import yaml

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
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
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from agent_client import AgentClient, AgentError
from host_store import HostStore
import session

_POLL_INTERVAL_SEC = 2.0


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
    the Nodes tab."""
    parts = []
    if node.get("target_host"):
        parts.append(f"BOAT_HOST={node['target_host']}")
    parts.append(f"python3 {node.get('script_path', '')}")
    if node.get("extra_args"):
        parts.append(" ".join(node["extra_args"]))
    return " ".join(parts)


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
    """Background thread: polls every configured host's /api/instances and
    /api/nodes (and the selected instance's/node's log, if any) on a fixed
    interval, emitting results back to the UI thread via signals."""

    snapshot_ready = Signal(dict)        # {host_url: {"name":..., "ok":bool, "instances":[...], "error":str|None}}
    log_ready = Signal(str, list)        # (instance_id, log_lines)
    node_snapshot_ready = Signal(dict)   # {host_url: {"name":..., "ok":bool, "nodes":[...], "error":str|None}}
    node_log_ready = Signal(str, list)   # (node_id, log_lines)

    def __init__(self, get_hosts, get_selected, get_selected_node,
                 interval: float = _POLL_INTERVAL_SEC, parent=None):
        super().__init__(parent)
        self._get_hosts = get_hosts
        self._get_selected = get_selected
        self._get_selected_node = get_selected_node
        self._interval = interval
        self._running = True

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        while self._running:
            snapshot = {}
            node_snapshot = {}
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
            if self._running:
                self.snapshot_ready.emit(snapshot)
                self.node_snapshot_ready.emit(node_snapshot)

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
    structured (not re-parsed from display text)."""

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

        self.config_edit = QLineEdit()
        self.config_edit.setPlaceholderText('optional config, e.g. {"iface": "vcan0"}')
        layout.addWidget(self.config_edit)

        self.list_widget = QListWidget()
        self.list_widget.setFixedHeight(70)
        layout.addWidget(self.list_widget)
        remove_btn = QPushButton("Remove selected")
        remove_btn.clicked.connect(self.remove_selected)
        layout.addWidget(remove_btn)

    def set_choices(self, paths: list) -> None:
        current = self.combo.currentText()
        self.combo.clear()
        self.combo.addItems(paths)
        self.combo.setCurrentText(current)

    def add_current(self) -> None:
        path = self.combo.currentText().strip()
        if not path:
            return
        cfg_text = self.config_edit.text().strip()
        cfg = {}
        if cfg_text:
            try:
                cfg = json.loads(cfg_text)
            except json.JSONDecodeError as e:
                QMessageBox.warning(self, "Invalid plugin config", f"Not valid JSON: {e}")
                return
        self.add_value(path, cfg)
        self.combo.setCurrentText("")
        self.config_edit.clear()

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

        self.gw_bin_edit = QLineEdit()
        self.gw_bin_edit.setPlaceholderText("default build/debug path on that host")
        layout.addRow("Gateway binary:", self.gw_bin_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
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
        return {
            "name": self.name_edit.text().strip(),
            "can_ifaces": self.can_picker.values(),
            "eth_ifaces": self.eth_picker.values(),
            "node_plugins": self.plugin_picker.values(),
            "grpc_port": int(port_text) if port_text else None,
            "gateway_bin": self.gw_bin_edit.text().strip() or None,
        }


class NewNodeDialog(QDialog):
    """New/Edit dialog for a node instance -- same doubles-as-Edit pattern as
    NewInstanceDialog. The Script combo is populated from the selected
    host's GET /api/node-scripts (boat-platform/nodes/*.py) and shows each
    script's module docstring below it; Extra Args is a free-text field
    parsed with shlex.split() on submit, since node scripts have wildly
    different CLI flags from each other (unlike gateway plugin configs,
    there's no single structured shape to build a picker around)."""

    def __init__(self, hosts: list, parent=None, existing: Optional[dict] = None,
                 existing_host_url: Optional[str] = None):
        super().__init__(parent)
        editing = existing is not None
        self.setWindowTitle("Edit Node" if editing else "New Node")
        self.resize(520, 320)
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

        self.script_combo = QComboBox()
        layout.addRow("Script:", self.script_combo)

        self.script_doc_label = QLabel("")
        self.script_doc_label.setWordWrap(True)
        self.script_doc_label.setStyleSheet("color: gray; font-size: 11px;")
        layout.addRow("", self.script_doc_label)

        self.target_host_edit = QLineEdit(existing.get("target_host", "") if editing else "")
        self.target_host_edit.setPlaceholderText(
            "e.g. localhost:50051 -- which gateway this node talks to (sets BOAT_HOST)"
        )
        layout.addRow("Target host:", self.target_host_edit)

        self.extra_args_edit = QLineEdit(
            " ".join(existing.get("extra_args", [])) if editing else ""
        )
        self.extra_args_edit.setPlaceholderText(
            '--iface vcan0 --can-id 0x300 --data AABBCCDD --cycle-ms 500'
        )
        layout.addRow("Extra args:", self.extra_args_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

        self.host_combo.currentIndexChanged.connect(self._reload_scripts)
        self.script_combo.currentIndexChanged.connect(self._update_doc_label)
        self._reload_scripts()

        if editing:
            idx = self.script_combo.findData(existing.get("script_path", ""))
            if idx >= 0:
                self.script_combo.setCurrentIndex(idx)
            self._update_doc_label()

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
        if current:
            idx = self.script_combo.findData(current)
            if idx >= 0:
                self.script_combo.setCurrentIndex(idx)
        self._update_doc_label()

    def _update_doc_label(self) -> None:
        idx = self.script_combo.currentIndex()
        doc = self.script_combo.itemData(idx, Qt.UserRole + 1) if idx >= 0 else None
        self.script_doc_label.setText(doc or "")

    def result_payload(self) -> dict:
        script_path = self.script_combo.currentData()
        if not script_path:
            raise ValueError("select a node script")
        try:
            extra_args = shlex.split(self.extra_args_edit.text().strip())
        except ValueError as e:
            raise ValueError(f"invalid extra args: {e}") from e
        return {
            "name": self.name_edit.text().strip(),
            "script_path": script_path,
            "target_host": self.target_host_edit.text().strip(),
            "extra_args": extra_args,
        }


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("BoAt Admin")
        self.resize(1100, 700)

        self.host_store = HostStore()
        self._snapshot: dict = {}
        self._selected: Optional[Tuple[str, str]] = None  # (host_url, instance_id)
        self._node_snapshot: dict = {}
        self._selected_node: Optional[Tuple[str, str]] = None  # (host_url, node_id)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # ── Hosts (shared across both tabs -- one agent per host manages
        # both gateway instances and node instances there) ──
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
        root.addLayout(host_bar)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_gateways_tab(), "Gateways")
        self.tabs.addTab(self._build_nodes_tab(), "Nodes")
        root.addWidget(self.tabs, 1)

        self.statusBar()
        self.refresh_host_list()

        self.worker = PollWorker(self.host_store.list, lambda: self._selected, lambda: self._selected_node)
        self.worker.snapshot_ready.connect(self.on_snapshot)
        self.worker.log_ready.connect(self.on_log)
        self.worker.node_snapshot_ready.connect(self.on_node_snapshot)
        self.worker.node_log_ready.connect(self.on_node_log)
        self.worker.start()

    def _build_gateways_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

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
        start_btn = QPushButton("Start")
        start_btn.clicked.connect(self.start_selected)
        stop_btn = QPushButton("Stop")
        stop_btn.clicked.connect(self.stop_selected)
        delete_btn = QPushButton("Delete")
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
        start_btn = QPushButton("Start")
        start_btn.clicked.connect(self.start_node_selected)
        stop_btn = QPushButton("Stop")
        stop_btn.clicked.connect(self.stop_node_selected)
        delete_btn = QPushButton("Delete")
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
        """Snapshots the current hosts + their agent-managed instance
        *definitions* (not externally-discovered ones -- see session.py) to
        a YAML file, docker-compose-style."""
        path, _ = QFileDialog.getSaveFileName(self, "Save Session", "session.yaml", "YAML files (*.yaml *.yml)")
        if not path:
            return
        try:
            session.save_session(path, self.host_store.list(), self._snapshot)
        except OSError as e:
            QMessageBox.warning(self, "Save Session", f"Failed to write file: {e}")
            return
        QMessageBox.information(self, "Save Session", f"Saved to {path}")

    def load_session(self) -> None:
        """Adds every host in the file (skipping ones already present) and
        re-creates every saved instance definition, left **stopped** --
        review the table and Start what you want. A recipe replay, not a
        resume: each loaded instance gets a fresh id, not the one it had
        when saved."""
        path, _ = QFileDialog.getOpenFileName(self, "Load Session", "", "YAML files (*.yaml *.yml)")
        if not path:
            return
        try:
            hosts_to_add, created_count, errors = session.load_session(path)
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
        msg = f"Session loaded: {added} new host(s) added, {created_count} instance(s) created (stopped -- start them from the table)."
        if errors:
            msg += "\n\nSome instances failed:\n" + "\n".join(errors)
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


def main() -> None:
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
