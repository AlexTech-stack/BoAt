"""
BoAt Platform — Launcher Agent

Multi-instance gateway lifecycle API for one host. Runs once per machine that
hosts `boat_gateway` instances; a separate admin client (the planned PySide6
app, or curl/anything else) talks to one or more of these agents over the
network to start/stop/inspect gateways -- no SSH involved, the agent only
ever touches processes on its own host.

This is deliberately a *separate* service from `ui/launcher.py` (which stays
as-is: a single-instance browser tool with its own PID-file guard). The
agent's job is the multi-instance case: several BOAT_GRPC_PORT-distinct
gateways, each with its own CAN/Ethernet interfaces and BOAT_NODE_PLUGINS set,
tracked and controlled from one place.

Also manages **node** processes -- scripts under `boat-platform/nodes/`
(FrameNode-based senders/responders/simulated ECUs, see AGENTS.md) -- as a
separate registry (`/api/nodes`, `/api/node-scripts`) alongside the gateway
instance one, since a node has no port to allocate or ifaces/plugins of its
own; it just needs a target gateway (BOAT_HOST) and its own CLI args.

Also manages **test runs** -- `boat test run <manifest.json>` invocations
(the automated CI-style HIL suite runner, `boat_cli/test.py` +
`sdk/python/boat/test/`; not the manual `test/*.md` TestSuite, which is
hand-verified and never touched by this agent) -- as a third registry
(`/api/test-runs`, `/api/test-manifests`, `/api/test-environments`), same
subprocess-lifecycle shape as a node: `boat test run` already owns its own
gateway lifecycle (per whichever environment config it's pointed at) and
its own report generation, so this agent just runs the command and tails
its log, same as it does for a node. `GET /api/test-runs/{id}/report`
additionally reads back the per-test `report.json` files a finished run
wrote under its `report_dir` and returns their content directly -- so a
remote client can render pass/fail results without needing filesystem
access to *this* host (report_dir is a path on the agent's own
filesystem, same "not necessarily where the client runs" situation as
everything else in this file).

Also manages **network interfaces** on this host -- create/delete vcan and
veth pairs, bring any interface up/down, and configure a `type can` link's
bitrate (virtual or physical -- the exact `ip link set ... type can
bitrate ...` commands documented in `boat_cli/bus_setup_context.py`'s
"Physical CAN" section). Requires passwordless sudo for `ip`/`modprobe`,
same prerequisite `ui/launcher.py`'s own equivalent endpoints already
document -- either tool works against the same host, they shell out to
the same commands. Deliberately no delete for anything but vcan/veth: a
real network device isn't something this agent should be able to remove,
only reconfigure or toggle up/down.

Usage:
    python3 ui/launcher_agent.py
    # REST API on http://0.0.0.0:8090

Environment:
    BOAT_AGENT_PORT       — HTTP port for this agent (default 8090)
    BOAT_AGENT_BASE_PORT  — first gRPC port to try when auto-allocating
                            (default 50051, matches boat_gateway's own default)
    BOAT_GATEWAY_BIN      — path to boat_gateway binary (default build/debug)
    BOAT_CLI_BIN          — path to the `boat` console script, for test runs
                            (default: discovered via PATH, then ~/.local/bin/boat)

Requires passwordless sudo for `modprobe vcan` and `ip link add/del/set`
(interface management endpoints only -- everything else needs no
elevated privileges).

v1 scope: in-memory instance registry only (an agent restart forgets
definitions of stopped instances; running processes are unaffected but
become unmanaged). Extend as real needs surface.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "boat-platform" / "sdk" / "python"))

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# ── Configuration ────────────────────────────────────────────────────────────

_PROJECT_ROOT   = Path(__file__).resolve().parent.parent / "boat-platform"
_DEFAULT_GW_BIN = str(_PROJECT_ROOT / "build" / "debug" / "src" / "gateway" / "grpc_gateway" / "boat_gateway")
_GW_BIN_DEFAULT = os.environ.get("BOAT_GATEWAY_BIN", _DEFAULT_GW_BIN)
_AGENT_PORT     = int(os.environ.get("BOAT_AGENT_PORT", "8090"))
_BASE_PORT      = int(os.environ.get("BOAT_AGENT_BASE_PORT", "50051"))
_LOG_LINES      = 500
_NODES_DIR      = _PROJECT_ROOT / "nodes"
_TESTS_DIR      = _PROJECT_ROOT / "config" / "tests"
_REPORTS_DIR    = _PROJECT_ROOT / "reports" / "admin_gui"

_SIM_STATE_NAMES = {0: "UNSPECIFIED", 1: "IDLE", 2: "RUNNING", 3: "PAUSED", 4: "STOPPED", 5: "ERROR"}


# ── Port allocation ──────────────────────────────────────────────────────────

def _port_is_free(port: int) -> bool:
    """Plain bind probe -- same technique boat_gateway's own RefuseIfPortInUse
    uses, so 'free according to this agent' matches 'free according to the
    gateway it's about to spawn'."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False


def _allocate_port(preferred: Optional[int], reserved: set) -> int:
    if preferred is not None:
        if preferred in reserved:
            raise ValueError(f"port {preferred} is already assigned to another tracked instance")
        return preferred
    port = _BASE_PORT
    while port in reserved or not _port_is_free(port):
        port += 1
        if port > 65535:
            raise RuntimeError("no free gRPC port found")
    return port


# ── Gateway instance ─────────────────────────────────────────────────────────

