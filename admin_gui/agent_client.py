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
