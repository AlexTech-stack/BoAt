"""
BoAt Admin — PySide6 desktop client for one or more launcher agents.

Run:
    pip install -r admin_gui/requirements.txt
    python3 admin_gui/main.py

Talks to any number of `ui/launcher_agent.py` instances over plain HTTP (add
each as a host below) -- no SSH, this app never touches a remote machine
directly, it only calls each host's own agent API. See
backlog/launcher_agent_backlog.md and AGENTS.md's "Launcher Agent" section.

v1 scope: host list + aggregated instance table + start/stop/delete/create +
a log viewer for the selected instance. No interface-creation UI yet (the
agent doesn't expose that either -- see the backlog).
"""

from __future__ import annotations

import json
import os
import sys
from typing import Optional, Tuple

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
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
    QVBoxLayout,
    QWidget,
)

from agent_client import AgentClient, AgentError
from host_store import HostStore

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


class PollWorker(QThread):
    """Background thread: polls every configured host's /api/instances (and
    the selected instance's log, if any) on a fixed interval, emitting
    results back to the UI thread via signals."""

    snapshot_ready = Signal(dict)   # {host_url: {"name":..., "ok":bool, "instances":[...], "error":str|None}}
    log_ready = Signal(str, list)   # (instance_id, log_lines)

    def __init__(self, get_hosts, get_selected, interval: float = _POLL_INTERVAL_SEC, parent=None):
        super().__init__(parent)
        self._get_hosts = get_hosts
        self._get_selected = get_selected
        self._interval = interval
        self._running = True

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        while self._running:
            snapshot = {}
            for host in self._get_hosts():
                client = AgentClient(host["url"])
                try:
                    instances = client.list_instances()
                    snapshot[host["url"]] = {"name": host["name"], "ok": True, "instances": instances, "error": None}
                except AgentError as e:
                    snapshot[host["url"]] = {"name": host["name"], "ok": False, "instances": [], "error": str(e)}
            if self._running:
                self.snapshot_ready.emit(snapshot)

            selected = self._get_selected()
            if selected and self._running:
                host_url, inst_id = selected
                try:
                    log = AgentClient(host_url).get_log(inst_id)
                    self.log_ready.emit(inst_id, log)
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
        self.list_widget.addItem(text)
        self.combo.setCurrentText("")

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
        label = os.path.basename(path) or path
        if cfg:
            label += f"  {cfg_text}"
        item = QListWidgetItem(label)
        item.setData(Qt.UserRole, {"path": path, "config": cfg})
        self.list_widget.addItem(item)
        self.combo.setCurrentText("")
        self.config_edit.clear()

    def remove_selected(self) -> None:
        for item in self.list_widget.selectedItems():
            self.list_widget.takeItem(self.list_widget.row(item))

    def values(self) -> list:
        return [self.list_widget.item(i).data(Qt.UserRole) for i in range(self.list_widget.count())]


class NewInstanceDialog(QDialog):
    def __init__(self, hosts: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New Gateway Instance")
        self.resize(560, 600)
        layout = QFormLayout(self)

        self.host_combo = QComboBox()
        for h in hosts:
            self.host_combo.addItem(f"{h['name']} ({h['url']})", h["url"])
        layout.addRow("Host:", self.host_combo)

        self.name_edit = QLineEdit()
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

    def selected_host_url(self) -> str:
        return self.host_combo.currentData()

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


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("BoAt Admin")
        self.resize(1100, 700)

        self.host_store = HostStore()
        self._snapshot: dict = {}
        self._selected: Optional[Tuple[str, str]] = None  # (host_url, instance_id)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # ── Hosts ──
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
        host_btns.addWidget(add_host_btn)
        host_btns.addWidget(remove_host_btn)
        host_bar.addLayout(host_btns)
        root.addLayout(host_bar)

        # ── Instance table ──
        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(
            ["Host", "Name", "ID", "Port", "Status", "PID", "Interfaces", "Plugins", "Uptime"]
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self.on_selection_changed)
        root.addWidget(self.table, 2)

        # ── Actions ──
        actions = QHBoxLayout()
        new_btn = QPushButton("New Instance…")
        new_btn.clicked.connect(self.new_instance)
        start_btn = QPushButton("Start")
        start_btn.clicked.connect(self.start_selected)
        stop_btn = QPushButton("Stop")
        stop_btn.clicked.connect(self.stop_selected)
        delete_btn = QPushButton("Delete")
        delete_btn.clicked.connect(self.delete_selected)
        for b in (new_btn, start_btn, stop_btn, delete_btn):
            actions.addWidget(b)
        actions.addStretch(1)
        root.addLayout(actions)

        # ── Log viewer ──
        root.addWidget(QLabel("Log (selected instance):"))
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        root.addWidget(self.log_view, 1)

        self.statusBar()
        self.refresh_host_list()

        self.worker = PollWorker(self.host_store.list, lambda: self._selected)
        self.worker.snapshot_ready.connect(self.on_snapshot)
        self.worker.log_ready.connect(self.on_log)
        self.worker.start()

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
                _format_interfaces(inst), _format_plugins(inst), uptime,
            ]
            for c, v in enumerate(values):
                item = QTableWidgetItem(v)
                item.setData(Qt.UserRole, key)
                self.table.setItem(r, c, item)
        if select_row is not None:
            self.table.selectRow(select_row)
        self.table.blockSignals(False)
        # Interfaces/Plugins can be the widest cells (multiple entries,
        # iface annotations) -- size every column to its actual content
        # instead of the default even split, which truncated them.
        self.table.resizeColumnsToContents()

    def on_selection_changed(self) -> None:
        items = self.table.selectedItems()
        if not items:
            self._selected = None
            return
        key = items[0].data(Qt.UserRole)
        if key != self._selected:
            self.log_view.clear()
        self._selected = key

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

    def start_selected(self) -> None:
        res = self._selected_client_and_id()
        if not res:
            return
        client, inst_id = res
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


def main() -> None:
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