@dataclass
class GatewayInstance:
    id: str
    name: str
    gateway_bin: str
    can_ifaces: List[str] = field(default_factory=list)
    eth_ifaces: List[str] = field(default_factory=list)
    node_plugins: List[dict] = field(default_factory=list)  # [{"path": ..., "config": {...}}]
    grpc_port: int = 50051
    tick_ms: Optional[int] = None
    tick_us: Optional[int] = None
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    exit_code: Optional[int] = None
    process: Optional[subprocess.Popen] = field(default=None, repr=False)
    _log: deque = field(default_factory=lambda: deque(maxlen=_LOG_LINES), repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    _log_thread: Optional[threading.Thread] = field(default=None, repr=False)

    def append_log(self, line: str) -> None:
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        with self._lock:
            self._log.append({"ts": ts, "text": line.rstrip()})

    def get_log(self) -> List[dict]:
        with self._lock:
            return list(self._log)

    @property
    def running(self) -> bool:
        if self.process is None:
            return False
        return self.process.poll() is None

    @property
    def status(self) -> str:
        if self.process is None:
            return "stopped"
        code = self.process.poll()
        if code is None:
            return "running"
        return "stopped" if code == 0 else f"exited:{code}"

    def pid(self) -> Optional[int]:
        if self.running and self.process is not None:
            return self.process.pid
        return None

    def uptime(self) -> Optional[float]:
        if self.started_at is None or not self.running:
            return None
        return time.time() - self.started_at

    def build_env(self) -> dict:
        env = os.environ.copy()
        if self.can_ifaces:
            env["BOAT_CAN_INTERFACES"] = ",".join(self.can_ifaces)
        if self.eth_ifaces:
            env["BOAT_ETH_INTERFACES"] = ",".join(self.eth_ifaces)
        env["BOAT_GRPC_PORT"] = str(self.grpc_port)
        if self.node_plugins:
            parts = []
            for p in self.node_plugins:
                path = p["path"]
                cfg = p.get("config") or {}
                parts.append(f"{path}?{json.dumps(cfg)}" if cfg else path)
            env["BOAT_NODE_PLUGINS"] = ",".join(parts)
        if self.tick_ms:
            env["BOAT_NODE_TICK_MS"] = str(self.tick_ms)
        if self.tick_us:
            env["BOAT_NODE_TICK_US"] = str(self.tick_us)
        return env

    def start(self) -> None:
        with self._lock:
            if self.running:
                raise RuntimeError(f"instance '{self.id}' is already running (PID {self.pid()})")
            if not os.path.isfile(self.gateway_bin):
                raise FileNotFoundError(f"gateway binary not found: {self.gateway_bin}")
            env = self.build_env()
            self.exit_code = None
            self.started_at = time.time()
            self.process = subprocess.Popen(
                [self.gateway_bin],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=env,
                text=True,
                bufsize=1,
            )
            self.append_log(f"[agent] started PID {self.process.pid} on port {self.grpc_port}")
            self._log_thread = threading.Thread(target=self._drain_output, daemon=True, name=f"gw-log-{self.id}")
            self._log_thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        with self._lock:
            if not self.running or self.process is None:
                return
            self.append_log("[agent] sending SIGTERM…")
            self.process.terminate()
            try:
                self.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.append_log("[agent] timeout — sending SIGKILL")
                self.process.kill()
                self.process.wait()
            self.exit_code = self.process.returncode
            self.append_log(f"[agent] exited with code {self.exit_code}")
            self.process = None

    def _drain_output(self) -> None:
        assert self.process is not None
        assert self.process.stdout is not None
        try:
            for line in self.process.stdout:
                self.append_log(line)
        except ValueError:
            pass
        with self._lock:
            if self.process:
                self.exit_code = self.process.wait()
                self.process = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "gateway_bin": self.gateway_bin,
            "can_ifaces": self.can_ifaces,
            "eth_ifaces": self.eth_ifaces,
            "node_plugins": self.node_plugins,
            "grpc_port": self.grpc_port,
            "tick_ms": self.tick_ms,
            "tick_us": self.tick_us,
            "status": self.status,
            "pid": self.pid(),
            "uptime_sec": self.uptime(),
            "exit_code": self.exit_code,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "managed": True,
        }


# ── Registry ──────────────────────────────────────────────────────────────────

class InstanceRegistry:
    def __init__(self) -> None:
        self._instances: Dict[str, GatewayInstance] = {}
        self._lock = threading.RLock()

    def create(
        self,
        name: str,
        can_ifaces: List[str],
        eth_ifaces: List[str],
        node_plugins: List[dict],
        grpc_port: Optional[int],
        tick_ms: Optional[int],
        tick_us: Optional[int],
        gateway_bin: Optional[str],
    ) -> GatewayInstance:
        with self._lock:
            reserved = {inst.grpc_port for inst in self._instances.values()}
            port = _allocate_port(grpc_port, reserved)
            inst_id = uuid.uuid4().hex[:8]
            inst = GatewayInstance(
                id=inst_id,
                name=name or inst_id,
                gateway_bin=gateway_bin or _GW_BIN_DEFAULT,
                can_ifaces=list(can_ifaces or []),
                eth_ifaces=list(eth_ifaces or []),
                node_plugins=list(node_plugins or []),
                grpc_port=port,
                tick_ms=tick_ms,
                tick_us=tick_us,
            )
            self._instances[inst_id] = inst
            return inst

    def get(self, instance_id: str) -> GatewayInstance:
        with self._lock:
            inst = self._instances.get(instance_id)
            if inst is None:
                raise KeyError(instance_id)
            return inst

    def list(self) -> List[GatewayInstance]:
        with self._lock:
            return list(self._instances.values())

    def delete(self, instance_id: str) -> None:
        with self._lock:
            inst = self.get(instance_id)
            if inst.running:
                raise RuntimeError(f"instance '{instance_id}' is running; stop it first")
            del self._instances[instance_id]

    def update(
        self,
        instance_id: str,
        name: str,
        can_ifaces: List[str],
        eth_ifaces: List[str],
        node_plugins: List[dict],
        grpc_port: Optional[int],
        tick_ms: Optional[int],
        tick_us: Optional[int],
        gateway_bin: Optional[str],
    ) -> GatewayInstance:
        """Edit a stopped instance's definition in place -- same id, same
        semantics as CanTp's re-run-configure-to-edit pattern: refused while
        running (see ICanTp / CanTpServiceImpl for the precedent this
        follows). grpc_port re-runs through _allocate_port with the
        instance's *own current port* excluded from the collision set, so
        submitting the same port back (the common case -- the Edit dialog
        pre-fills it) is never mistaken for a conflict with itself."""
        with self._lock:
            inst = self.get(instance_id)
            if inst.running:
                raise RuntimeError(f"instance '{instance_id}' is running; stop it first")
            reserved = {i.grpc_port for i in self._instances.values() if i.id != instance_id}
            port = _allocate_port(grpc_port, reserved)
            inst.name = name or inst.name
            inst.can_ifaces = list(can_ifaces or [])
            inst.eth_ifaces = list(eth_ifaces or [])
            inst.node_plugins = list(node_plugins or [])
            inst.grpc_port = port
            inst.tick_ms = tick_ms
            inst.tick_us = tick_us
            inst.gateway_bin = gateway_bin or inst.gateway_bin
            return inst


_registry = InstanceRegistry()


# ── Node instances ───────────────────────────────────────────────────────────
#
# A "node" here is a script under boat-platform/nodes/ (see AGENTS.md) -- an
# SDK-driven process (FrameNode-based sender/responder/simulated ECU), not a
# boat_gateway. Deliberately a separate registry from GatewayInstance/
# InstanceRegistry above rather than a generalization of it: the domains
# genuinely differ (a node has no port to allocate, no CAN/Eth ifaces or
# plugins of its own -- it has a target gateway to talk to, via BOAT_HOST, and
# arbitrary script-specific CLI args). Same subprocess-lifecycle shape though.

