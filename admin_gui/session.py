"""Save/load a "session" -- a docker-compose-style YAML file capturing
which hosts and which agent-managed instance/node *definitions* should
exist -- so a whole multi-host setup can be recreated in one action
instead of re-adding hosts and refilling New Instance/New Node forms by
hand each time.

No Qt import, so it's usable/testable headlessly; MainWindow only owns the
file dialog and result reporting.

A session is a recipe, not a live snapshot: loading it creates fresh
instances/nodes (new ids, new PIDs) from the same definitions -- it does
not "resume" the exact processes that were running when saved. Loaded
instances/nodes are left **stopped**, deliberately -- unlike
`docker-compose up`, this doesn't start them automatically; review the
tables and hit Start yourself (or Start each one from here later). Only
agent-managed (managed: true) instances are saved -- an
externally-discovered gateway has no definition this tool owns to
recreate later; every node is agent-created already, so all of them are
saved. A node's `target_host` is saved as whatever concrete address it
already resolved to (e.g. "localhost:50051" or "10.10.7.175:50051") --
same as it's stored server-side -- so it just needs that address to still
be reachable after a load, not the *other* host to still be defined in
this same session file (it can point anywhere, including a host this
session doesn't even list).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

from agent_client import AgentClient, AgentError

_INSTANCE_FIELDS = (
    "name", "can_ifaces", "eth_ifaces", "node_plugins",
    "grpc_port", "tick_ms", "tick_us", "gateway_bin",
)

_NODE_FIELDS = ("name", "script_path", "target_host", "extra_args")


def save_session(path: str, hosts: List[Dict[str, str]], snapshot: dict,
                  node_snapshot: dict) -> None:
    """hosts: HostStore.list() shape [{"name", "url"}, ...].
    snapshot / node_snapshot: the same {host_url: {"name", "instances"/
    "nodes": [...], ...}} shapes MainWindow already tracks from its
    poller (on_snapshot()/on_node_snapshot())."""
    doc: Dict[str, Any] = {"version": "1", "hosts": []}
    for h in hosts:
        entry: Dict[str, Any] = {
            "name": h["name"], "url": h["url"], "instances": [], "nodes": [],
        }
        data = snapshot.get(h["url"]) or {}
        for inst in data.get("instances", []):
            if not inst.get("managed", True):
                continue  # no owned definition to recreate later
            entry["instances"].append({k: inst.get(k) for k in _INSTANCE_FIELDS})
        node_data = node_snapshot.get(h["url"]) or {}
        for node in node_data.get("nodes", []):
            entry["nodes"].append({k: node.get(k) for k in _NODE_FIELDS})
        doc["hosts"].append(entry)
    Path(path).write_text(yaml.safe_dump(doc, sort_keys=False))


def load_session(path: str) -> Tuple[List[Dict[str, str]], int, int, List[str]]:
    """Parses the file and *performs* the create calls (define only, does
    NOT start) for every saved instance and node (needs live AgentClients).
    Returns (hosts_to_add, instances_created, nodes_created, errors):
    hosts_to_add is [{"name","url"}, ...] for the caller to add via its own
    HostStore (left to the caller since that's a stateful object main.py
    already owns); errors lists anything that failed (unreachable host,
    port conflict, missing script, etc.) so the caller can show a clear
    summary instead of a silent partial success."""
    doc = yaml.safe_load(Path(path).read_text()) or {}
    hosts_out: List[Dict[str, str]] = []
    instances_created = 0
    nodes_created = 0
    errors: List[str] = []
    for h in doc.get("hosts", []):
        url = h.get("url", "")
        if not url:
            errors.append(f"host entry missing 'url': {h}")
            continue
        name = h.get("name") or url
        hosts_out.append({"name": name, "url": url})
        client = AgentClient(url)
        for inst in h.get("instances", []):
            inst_name = inst.get("name", "?")
            try:
                client.create_instance(
                    name=inst.get("name", ""),
                    can_ifaces=inst.get("can_ifaces") or [],
                    eth_ifaces=inst.get("eth_ifaces") or [],
                    node_plugins=inst.get("node_plugins") or [],
                    grpc_port=inst.get("grpc_port"),
                    tick_ms=inst.get("tick_ms"),
                    tick_us=inst.get("tick_us"),
                    gateway_bin=inst.get("gateway_bin"),
                )
                instances_created += 1
            except AgentError as e:
                errors.append(f"{name} / instance {inst_name}: {e}")
        for node in h.get("nodes", []):
            node_name = node.get("name", "?")
            try:
                client.create_node(
                    script_path=node.get("script_path", ""),
                    name=node.get("name", ""),
                    target_host=node.get("target_host", ""),
                    extra_args=node.get("extra_args") or [],
                )
                nodes_created += 1
            except AgentError as e:
                errors.append(f"{name} / node {node_name}: {e}")
    return hosts_out, instances_created, nodes_created, errors
