"""HTTP client for ui/launcher_agent.py's REST API.

Deliberately has no Qt dependency -- it's exercised/tested headlessly, and
main.py's PollWorker runs it on a background QThread.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests


class AgentError(Exception):
    """Raised for any failed call to a launcher agent: a network/connection
    error, or a non-2xx response (message taken from the agent's JSON
    {"detail": ...} body when present)."""


# start/stop spawn or tear down a whole boat_gateway process (SIGTERM,
# then up to a 5s wait, then SIGKILL as a fallback -- see
# ui/launcher_agent.py's GatewayInstance.stop()). A client-side timeout at
# or below that 5s wait is a real, previously-hit bug: the server-side call
# can legitimately take just over 5s and still succeed, but a <=5s client
# timeout reads that as a failure -- a false "Stop failed" even though the
# gateway did stop. Give lifecycle calls real headroom over that worst case.
_LIFECYCLE_TIMEOUT = 15.0


class AgentClient:
    def __init__(self, base_url: str, timeout: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, method: str, path: str, timeout: Optional[float] = None, **kwargs) -> Any:
        url = f"{self.base_url}{path}"
        try:
            resp = requests.request(method, url, timeout=timeout or self.timeout, **kwargs)
        except requests.RequestException as e:
            raise AgentError(f"{method} {url}: {e}") from e
        if not resp.ok:
            detail = resp.text
            try:
                detail = resp.json().get("detail", detail)
            except ValueError:
                pass
            raise AgentError(f"{method} {url} -> {resp.status_code}: {detail}")
        return resp.json() if resp.content else None

    def health(self) -> dict:
        return self._request("GET", "/api/health")

    def host_info(self) -> dict:
        return self._request("GET", "/api/host/info")

    def list_instances(self) -> List[dict]:
        return self._request("GET", "/api/instances")["instances"]

    def get_instance(self, instance_id: str) -> dict:
        return self._request("GET", f"/api/instances/{instance_id}")

    def create_instance(
        self,
        name: str = "",
        can_ifaces: Optional[List[str]] = None,
        eth_ifaces: Optional[List[str]] = None,
        node_plugins: Optional[List[Dict[str, Any]]] = None,
        grpc_port: Optional[int] = None,
        tick_ms: Optional[int] = None,
        tick_us: Optional[int] = None,
        gateway_bin: Optional[str] = None,
    ) -> dict:
        body = {
            "name": name,
            "can_ifaces": can_ifaces or [],
            "eth_ifaces": eth_ifaces or [],
            "node_plugins": node_plugins or [],
            "grpc_port": grpc_port,
            "tick_ms": tick_ms,
            "tick_us": tick_us,
            "gateway_bin": gateway_bin,
        }
        return self._request("POST", "/api/instances", json=body)

    def update_instance(
        self,
        instance_id: str,
        name: str = "",
        can_ifaces: Optional[List[str]] = None,
        eth_ifaces: Optional[List[str]] = None,
        node_plugins: Optional[List[Dict[str, Any]]] = None,
        grpc_port: Optional[int] = None,
        tick_ms: Optional[int] = None,
        tick_us: Optional[int] = None,
        gateway_bin: Optional[str] = None,
    ) -> dict:
        """Edit a stopped instance's definition in place -- refused (409) by
        the agent while it's running."""
        body = {
            "name": name,
            "can_ifaces": can_ifaces or [],
            "eth_ifaces": eth_ifaces or [],
            "node_plugins": node_plugins or [],
            "grpc_port": grpc_port,
            "tick_ms": tick_ms,
            "tick_us": tick_us,
            "gateway_bin": gateway_bin,
        }
        return self._request("PUT", f"/api/instances/{instance_id}", json=body)

    def start_instance(self, instance_id: str) -> dict:
        return self._request("POST", f"/api/instances/{instance_id}/start", timeout=_LIFECYCLE_TIMEOUT)

    def stop_instance(self, instance_id: str) -> dict:
        return self._request("POST", f"/api/instances/{instance_id}/stop", timeout=_LIFECYCLE_TIMEOUT)

    def delete_instance(self, instance_id: str) -> None:
        self._request("DELETE", f"/api/instances/{instance_id}")

    def get_log(self, instance_id: str) -> List[dict]:
        return self._request("GET", f"/api/instances/{instance_id}/log")["log"]

    def sim_state(self, instance_id: str) -> dict:
        return self._request("GET", f"/api/instances/{instance_id}/sim-state")

    # ── Node scripts/instances ───────────────────────────────────────────
    # Same shape as the gateway-instance methods above, but for scripts
    # under boat-platform/nodes/ (see ui/launcher_agent.py's "Node
    # instances" section for why this is a separate registry server-side).

    def list_node_scripts(self) -> List[dict]:
        return self._request("GET", "/api/node-scripts")["scripts"]

    def list_nodes(self) -> List[dict]:
        return self._request("GET", "/api/nodes")["nodes"]

    def get_node(self, node_id: str) -> dict:
        return self._request("GET", f"/api/nodes/{node_id}")

    def create_node(
        self,
        script_path: str,
        name: str = "",
        target_host: str = "",
        extra_args: Optional[List[str]] = None,
    ) -> dict:
        body = {
            "name": name,
            "script_path": script_path,
            "target_host": target_host,
            "extra_args": extra_args or [],
        }
        return self._request("POST", "/api/nodes", json=body)

    def update_node(
        self,
        node_id: str,
        script_path: str,
        name: str = "",
        target_host: str = "",
        extra_args: Optional[List[str]] = None,
    ) -> dict:
        """Edit a stopped node's definition in place -- refused (409) by the
        agent while it's running."""
        body = {
            "name": name,
            "script_path": script_path,
            "target_host": target_host,
            "extra_args": extra_args or [],
        }
        return self._request("PUT", f"/api/nodes/{node_id}", json=body)

    def start_node(self, node_id: str) -> dict:
        return self._request("POST", f"/api/nodes/{node_id}/start", timeout=_LIFECYCLE_TIMEOUT)

    def stop_node(self, node_id: str) -> dict:
        return self._request("POST", f"/api/nodes/{node_id}/stop", timeout=_LIFECYCLE_TIMEOUT)

    def delete_node(self, node_id: str) -> None:
        self._request("DELETE", f"/api/nodes/{node_id}")

    def get_node_log(self, node_id: str) -> List[dict]:
        return self._request("GET", f"/api/nodes/{node_id}/log")["log"]

    # ── Test runs ─────────────────────────────────────────────────────────
    # `boat test run <manifest.json>` invocations -- the automated CI-style
    # HIL suite runner, a different thing from the manual test/*.md
    # TestSuite (see ui/launcher_agent.py's "Test runs" section). Same
    # subprocess-lifecycle shape as nodes above.

    def list_test_manifests(self) -> List[dict]:
        return self._request("GET", "/api/test-manifests")["manifests"]

    def list_test_environments(self) -> List[dict]:
        return self._request("GET", "/api/test-environments")["environments"]

    def list_test_runs(self) -> List[dict]:
        return self._request("GET", "/api/test-runs")["runs"]

    def get_test_run(self, run_id: str) -> dict:
        return self._request("GET", f"/api/test-runs/{run_id}")

    def create_test_run(
        self,
        manifest_path: str,
        name: str = "",
        env_config_path: str = "",
        extra_args: Optional[List[str]] = None,
    ) -> dict:
        body = {
            "name": name,
            "manifest_path": manifest_path,
            "env_config_path": env_config_path,
            "extra_args": extra_args or [],
        }
        return self._request("POST", "/api/test-runs", json=body)

    def update_test_run(
        self,
        run_id: str,
        manifest_path: str,
        name: str = "",
        env_config_path: str = "",
        extra_args: Optional[List[str]] = None,
    ) -> dict:
        """Edit a stopped test run's definition in place -- refused (409)
        by the agent while it's running."""
        body = {
            "name": name,
            "manifest_path": manifest_path,
            "env_config_path": env_config_path,
            "extra_args": extra_args or [],
        }
        return self._request("PUT", f"/api/test-runs/{run_id}", json=body)

    def start_test_run(self, run_id: str) -> dict:
        return self._request("POST", f"/api/test-runs/{run_id}/start", timeout=_LIFECYCLE_TIMEOUT)

    def stop_test_run(self, run_id: str) -> dict:
        return self._request("POST", f"/api/test-runs/{run_id}/stop", timeout=_LIFECYCLE_TIMEOUT)

    def delete_test_run(self, run_id: str) -> None:
        self._request("DELETE", f"/api/test-runs/{run_id}")

    def get_test_run_log(self, run_id: str) -> List[dict]:
        return self._request("GET", f"/api/test-runs/{run_id}/log")["log"]

    def get_test_run_report(self, run_id: str) -> dict:
        """{"report_dir", "exists", "tests": [{"folder", "report": {...
        boat.test.report.TestReport schema...}, "has_stdout", "has_html",
        ...}, ...]} -- the agent reads report.json off its own disk and
        hands back the content directly, so this works from any client
        regardless of whether it's on the same host as the agent (see
        ui/launcher_agent.py's docstring)."""
        return self._request("GET", f"/api/test-runs/{run_id}/report")

    # ── Interfaces ────────────────────────────────────────────────────────
    # vcan/veth create+delete, generic up/down, and CAN bitrate config for
    # any type-can link (virtual or physical). No delete for anything but
    # vcan/veth -- see ui/launcher_agent.py's "Interface endpoints" section.

    def list_interfaces(self) -> List[dict]:
        return self._request("GET", "/api/interfaces")["interfaces"]

    def create_vcan(self, name: str) -> dict:
        return self._request("POST", "/api/interfaces/vcan", json={"name": name})

    def delete_vcan(self, name: str) -> None:
        self._request("DELETE", f"/api/interfaces/vcan/{name}")

    def create_veth(self, name: str) -> dict:
        return self._request("POST", "/api/interfaces/veth", json={"name": name})

    def delete_veth(self, name: str) -> None:
        self._request("DELETE", f"/api/interfaces/veth/{name}")

    def interface_up(self, name: str) -> dict:
        return self._request("POST", f"/api/interfaces/{name}/up")

    def interface_down(self, name: str) -> dict:
        return self._request("POST", f"/api/interfaces/{name}/down")

    def configure_can(self, name: str, bitrate: int,
                       dbitrate: Optional[int] = None, fd: bool = False) -> dict:
        body = {"bitrate": bitrate, "dbitrate": dbitrate, "fd": fd}
        return self._request("POST", f"/api/interfaces/{name}/can-config", json=body)