@dataclass
class NodeInstance:
    id: str
    name: str
    script_path: str
    target_host: str = ""              # BOAT_HOST value set in the child's env
    extra_args: List[str] = field(default_factory=list)  # appended to the command line as-is
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    exit_code: Optional[int] = None
    process: Optional[subprocess.Popen] = field(default=None, repr=False)
    _log: deque = field(default_factory=lambda: deque(maxlen=_LOG_LINES), repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    _log_thread: Optional[threading.Thread] = field(default=None, repr=False)

    def append_log(self, line: str) -> None:
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        with self._lock:
            self._log.append({"ts": ts, "text": line.rstrip()})

    def get_log(self) -> List[dict]:
        with self._lock:
            return list(self._log)

    @property
    def running(self) -> bool:
        if self.process is None:
            return False
        return self.process.poll() is None

    @property
    def status(self) -> str:
        if self.process is None:
            return "stopped"
        code = self.process.poll()
        if code is None:
            return "running"
        return "stopped" if code == 0 else f"exited:{code}"

    def pid(self) -> Optional[int]:
        if self.running and self.process is not None:
            return self.process.pid
        return None

    def uptime(self) -> Optional[float]:
        if self.started_at is None or not self.running:
            return None
        return time.time() - self.started_at

    def start(self) -> None:
        with self._lock:
            if self.running:
                raise RuntimeError(f"node '{self.id}' is already running (PID {self.pid()})")
            if not os.path.isfile(self.script_path):
                raise FileNotFoundError(f"node script not found: {self.script_path}")
            env = os.environ.copy()
            if self.target_host:
                env["BOAT_HOST"] = self.target_host
            # CPython fully block-buffers stdout (unlike stderr, which is
            # always unbuffered) whenever it isn't a tty -- which a piped
            # subprocess never is. Without this, a node's ordinary print()
            # output sits invisibly in the child's own libc buffer (~8KB)
            # until it fills or the process exits; bufsize=1 below only
            # controls how *this* process reads the pipe, it has no effect
            # on how the *child* fills it. Every node script's routine log
            # line was silently subject to this -- only their stderr
            # warnings (e.g. the retry/backoff messages added for gateway-
            # restart resilience) were ever actually showing up promptly.
            env["PYTHONUNBUFFERED"] = "1"
            self.exit_code = None
            self.started_at = time.time()
            cmd = [sys.executable, self.script_path] + list(self.extra_args)
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=env,
                text=True,
                bufsize=1,
            )
            self.append_log(f"[agent] started PID {self.process.pid} "
                             f"(BOAT_HOST={self.target_host or '(unset)'})")
            self._log_thread = threading.Thread(target=self._drain_output, daemon=True, name=f"node-log-{self.id}")
            self._log_thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        with self._lock:
            if not self.running or self.process is None:
                return
            self.append_log("[agent] sending SIGTERM…")
            self.process.terminate()
            try:
                self.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.append_log("[agent] timeout — sending SIGKILL")
                self.process.kill()
                self.process.wait()
            self.exit_code = self.process.returncode
            self.append_log(f"[agent] exited with code {self.exit_code}")
            self.process = None

    def _drain_output(self) -> None:
        assert self.process is not None
        assert self.process.stdout is not None
        try:
            for line in self.process.stdout:
                self.append_log(line)
        except ValueError:
            pass
        with self._lock:
            if self.process:
                self.exit_code = self.process.wait()
                self.process = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "script_path": self.script_path,
            "target_host": self.target_host,
            "extra_args": self.extra_args,
            "status": self.status,
            "pid": self.pid(),
            "uptime_sec": self.uptime(),
            "exit_code": self.exit_code,
            "created_at": self.created_at,
            "started_at": self.started_at,
        }


class NodeRegistry:
    def __init__(self) -> None:
        self._nodes: Dict[str, NodeInstance] = {}
        self._lock = threading.RLock()

    def create(self, name: str, script_path: str, target_host: str, extra_args: List[str]) -> NodeInstance:
        with self._lock:
            node_id = uuid.uuid4().hex[:8]
            inst = NodeInstance(
                id=node_id,
                name=name or node_id,
                script_path=script_path,
                target_host=target_host or "",
                extra_args=list(extra_args or []),
            )
            self._nodes[node_id] = inst
            return inst

    def get(self, node_id: str) -> NodeInstance:
        with self._lock:
            inst = self._nodes.get(node_id)
            if inst is None:
                raise KeyError(node_id)
            return inst

    def list(self) -> List[NodeInstance]:
        with self._lock:
            return list(self._nodes.values())

    def delete(self, node_id: str) -> None:
        with self._lock:
            inst = self.get(node_id)
            if inst.running:
                raise RuntimeError(f"node '{node_id}' is running; stop it first")
            del self._nodes[node_id]

    def update(self, node_id: str, name: str, script_path: str, target_host: str, extra_args: List[str]) -> NodeInstance:
        """Edit a stopped node's definition in place -- same
        edit-refused-while-running pattern as InstanceRegistry.update()."""
        with self._lock:
            inst = self.get(node_id)
            if inst.running:
                raise RuntimeError(f"node '{node_id}' is running; stop it first")
            inst.name = name or inst.name
            inst.script_path = script_path or inst.script_path
            inst.target_host = target_host if target_host is not None else inst.target_host
            inst.extra_args = list(extra_args) if extra_args is not None else inst.extra_args
            return inst


_node_registry = NodeRegistry()


def _introspect_node_args(py: Path) -> List[dict]:
    """Imports a node script as a module -- by convention (see
    boat-platform/nodes/cyclic_can_sender.py's docstring) never running its
    main(), only calling build_parser() if the script defines one at module
    level -- and turns its argparse actions into a JSON-serializable schema
    admin_gui uses to build one input field per argument. --address is
    skipped (that's the dialog's separate Target gateway field); so is -h.

    Deliberately swallows *any* failure into an empty list: a script with
    no build_parser(), one that isn't valid Python, one whose module-level
    imports fail in this particular environment, etc. should just fall back
    to a flat free-text field client-side, not break discovery for every
    other script under boat-platform/nodes/."""
    mod_name = f"_boat_node_introspect_{py.stem}"
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(mod_name, py)
        if spec is None or spec.loader is None:
            return []
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        try:
            spec.loader.exec_module(module)
            build_parser = getattr(module, "build_parser", None)
            if not callable(build_parser):
                return []
            parser = build_parser()
        finally:
            sys.modules.pop(mod_name, None)

        actions = []
        for action in parser._actions:
            if not action.option_strings:
                continue  # positional args -- none of our scripts use these
            if "--address" in action.option_strings or "-h" in action.option_strings:
                continue
            default = action.default
            if not isinstance(default, (str, int, float, bool)) and default is not None:
                default = str(default)
            actions.append({
                "flag": action.option_strings[-1],  # prefer the long form (assumes short-then-long order)
                "help": action.help or "",
                "default": default,
                "is_flag": action.nargs == 0,  # store_true/store_false/store_const -- no value typed
            })
        return actions
    except Exception:
        return []


def _discover_node_scripts() -> List[dict]:
    """Mirrors ui/control_panel.py's node discovery: any *.py file directly
    under boat-platform/nodes/, not prefixed with '_', with its module
    docstring's first line and whether it uses input() (can't run headlessly,
    so the client can grey out Start for it the same way control_panel.py
    disables its own start button). Also carries each script's argument
    schema (see _introspect_node_args()) when discoverable."""
    out: List[dict] = []
    if not _NODES_DIR.is_dir():
        return out
    for py in sorted(_NODES_DIR.glob("*.py")):
        if py.name.startswith("_"):
            continue
        try:
            src = py.read_text(encoding="utf-8")
        except OSError:
            src = ""
        docstring = ""
        m = re.search(r'"""(.*?)"""', src, re.DOTALL)
        if m:
            lines = m.group(1).strip().splitlines()
            if lines:
                docstring = lines[0].strip()[:200]
        out.append({
            "name": py.stem,
            "path": str(py.absolute()),
            "docstring": docstring,
            "interactive": "input(" in src,
            "args": _introspect_node_args(py),
        })
    return out


