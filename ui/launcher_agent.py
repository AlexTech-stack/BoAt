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

Usage:
    python3 ui/launcher_agent.py
    # REST API on http://0.0.0.0:8090

Environment:
    BOAT_AGENT_PORT       — HTTP port for this agent (default 8090)
    BOAT_AGENT_BASE_PORT  — first gRPC port to try when auto-allocating
                            (default 50051, matches boat_gateway's own default)
    BOAT_GATEWAY_BIN      — path to boat_gateway binary (default build/debug)

v1 scope: in-memory instance registry only (an agent restart forgets
definitions of stopped instances; running processes are unaffected but
become unmanaged). Interface creation (vcan/veth) stays in ui/launcher.py
for now -- this agent only lists what's on the host, for populating a
client's dropdowns. Extend both as real needs surface.
"""

from __future__ import annotations

import json
import os
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

sys.path.insert(0, str(Path(__file__).parent.parent / "boat-platform" / "sdk" / "python"))

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# ── Configuration ────────────────────────────────────────────────────────────

_PROJECT_ROOT   = Path(__file__).parent.parent / "boat-platform"
_DEFAULT_GW_BIN = str(_PROJECT_ROOT / "build" / "debug" / "src" / "gateway" / "grpc_gateway" / "boat_gateway")
_GW_BIN_DEFAULT = os.environ.get("BOAT_GATEWAY_BIN", _DEFAULT_GW_BIN)
_AGENT_PORT     = int(os.environ.get("BOAT_AGENT_PORT", "8090"))
_BASE_PORT      = int(os.environ.get("BOAT_AGENT_BASE_PORT", "50051"))
_LOG_LINES      = 500

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


# ── Host introspection ───────────────────────────────────────────────────────

def _list_interfaces() -> List[dict]:
    """Read-only interface listing, mirroring ui/launcher.py's version.
    Interface *creation* stays in launcher.py for now -- this is purely for
    populating a client's CAN/Eth dropdowns."""
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
                iface_type = link_type or "other"
            out.append({
                "name": name,
                "type": iface_type,
                "up": "UP" in iface.get("flags", []),
                "mac": iface.get("address", ""),
            })
        return out
    except (subprocess.CalledProcessError, json.JSONDecodeError, FileNotFoundError) as e:
        raise HTTPException(status_code=500, detail=f"Failed to list interfaces: {e}")


def _discover_gateway_bins() -> List[str]:
    out = []
    for preset in ("debug", "release"):
        b = _PROJECT_ROOT / "build" / preset / "src" / "gateway" / "grpc_gateway" / "boat_gateway"
        if b.is_file():
            out.append(str(b))
    return out


def _discover_plugins() -> List[str]:
    out = []
    for preset in ("debug", "release"):
        plugin_dir = _PROJECT_ROOT / "build" / preset / "src" / "plugins"
        if plugin_dir.is_dir():
            out.extend(str(p) for p in sorted(plugin_dir.glob("*/*.so")))
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
    }


@app.get("/api/instances")
def api_list_instances():
    return {"instances": [i.to_dict() for i in _registry.list()]}


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
    try:
        return _registry.get(instance_id).to_dict()
    except KeyError:
        raise HTTPException(status_code=404, detail="instance not found")


@app.put("/api/instances/{instance_id}")
def api_update_instance(instance_id: str, req: CreateInstanceRequest):
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
    try:
        inst = _registry.get(instance_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="instance not found")
    inst.stop()
    return inst.to_dict()


@app.get("/api/instances/{instance_id}/log")
def api_instance_log(instance_id: str):
    try:
        inst = _registry.get(instance_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="instance not found")
    return {"log": inst.get_log()}


@app.delete("/api/instances/{instance_id}")
def api_delete_instance(instance_id: str):
    try:
        _registry.delete(instance_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="instance not found")
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"ok": True}


@app.get("/api/instances/{instance_id}/sim-state")
def api_instance_sim_state(instance_id: str):
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


if __name__ == "__main__":
    print(f"BoAt Launcher Agent → http://0.0.0.0:{_AGENT_PORT}")
    print(f"Default gateway binary: {_GW_BIN_DEFAULT}")
    print(f"Base gRPC port for auto-allocation: {_BASE_PORT}")
    uvicorn.run(app, host="0.0.0.0", port=_AGENT_PORT, log_level="warning")
