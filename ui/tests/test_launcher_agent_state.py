# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

"""Adoption and restart-persistence tests for the launcher agent.

The agent is imported as a module (not run under uvicorn), with
BOAT_AGENT_STATE_FILE pointed at a per-test temp file so no test touches the
real ~/.boat state.
"""

import importlib
import json
import sys
from pathlib import Path

import pytest

_UI_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_UI_DIR))


@pytest.fixture
def agent(tmp_path, monkeypatch):
    import launcher_agent

    monkeypatch.setenv("BOAT_AGENT_STATE_FILE", str(tmp_path / "agent_state.json"))
    return importlib.reload(launcher_agent)


def _row(port: int = 50099) -> dict:
    return {
        "id": "external:4242",
        "name": "(unmanaged)",
        "gateway_bin": "/bin/true",
        "can_ifaces": ["vcan0"],
        "eth_ifaces": [],
        "node_plugins": [],
        "grpc_port": port,
        "tick_ms": 1,
        "tick_us": None,
        "started_at": 1234.0,
    }


def test_adopt_turns_external_row_into_a_tracked_instance(agent, monkeypatch):
    monkeypatch.setattr(agent, "_pid_is_boat_gateway", lambda pid: pid == 4242)

    inst = agent._registry.adopt(4242, "", _row())
    assert inst.running is True
    assert inst.status == "running"
    assert inst.pid() == 4242
    assert inst.adopted_pid == 4242
    data = inst.to_dict()
    assert data["managed"] is True
    assert data["adopted"] is True
    assert data["grpc_port"] == 50099
    assert "adopted_pid" not in inst.to_definition()


def test_adopted_stop_signals_the_process_and_clears_the_adoption(agent, monkeypatch):
    monkeypatch.setattr(agent, "_pid_is_boat_gateway", lambda pid: pid == 4242)
    inst = agent._registry.adopt(4242, "", _row())

    alive = {"value": True}
    kills = []
    monkeypatch.setattr(agent, "_pid_is_boat_gateway", lambda pid: alive["value"])

    def fake_kill(pid, sig):
        kills.append((pid, sig))
        alive["value"] = False

    monkeypatch.setattr(agent.os, "kill", fake_kill)
    inst.stop(timeout=0.01)

    assert kills == [(4242, agent.signal.SIGTERM)]
    assert inst.adopted_pid is None
    assert inst.status == "stopped"
    assert inst.to_dict()["adopted"] is False


def test_adopted_stop_keeps_tracking_when_the_process_will_not_die(agent, monkeypatch):
    monkeypatch.setattr(agent, "_pid_is_boat_gateway", lambda pid: pid == 4242)
    inst = agent._registry.adopt(4242, "", _row())
    monkeypatch.setattr(agent.os, "kill", lambda pid, sig: None)

    with pytest.raises(RuntimeError):
        inst.stop(timeout=0.01)
    assert inst.adopted_pid == 4242
    assert inst.running is True


def test_adopt_refuses_a_duplicate_pid_or_port(agent, monkeypatch):
    monkeypatch.setattr(agent, "_pid_is_boat_gateway", lambda pid: pid == 4242)
    agent._registry.adopt(4242, "", _row())

    with pytest.raises(RuntimeError):
        agent._registry.adopt(4242, "", _row(50099))
    with pytest.raises(ValueError):
        agent._registry.adopt(4243, "", _row(50099))


def test_adopt_api_promotes_external_id(agent, monkeypatch):
    monkeypatch.setattr(agent, "_external_pid", lambda instance_id: 4242)
    monkeypatch.setattr(agent, "_external_gateway_row", lambda pid: _row())
    monkeypatch.setattr(agent, "_pid_is_boat_gateway", lambda pid: pid == 4242)

    data = agent.api_adopt_instance(
        "external:4242", agent.AdoptInstanceRequest(name="rescued"))

    assert data["name"] == "rescued"
    assert data["managed"] is True
    assert data["adopted"] is True
    assert len(agent._registry.list()) == 1


def test_stopped_definitions_survive_save_and_load(agent):
    inst = agent._registry.create(
        name="persisted",
        can_ifaces=["vcan0"],
        eth_ifaces=["veth0"],
        node_plugins=[],
        grpc_port=50099,
        tick_ms=2,
        tick_us=None,
        gateway_bin="/bin/true",
    )
    node = agent._node_registry.create(
        name="sender", script_path="/bin/true", target_host="localhost:50051",
        extra_args=["--iface", "vcan0"])

    agent._save_state()
    doc = json.loads(agent._state_file_path().read_text())
    assert [i["id"] for i in doc["instances"]] == [inst.id]
    assert [n["id"] for n in doc["nodes"]] == [node.id]

    agent._registry._instances.clear()
    agent._node_registry._nodes.clear()
    agent._load_state()

    loaded = agent._registry.get(inst.id)
    assert loaded.name == "persisted"
    assert loaded.can_ifaces == ["vcan0"]
    assert loaded.eth_ifaces == ["veth0"]
    assert loaded.grpc_port == 50099
    assert loaded.running is False
    loaded_node = agent._node_registry.get(node.id)
    assert loaded_node.script_path == "/bin/true"
    assert loaded_node.extra_args == ["--iface", "vcan0"]


def test_api_create_and_delete_persist_the_definition(agent):
    created = agent.api_create_instance(agent.CreateInstanceRequest(
        name="api-persisted", gateway_bin="/bin/true", grpc_port=50099))
    doc = json.loads(agent._state_file_path().read_text())
    assert any(i["id"] == created["id"] for i in doc["instances"])
    assert (agent._state_file_path().stat().st_mode & 0o777) == 0o600

    agent.api_delete_instance(created["id"])
    doc = json.loads(agent._state_file_path().read_text())
    assert all(i["id"] != created["id"] for i in doc["instances"])


def test_running_definitions_are_not_persisted(agent, monkeypatch):
    inst = agent._registry.create(
        name="live", can_ifaces=[], eth_ifaces=[], node_plugins=[],
        grpc_port=50099, tick_ms=None, tick_us=None, gateway_bin="/bin/true")
    monkeypatch.setattr(agent.GatewayInstance, "running", property(lambda self: True))

    assert all(i["id"] != inst.id for i in agent._state_payload()["instances"])