# ── Test runs ─────────────────────────────────────────────────────────────────
#
# A "test run" is one invocation of `boat test run <manifest.json>` -- the
# automated, CI-style HIL suite runner (boat_cli/test.py + sdk/python/boat/
# test/). Not to be confused with this repo's manual test/*.md TestSuite
# (hand-verified, release-oriented, never touched by anything here) or the
# C++/pytest unit tests -- see AGENTS.md's "Three distinct things..." note.
# Tracked the same way as a node: a subprocess this agent spawns and tails
# the log of. `boat test run` already owns its own gateway lifecycle (per
# its --config environment) and its own report generation, so this agent's
# job is the same as it is for a node -- run the command, somewhere, and
# let a client watch it -- not to re-implement any of that.

def _discover_boat_cli() -> Optional[str]:
    """Locates the `boat` console script -- a pip-installed entry point
    (`pip install -e ./boat-platform/cli`), not something under
    build/{debug,release} like the gateway binary. shutil.which() alone
    isn't fully reliable here: an agent started from a non-interactive
    context (a service manager, a plain `python3 ui/launcher_agent.py`
    from a script) may not have ~/.local/bin on PATH even though `boat`
    is installed there -- the common case for a per-user pip install.
    BOAT_CLI_BIN overrides both, for anything this doesn't find."""
    override = os.environ.get("BOAT_CLI_BIN")
    if override and os.path.isfile(override):
        return override
    found = shutil.which("boat")
    if found:
        return found
    candidate = Path.home() / ".local" / "bin" / "boat"
    if candidate.is_file():
        return str(candidate)
    return None


_BOAT_CLI_BIN = _discover_boat_cli()


@dataclass
class TestRunInstance:
    id: str
    name: str
    manifest_path: str                 # relative to boat-platform/, e.g. "config/tests/manifest_x.json"
    env_config_path: str = ""          # "" = let the manifest's own environment_config decide
    extra_args: List[str] = field(default_factory=list)  # e.g. --stop-on-failure, --parallel 2
    report_dir: str = ""               # auto-assigned (relative to boat-platform/) on first start()
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    exit_code: Optional[int] = None
    process: Optional[subprocess.Popen] = field(default=None, repr=False)
    _log: deque = field(default_factory=lambda: deque(maxlen=_LOG_LINES), repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    _log_thread: Optional[threading.Thread] = field(default=None, repr=False)

    def append_log(self, line: str) -> None:
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        with self._lock:
            self._log.append({"ts": ts, "text": line.rstrip()})

    def get_log(self) -> List[dict]:
        with self._lock:
            return list(self._log)

    @property
    def running(self) -> bool:
        if self.process is None:
            return False
        return self.process.poll() is None

    @property
    def status(self) -> str:
        if self.process is None:
            return "stopped"
        code = self.process.poll()
        if code is None:
            return "running"
        return "stopped" if code == 0 else f"exited:{code}"

    @property
    def result(self) -> Optional[str]:
        """Friendlier than status/exit_code for a UI badge: None until
        this run has actually finished at least once, then PASS/FAIL
        matching TestSuiteRunner.run()'s own exit-code contract (0 =
        every test in the manifest passed, matching every test file's
        own subprocess-returncode convention -- see runner.py)."""
        if self.exit_code is None:
            return None
        return "PASS" if self.exit_code == 0 else "FAIL"

    def pid(self) -> Optional[int]:
        if self.running and self.process is not None:
            return self.process.pid
        return None

    def uptime(self) -> Optional[float]:
        if self.started_at is None or not self.running:
            return None
        return time.time() - self.started_at

    def start(self) -> None:
        with self._lock:
            if self.running:
                raise RuntimeError(f"test run '{self.id}' is already running (PID {self.pid()})")
            if _BOAT_CLI_BIN is None:
                raise FileNotFoundError(
                    "'boat' CLI not found on this host -- install it "
                    "(pip install -e ./boat-platform/cli) or set BOAT_CLI_BIN")
            manifest_abs = _PROJECT_ROOT / self.manifest_path
            if not manifest_abs.is_file():
                raise FileNotFoundError(f"manifest not found: {self.manifest_path}")
            if not self.report_dir:
                # Relative (like manifest_path/env_config_path) so it's
                # meaningful from either this process's cwd or the
                # subprocess's -- both are _PROJECT_ROOT, see cwd= below.
                self.report_dir = str(Path("reports") / "admin_gui" / self.id)
            cmd = [_BOAT_CLI_BIN, "test", "run", self.manifest_path,
                   "--report-dir", self.report_dir]
            if self.env_config_path:
                cmd += ["--config", self.env_config_path]
            cmd += list(self.extra_args)
            env = os.environ.copy()
            # Same reasoning as NodeInstance.start(): CPython block-buffers
            # stdout whenever it isn't a tty, which a piped subprocess
            # never is -- without this, boat test run's own progress
            # output (and everything the test files under it print) sits
            # invisibly in the child's own libc buffer until it fills or
            # the process exits.
            env["PYTHONUNBUFFERED"] = "1"
            self.exit_code = None
            self.started_at = time.time()
            self.process = subprocess.Popen(
                cmd,
                cwd=str(_PROJECT_ROOT),  # manifest/env/report paths are relative to boat-platform/
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=env,
                text=True,
                bufsize=1,
            )
            self.append_log(f"[agent] started PID {self.process.pid} ({' '.join(cmd)})")
            self._log_thread = threading.Thread(target=self._drain_output, daemon=True,
                                                 name=f"testrun-log-{self.id}")
            self._log_thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        with self._lock:
            if not self.running or self.process is None:
                return
            self.append_log("[agent] sending SIGTERM…")
            self.process.terminate()
            try:
                self.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.append_log("[agent] timeout — sending SIGKILL")
                self.process.kill()
                self.process.wait()
            self.exit_code = self.process.returncode
            self.append_log(f"[agent] exited with code {self.exit_code}")
            self.process = None

    def _drain_output(self) -> None:
        assert self.process is not None
        assert self.process.stdout is not None
        try:
            for line in self.process.stdout:
                self.append_log(line)
        except ValueError:
            pass
        with self._lock:
            if self.process:
                self.exit_code = self.process.wait()
                self.process = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "manifest_path": self.manifest_path,
            "env_config_path": self.env_config_path,
            "extra_args": self.extra_args,
            "report_dir": self.report_dir,
            "status": self.status,
            "result": self.result,
            "pid": self.pid(),
            "uptime_sec": self.uptime(),
            "exit_code": self.exit_code,
            "created_at": self.created_at,
            "started_at": self.started_at,
        }


class TestRunRegistry:
    def __init__(self) -> None:
        self._runs: Dict[str, TestRunInstance] = {}
        self._lock = threading.RLock()

    def create(self, name: str, manifest_path: str, env_config_path: str,
               extra_args: List[str]) -> TestRunInstance:
        with self._lock:
            run_id = uuid.uuid4().hex[:8]
            inst = TestRunInstance(
                id=run_id,
                name=name or run_id,
                manifest_path=manifest_path,
                env_config_path=env_config_path or "",
                extra_args=list(extra_args or []),
            )
            self._runs[run_id] = inst
            return inst

    def get(self, run_id: str) -> TestRunInstance:
        with self._lock:
            inst = self._runs.get(run_id)
            if inst is None:
                raise KeyError(run_id)
            return inst

    def list(self) -> List[TestRunInstance]:
        with self._lock:
            return list(self._runs.values())

    def delete(self, run_id: str) -> None:
        with self._lock:
            inst = self.get(run_id)
            if inst.running:
                raise RuntimeError(f"test run '{run_id}' is running; stop it first")
            del self._runs[run_id]

    def update(self, run_id: str, name: str, manifest_path: str, env_config_path: str,
               extra_args: List[str]) -> TestRunInstance:
        """Edit a stopped test run's definition in place -- same
        edit-refused-while-running pattern as NodeRegistry.update()."""
        with self._lock:
            inst = self.get(run_id)
            if inst.running:
                raise RuntimeError(f"test run '{run_id}' is running; stop it first")
            inst.name = name or inst.name
            inst.manifest_path = manifest_path or inst.manifest_path
            inst.env_config_path = env_config_path if env_config_path is not None else inst.env_config_path
            inst.extra_args = list(extra_args) if extra_args is not None else inst.extra_args
            return inst


_test_run_registry = TestRunRegistry()


def _discover_test_manifests() -> List[dict]:
    """Scans boat-platform/config/tests/ for manifest_*.json files -- the
    naming convention every manifest in this repo follows (see
    manifest_can_loopback.json). Each entry carries enough to populate a
    picker without a client needing to fetch and parse the file itself.
    Swallows any read/parse failure into skipping that file, same
    defensive reasoning as _discover_node_scripts()."""
    out: List[dict] = []
    if not _TESTS_DIR.is_dir():
        return out
    for f in sorted(_TESTS_DIR.glob("manifest_*.json")):
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append({
            "path": str(f.relative_to(_PROJECT_ROOT)).replace("\\", "/"),
            "name": doc.get("name", f.stem),
            "description": doc.get("description", ""),
            "environment_config": doc.get("environment_config", ""),
            "test_count": len(doc.get("tests", [])),
        })
    return out


def _discover_test_environments() -> List[dict]:
    """Scans boat-platform/config/tests/ for env_*.json files -- disjoint
    from manifest_*.json and *.schema.json by naming convention alone, no
    extra filtering needed."""
    out: List[dict] = []
    if not _TESTS_DIR.is_dir():
        return out
    for f in sorted(_TESTS_DIR.glob("env_*.json")):
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        gw = doc.get("gateway", {})
        buses = doc.get("buses", {})
        out.append({
            "path": str(f.relative_to(_PROJECT_ROOT)).replace("\\", "/"),
            "name": doc.get("name", f.stem),
            "description": doc.get("description", ""),
            "gateway_address": gw.get("address", ""),
            "buses": {bus_name: b.get("interface", "") for bus_name, b in buses.items()},
        })
    return out


def _read_test_run_report(report_dir: str) -> dict:
    """Reads back what TestSuiteRunner._run_single_test() wrote for a
    given run's report_dir: one subfolder per manifest test entry
    (`<timestamp>_<test_id>/`), each with its own report.json (the
    boat.test.report.TestReport schema -- verdict, steps, assertions,
    ...), report.junit.xml, report.html, stdout.txt/stderr.txt. There is
    no aggregate top-level report file (TestSuiteRunner's own summary is
    stderr-only, never persisted -- see runner.py's _print_summary()), so
    this walks the subfolders and returns each parsed report.json
    directly -- letting a client (admin_gui's report viewer) render
    pass/fail results without needing filesystem/SSH access to this
    host, the same problem the Report directory field's own "no Open
    button" comment describes for the raw path."""
    if not report_dir:
        return {"report_dir": report_dir, "exists": False, "tests": []}
    abs_dir = _PROJECT_ROOT / report_dir
    if not abs_dir.is_dir():
        return {"report_dir": report_dir, "exists": False, "tests": []}
    tests: List[dict] = []
    for entry in sorted(abs_dir.iterdir()):
        if not entry.is_dir():
            continue
        item: Dict[str, object] = {
            "folder": entry.name,
            "has_stdout": (entry / "stdout.txt").is_file(),
            "has_stderr": (entry / "stderr.txt").is_file(),
            "has_html": (entry / "report.html").is_file(),
            "has_junit": (entry / "report.junit.xml").is_file(),
        }
        report_json = entry / "report.json"
        if report_json.is_file():
            try:
                item["report"] = json.loads(report_json.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                item["error"] = f"failed to parse report.json: {e}"
        else:
            item["error"] = "no report.json in this folder yet"
        tests.append(item)
    return {"report_dir": report_dir, "exists": True, "tests": tests}


# ── Host introspection ───────────────────────────────────────────────────────

def _list_interfaces() -> List[dict]:
    """Interface listing, mirroring (and now a superset of) ui/launcher.py's
    version -- that tool's own vcan/veth create/delete endpoints are
    unaffected by this agent also having equivalents; either can be used
    against the same host, they just both shell out to the same `ip`
    commands."""
    try:
        raw = subprocess.run(["ip", "-j", "link", "show"], capture_output=True, text=True, check=True)
        all_ifaces: list = json.loads(raw.stdout)
        vcan_names: set = set()
        try:
            vraw = subprocess.run(["ip", "-j", "link", "show", "type", "vcan"], capture_output=True, text=True)
            if vraw.returncode == 0:
                for e in json.loads(vraw.stdout):
                    vcan_names.add(e["ifname"])
        except Exception:
            pass

        out = []
        for iface in all_ifaces:
            name = iface["ifname"]
            flags = iface.get("flags", [])
            link_type = iface.get("link_type", "")
            if name in vcan_names:
                iface_type = "vcan"
            elif link_type == "ether" and "veth" in name:
                iface_type = "veth"
            elif link_type == "ether":
                iface_type = "ether"
            elif link_type == "loopback":
                iface_type = "loopback"
            else:
                iface_type = link_type or "other"  # physical CAN ("can") lands here
            out.append({
                "name": name,
                "type": iface_type,
                "up": "UP" in flags,
                "lower_up": "LOWER_UP" in flags,
                "operstate": iface.get("operstate", "UNKNOWN"),
                "mac": iface.get("address", ""),
            })
        return out
    except (subprocess.CalledProcessError, json.JSONDecodeError, FileNotFoundError) as e:
        raise HTTPException(status_code=500, detail=f"Failed to list interfaces: {e}")


def _sudo_ip(args: List[str], check: bool = True) -> subprocess.CompletedProcess:
    """`sudo -n ip <args>` -- non-interactive (-n), so this fails cleanly
    with a permission error instead of hanging on a password prompt if
    passwordless sudo isn't set up for `ip`/`modprobe` on this host (same
    prerequisite ui/launcher.py's own interface endpoints already
    document)."""
    return subprocess.run(["sudo", "-n", "ip"] + args, capture_output=True, text=True, check=check)


def _ip_error_detail(e: subprocess.CalledProcessError) -> str:
    return (e.stderr or str(e)).strip()


def _discover_gateway_bins() -> List[str]:
    out = []
    for preset in ("debug", "release"):
        b = _PROJECT_ROOT / "build" / preset / "src" / "gateway" / "grpc_gateway" / "boat_gateway"
        if b.is_file():
            out.append(str(b))
    return out


def _introspect_plugin_config(so_path: Path) -> dict:
    """Reads a plugin's optional config-schema sidecar file --
    <name>.schema.json next to <name>.so, written by the plugin's own
    author and copied there at build time by add_boat_plugin()
    (cmake/BoAtPlugin.cmake) -- describing the JSON config a plugin
    accepts (the ?{...} appended to its .so path) as
    {"key": {"type", "default", "help", ...}, ...}.

    Unlike node scripts, a compiled .so has nothing to import/introspect
    at runtime the way build_parser() lets _introspect_node_args() work --
    this is a static, hand-maintained equivalent instead. Swallows any
    failure (no sidecar file, unreadable, invalid JSON) into an empty
    dict, same reasoning as _introspect_node_args(): a plugin without one
    just doesn't get per-key fields client-side, it never breaks discovery
    for every other plugin."""
    schema_path = so_path.with_suffix("").with_suffix(".schema.json")
    try:
        return json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _discover_plugins() -> List[dict]:
    out = []
    for preset in ("debug", "release"):
        plugin_dir = _PROJECT_ROOT / "build" / preset / "src" / "plugins"
        if plugin_dir.is_dir():
            for so_path in sorted(plugin_dir.glob("*/*.so")):
                out.append({
                    "path": str(so_path),
                    "config_schema": _introspect_plugin_config(so_path),
                })
    return out


# ── External (unmanaged) gateway discovery ──────────────────────────────────
#
# This agent only ever *manages* boat_gateway processes it spawned itself
# (InstanceRegistry). But "what gateways are running on this host" is a
# reasonable question regardless of who started them -- someone SSHed in and
# ran one by hand, or a previous agent process exited without stopping its
# children first (see the v1 in-memory-registry gap in the backlog). Answer
# it by scanning /proc for boat_gateway processes this registry doesn't
# already know about, and recovering their config from /proc/<pid>/environ
# -- the same BOAT_* env vars this agent itself sets when it spawns one.
# Linux-only (matches this project's deployment target); returns [] anywhere
# /proc doesn't exist rather than erroring.

def _parse_plugins_env(value: str) -> List[dict]:
    """Parse a BOAT_NODE_PLUGINS env value (path?{json},path2?{json2},...)
    into the structured [{"path", "config"}, ...] form. Splits on commas
    that are NOT inside a {...} span, since a plugin's JSON config can
    itself contain commas (multiple keys)."""
    parts = []
    depth = 0
    current: List[str] = []
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
            except json.JSONDecodeError:
                cfg = {}
            plugins.append({"path": path, "config": cfg})
        else:
            plugins.append({"path": part, "config": {}})
    return plugins


def _discover_external_gateways(known_pids: set) -> List[dict]:
    if not os.path.isdir("/proc"):
        return []
    out = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        pid = int(entry)
        if pid in known_pids:
            continue
        try:
            with open(f"/proc/{pid}/comm") as f:
                if f.read().strip() != "boat_gateway":
                    continue
            with open(f"/proc/{pid}/environ", "rb") as f:
                raw_env = f.read()
            env = {}
            for kv in raw_env.split(b"\0"):
                if b"=" in kv:
                    k, _, v = kv.partition(b"=")
                    env[k.decode(errors="replace")] = v.decode(errors="replace")
            try:
                gateway_bin = os.readlink(f"/proc/{pid}/exe")
            except OSError:
                gateway_bin = None
            try:
                # /proc/<pid>'s ctime approximates process start time on
                # Linux -- not exact, but good enough for an uptime display.
                started_at = os.stat(f"/proc/{pid}").st_ctime
            except OSError:
                started_at = None
        except (OSError, FileNotFoundError):
            continue  # exited mid-scan, or unreadable (different user's process)

        try:
            grpc_port = int(env.get("BOAT_GRPC_PORT", "50051"))
        except ValueError:
            grpc_port = 50051
        tick_ms_str = env.get("BOAT_NODE_TICK_MS", "")
        tick_us_str = env.get("BOAT_NODE_TICK_US", "")

        out.append({
            "id": f"external:{pid}",
            "name": "(unmanaged)",
            "gateway_bin": gateway_bin,
            "can_ifaces": [s for s in env.get("BOAT_CAN_INTERFACES", "").split(",") if s],
            "eth_ifaces": [s for s in env.get("BOAT_ETH_INTERFACES", "").split(",") if s],
            "node_plugins": _parse_plugins_env(env.get("BOAT_NODE_PLUGINS", "")),
            "grpc_port": grpc_port,
            "tick_ms": int(tick_ms_str) if tick_ms_str.isdigit() else None,
            "tick_us": int(tick_us_str) if tick_us_str.isdigit() else None,
            "status": "running",
            "pid": pid,
            "uptime_sec": (time.time() - started_at) if started_at else None,
            "exit_code": None,
            "created_at": started_at,
            "started_at": started_at,
            "managed": False,
        })
    return out


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(title="BoAt Launcher Agent")


class NodePluginSpec(BaseModel):
    path: str
    config: dict = {}


class CreateInstanceRequest(BaseModel):
    name: str = ""
    can_ifaces: List[str] = []
    eth_ifaces: List[str] = []
    node_plugins: List[NodePluginSpec] = []
    grpc_port: Optional[int] = None
    tick_ms: Optional[int] = None
    tick_us: Optional[int] = None
    gateway_bin: Optional[str] = None


@app.get("/api/health")
def api_health():
    return {"ok": True, "hostname": socket.gethostname(), "instance_count": len(_registry.list())}


@app.get("/api/host/info")
def api_host_info():
    return {
        "hostname": socket.gethostname(),
        "interfaces": _list_interfaces(),
        "gateway_bins": _discover_gateway_bins(),
        "plugins": _discover_plugins(),
        "boat_cli_bin": _BOAT_CLI_BIN,  # None = 'boat' not found on this host; New Test Run should grey out
    }


def _reject_if_external(instance_id: str) -> None:
    """external:<pid> ids are discovered, not created by this agent -- it
    has no stored definition for them, so Edit/Start/Delete don't make
    sense (Stop does, and is handled separately: a plain signal by pid
    works regardless of who spawned the process)."""
    if instance_id.startswith("external:"):
        raise HTTPException(
            status_code=400,
            detail="this gateway isn't managed by this agent (discovered "
                   "running, not started via this API) -- only Stop is "
                   "supported for it",
        )


@app.get("/api/instances")
def api_list_instances():
    managed = [i.to_dict() for i in _registry.list()]
    known_pids = {pid for pid in (i.pid() for i in _registry.list()) if pid is not None}
    external = _discover_external_gateways(known_pids)
    return {"instances": managed + external}


@app.post("/api/instances")
def api_create_instance(req: CreateInstanceRequest):
    try:
        inst = _registry.create(
            name=req.name,
            can_ifaces=req.can_ifaces,
            eth_ifaces=req.eth_ifaces,
            node_plugins=[p.model_dump() for p in req.node_plugins],
            grpc_port=req.grpc_port,
            tick_ms=req.tick_ms,
            tick_us=req.tick_us,
            gateway_bin=req.gateway_bin,
        )
    except (ValueError, RuntimeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return inst.to_dict()


@app.get("/api/instances/{instance_id}")
def api_get_instance(instance_id: str):
    _reject_if_external(instance_id)
    try:
        return _registry.get(instance_id).to_dict()
    except KeyError:
        raise HTTPException(status_code=404, detail="instance not found")


@app.put("/api/instances/{instance_id}")
def api_update_instance(instance_id: str, req: CreateInstanceRequest):
    _reject_if_external(instance_id)
    try:
        inst = _registry.update(
            instance_id=instance_id,
            name=req.name,
            can_ifaces=req.can_ifaces,
            eth_ifaces=req.eth_ifaces,
            node_plugins=[p.model_dump() for p in req.node_plugins],
            grpc_port=req.grpc_port,
            tick_ms=req.tick_ms,
            tick_us=req.tick_us,
            gateway_bin=req.gateway_bin,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="instance not found")
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return inst.to_dict()


@app.post("/api/instances/{instance_id}/start")
def api_start_instance(instance_id: str):
    _reject_if_external(instance_id)
    try:
        inst = _registry.get(instance_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="instance not found")
    try:
        inst.start()
    except (RuntimeError, FileNotFoundError) as e:
        raise HTTPException(status_code=409, detail=str(e))
    return inst.to_dict()


@app.post("/api/instances/{instance_id}/stop")
def api_stop_instance(instance_id: str):
    if instance_id.startswith("external:"):
        pid = int(instance_id.split(":", 1)[1])
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            raise HTTPException(status_code=404, detail="process not found (it may have already exited)")
        except PermissionError:
            raise HTTPException(status_code=403, detail=f"no permission to signal pid {pid} (owned by a different user?)")
        return {"id": instance_id, "pid": pid, "status": "stopping"}
    try:
        inst = _registry.get(instance_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="instance not found")
    inst.stop()
    return inst.to_dict()


@app.get("/api/instances/{instance_id}/log")
def api_instance_log(instance_id: str):
    if instance_id.startswith("external:"):
        return {"log": [{"ts": "", "text": "(log not captured -- this gateway "
                                            "wasn't started by this agent, so "
                                            "its stdout/stderr was never piped here)"}]}
    try:
        inst = _registry.get(instance_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="instance not found")
    return {"log": inst.get_log()}


@app.delete("/api/instances/{instance_id}")
def api_delete_instance(instance_id: str):
    _reject_if_external(instance_id)
    try:
        _registry.delete(instance_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="instance not found")
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"ok": True}


@app.get("/api/instances/{instance_id}/sim-state")
def api_instance_sim_state(instance_id: str):
    _reject_if_external(instance_id)
    try:
        inst = _registry.get(instance_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="instance not found")
    if not inst.running:
        return {"connected": False, "error": "instance not running"}
    try:
        from boat.client import BoAtClient
        from boat.v1 import simulation_pb2
        client = BoAtClient(f"localhost:{inst.grpc_port}")
        req = simulation_pb2.GetSimulationStateRequest(simulation_id="")
        resp = client.simulation.GetSimulationState(req)
        sim = resp.simulation
        return {
            "connected": True,
            "state": _SIM_STATE_NAMES.get(sim.state, "UNKNOWN"),
            "state_code": sim.state,
            "tick": sim.tick,
            "simulation_id": sim.simulation_id,
            "scenario_id": sim.scenario_id,
        }
    except Exception as e:
        return {"connected": False, "error": str(e)}


# ── Node endpoints ────────────────────────────────────────────────────────────

class CreateNodeRequest(BaseModel):
    name: str = ""
    script_path: str
    target_host: str = ""
    extra_args: List[str] = []


@app.get("/api/node-scripts")
def api_list_node_scripts():
    return {"scripts": _discover_node_scripts()}


@app.get("/api/nodes")
def api_list_nodes():
    return {"nodes": [n.to_dict() for n in _node_registry.list()]}


@app.post("/api/nodes")
def api_create_node(req: CreateNodeRequest):
    n = _node_registry.create(req.name, req.script_path, req.target_host, req.extra_args)
    return n.to_dict()


@app.get("/api/nodes/{node_id}")
def api_get_node(node_id: str):
    try:
        return _node_registry.get(node_id).to_dict()
    except KeyError:
        raise HTTPException(status_code=404, detail="node not found")


@app.put("/api/nodes/{node_id}")
def api_update_node(node_id: str, req: CreateNodeRequest):
    try:
        n = _node_registry.update(node_id, req.name, req.script_path, req.target_host, req.extra_args)
    except KeyError:
        raise HTTPException(status_code=404, detail="node not found")
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return n.to_dict()


@app.post("/api/nodes/{node_id}/start")
def api_start_node(node_id: str):
    try:
        n = _node_registry.get(node_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="node not found")
    try:
        n.start()
    except (RuntimeError, FileNotFoundError) as e:
        raise HTTPException(status_code=409, detail=str(e))
    return n.to_dict()


@app.post("/api/nodes/{node_id}/stop")
def api_stop_node(node_id: str):
    try:
        n = _node_registry.get(node_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="node not found")
    n.stop()
    return n.to_dict()


@app.get("/api/nodes/{node_id}/log")
def api_node_log(node_id: str):
    try:
        n = _node_registry.get(node_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="node not found")
    return {"log": n.get_log()}


@app.delete("/api/nodes/{node_id}")
def api_delete_node(node_id: str):
    try:
        _node_registry.delete(node_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="node not found")
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"ok": True}


# ── Test run endpoints ───────────────────────────────────────────────────────

class CreateTestRunRequest(BaseModel):
    name: str = ""
    manifest_path: str
    env_config_path: str = ""
    extra_args: List[str] = []


@app.get("/api/test-manifests")
def api_list_test_manifests():
    return {"manifests": _discover_test_manifests()}


@app.get("/api/test-environments")
def api_list_test_environments():
    return {"environments": _discover_test_environments()}


@app.get("/api/test-runs")
def api_list_test_runs():
    return {"runs": [r.to_dict() for r in _test_run_registry.list()]}


@app.post("/api/test-runs")
def api_create_test_run(req: CreateTestRunRequest):
    r = _test_run_registry.create(req.name, req.manifest_path, req.env_config_path, req.extra_args)
    return r.to_dict()


@app.get("/api/test-runs/{run_id}")
def api_get_test_run(run_id: str):
    try:
        return _test_run_registry.get(run_id).to_dict()
    except KeyError:
        raise HTTPException(status_code=404, detail="test run not found")


@app.put("/api/test-runs/{run_id}")
def api_update_test_run(run_id: str, req: CreateTestRunRequest):
    try:
        r = _test_run_registry.update(run_id, req.name, req.manifest_path,
                                       req.env_config_path, req.extra_args)
    except KeyError:
        raise HTTPException(status_code=404, detail="test run not found")
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return r.to_dict()


@app.post("/api/test-runs/{run_id}/start")
def api_start_test_run(run_id: str):
    try:
        r = _test_run_registry.get(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="test run not found")
    try:
        r.start()
    except (RuntimeError, FileNotFoundError) as e:
        raise HTTPException(status_code=409, detail=str(e))
    return r.to_dict()


@app.post("/api/test-runs/{run_id}/stop")
def api_stop_test_run(run_id: str):
    try:
        r = _test_run_registry.get(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="test run not found")
    r.stop()
    return r.to_dict()


@app.get("/api/test-runs/{run_id}/log")
def api_test_run_log(run_id: str):
    try:
        r = _test_run_registry.get(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="test run not found")
    return {"log": r.get_log()}


@app.get("/api/test-runs/{run_id}/report")
def api_test_run_report(run_id: str):
    try:
        r = _test_run_registry.get(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="test run not found")
    return _read_test_run_report(r.report_dir)


@app.delete("/api/test-runs/{run_id}")
def api_delete_test_run(run_id: str):
    try:
        _test_run_registry.delete(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="test run not found")
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"ok": True}


# ── Interface endpoints ──────────────────────────────────────────────────────
# Create/configure/up/down for network interfaces on this host -- vcan/veth
# creation mirrors ui/launcher.py's own equivalent endpoints exactly (same
# `ip`/`modprobe` commands; either tool can be used against the same host).
# CAN bitrate configuration is new here -- it applies to any `type can` link
# (virtual or physical), matching the reference commands in
# boat_cli/bus_setup_context.py's "Physical CAN" section. Deliberately no
# *delete* for anything but vcan/veth: a real network device isn't something
# this tool should be able to remove, only bring up/down or reconfigure.

@app.get("/api/interfaces")
def api_list_interfaces():
    return {"interfaces": _list_interfaces()}


class CreateVcanRequest(BaseModel):
    name: str = "vcan0"


class CreateVethRequest(BaseModel):
    name: str = "veth0"


class CanConfigRequest(BaseModel):
    bitrate: int
    dbitrate: Optional[int] = None
    fd: bool = False


_MAX_IFNAME_LEN = 15  # Linux IFNAMSIZ - 1 -- a kernel-enforced hard limit on
                      # any interface name, not a convention. Checked here so
                      # a too-long name fails with a clear message instead of
                      # `ip`'s own cryptic '"name" not a valid ifname' --
                      # found by hitting exactly this with a 20-char veth
                      # peer name during this feature's own verification.


def _check_ifname(name: str) -> None:
    if not name or len(name) > _MAX_IFNAME_LEN:
        raise HTTPException(
            status_code=400,
            detail=f"'{name}' is not a valid interface name -- Linux interface "
                   f"names must be 1-{_MAX_IFNAME_LEN} characters",
        )


@app.post("/api/interfaces/vcan")
def api_create_vcan(req: CreateVcanRequest):
    _check_ifname(req.name)
    try:
        subprocess.run(["sudo", "-n", "modprobe", "vcan"], capture_output=True, check=False)
        _sudo_ip(["link", "add", req.name, "type", "vcan"])
        _sudo_ip(["link", "set", req.name, "up"])
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=400, detail=_ip_error_detail(e))
    return {"ok": True, "name": req.name}


@app.delete("/api/interfaces/vcan/{name}")
def api_delete_vcan(name: str):
    try:
        _sudo_ip(["link", "delete", name])
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=400, detail=_ip_error_detail(e))
    return {"ok": True, "name": name}


@app.post("/api/interfaces/veth")
def api_create_veth(req: CreateVethRequest):
    _check_ifname(req.name)
    peer = f"{req.name}_peer"
    if len(peer) > _MAX_IFNAME_LEN:
        raise HTTPException(
            status_code=400,
            detail=f"'{req.name}' is too long for a veth pair -- the "
                   f"auto-generated peer name '{peer}' would be {len(peer)} "
                   f"characters, over Linux's {_MAX_IFNAME_LEN}-character "
                   f"interface name limit. Use a name of "
                   f"{_MAX_IFNAME_LEN - len('_peer')} characters or fewer.",
        )
    try:
        _sudo_ip(["link", "add", req.name, "type", "veth", "peer", "name", peer])
        _sudo_ip(["link", "set", req.name, "up"])
        _sudo_ip(["link", "set", peer, "up"])
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=400, detail=_ip_error_detail(e))
    return {"ok": True, "interfaces": [req.name, peer]}


@app.delete("/api/interfaces/veth/{name}")
def api_delete_veth(name: str):
    # Deleting either end of a veth pair removes both -- ip's own behavior,
    # not something this endpoint needs to special-case.
    try:
        _sudo_ip(["link", "delete", name])
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=400, detail=_ip_error_detail(e))
    return {"ok": True, "name": name}


@app.post("/api/interfaces/{name}/up")
def api_interface_up(name: str):
    try:
        _sudo_ip(["link", "set", name, "up"])
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=400, detail=_ip_error_detail(e))
    return {"ok": True, "name": name}


@app.post("/api/interfaces/{name}/down")
def api_interface_down(name: str):
    try:
        _sudo_ip(["link", "set", name, "down"])
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=400, detail=_ip_error_detail(e))
    return {"ok": True, "name": name}


@app.post("/api/interfaces/{name}/can-config")
def api_can_config(name: str, req: CanConfigRequest):
    """`ip link set <name> up type can bitrate <b> [dbitrate <d> fd on]` --
    the exact reference commands in bus_setup_context.py's "Physical CAN"
    section, for any type-can link (real hardware or vcan, though vcan
    has no physical bitrate and the kernel will reject it -- that error
    comes back to the caller same as any other, not special-cased here).
    A bitrate change is rejected by the kernel while the link is up, so
    this brings it down first (ignoring failure -- already-down is fine,
    the up+bitrate command below is what actually matters and does raise
    on failure)."""
    try:
        _sudo_ip(["link", "set", name, "down"], check=False)
        args = ["link", "set", name, "up", "type", "can", "bitrate", str(req.bitrate)]
        if req.fd:
            args += ["dbitrate", str(req.dbitrate or req.bitrate), "fd", "on"]
        _sudo_ip(args)
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=400, detail=_ip_error_detail(e))
    return {"ok": True, "name": name, "bitrate": req.bitrate, "dbitrate": req.dbitrate, "fd": req.fd}


if __name__ == "__main__":
    print(f"BoAt Launcher Agent → http://0.0.0.0:{_AGENT_PORT}")
    print(f"Default gateway binary: {_GW_BIN_DEFAULT}")
    print(f"Base gRPC port for auto-allocation: {_BASE_PORT}")
    uvicorn.run(app, host="0.0.0.0", port=_AGENT_PORT, log_level="warning")
