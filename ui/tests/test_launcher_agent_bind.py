# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

"""Bind-address tests for `ui/launcher_agent.py`.

The agent's API is unauthenticated remote process execution: it spawns
gateway binaries and node scripts from caller-supplied paths (issue #39).
Listening on a routable interface therefore has to be an explicit opt-in, and
the default has to stay loopback for the tests below to mean anything.
"""

import importlib
import os
import runpy
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

_UI_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_UI_DIR))


def test_default_bind_is_loopback(monkeypatch):
    monkeypatch.delenv("BOAT_AGENT_BIND", raising=False)
    import launcher_agent

    module = importlib.reload(launcher_agent)
    assert module._AGENT_BIND == "127.0.0.1"


def test_bind_env_override(monkeypatch):
    monkeypatch.setenv("BOAT_AGENT_BIND", "0.0.0.0")
    import launcher_agent

    module = importlib.reload(launcher_agent)
    assert module._AGENT_BIND == "0.0.0.0"

    # Leave the module in its default state for any later importer.
    monkeypatch.delenv("BOAT_AGENT_BIND", raising=False)
    importlib.reload(launcher_agent)


@pytest.mark.parametrize("raw,expected", [
    (None, "127.0.0.1"),
    ("", "127.0.0.1"),
    ("   ", "127.0.0.1"),
    ("0.0.0.0", "0.0.0.0"),
    (" 127.0.0.1 ", "127.0.0.1"),
])
def test_resolve_agent_bind(raw, expected):
    import launcher_agent

    assert launcher_agent._resolve_agent_bind(raw) == expected


@pytest.mark.parametrize("addr,expected", [
    ("127.0.0.1", True),
    ("127.0.1.1", True),
    ("::1", True),
    ("localhost", True),
    ("0.0.0.0", False),
    ("::", False),
    ("192.168.1.10", False),
    ("agent.lab", False),
])
def test_is_loopback_bind(addr, expected):
    import launcher_agent

    assert launcher_agent._is_loopback_bind(addr) is expected


@pytest.mark.parametrize("host,expected", [
    ("localhost:8090", True),
    ("127.0.0.1:8090", True),
    ("127.0.1.1:8090", True),
    ("192.168.1.10:8090", True),   # IP literal, not a rebinding domain
    ("[::1]:8090", True),
    ("evil.example:8090", False),
    ("", False),
])
def test_host_allowed(host, expected):
    import launcher_agent

    assert launcher_agent._host_allowed(host) is expected


@pytest.mark.parametrize("origin,expected", [
    (None, True),                        # native admin_gui / curl
    ("http://localhost:3000", True),
    ("http://127.0.0.1:3000", True),
    ("https://evil.example", False),
    ("http://192.168.1.10:3000", False),  # cross-origin browser page
    ("null", False),
    ("file:///tmp/x", False),
])
def test_origin_allowed(origin, expected):
    import launcher_agent

    assert launcher_agent._origin_allowed(origin) is expected


def test_external_pid_rejects_malformed_ids():
    import launcher_agent

    for value in ("external:", "external:abc", "external:1.5"):
        with pytest.raises(HTTPException) as raised:
            launcher_agent._external_pid(value)
        assert raised.value.status_code == 400


def test_external_pid_rejects_non_positive_pids():
    import launcher_agent

    for value in ("external:0", "external:-1"):
        with pytest.raises(HTTPException) as raised:
            launcher_agent._external_pid(value)
        assert raised.value.status_code == 400


def test_external_pid_rejects_processes_that_are_not_gateways():
    import launcher_agent

    with pytest.raises(HTTPException) as raised:
        launcher_agent._external_pid(f"external:{os.getpid()}")
    assert raised.value.status_code == 409


def test_main_passes_resolved_bind_to_uvicorn(monkeypatch, capsys):
    import uvicorn

    calls = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.update(kwargs))
    monkeypatch.setenv("BOAT_AGENT_BIND", "")

    runpy.run_path(str(_UI_DIR / "launcher_agent.py"), run_name="__main__")

    assert calls["host"] == "127.0.0.1"
    assert calls["port"] == 8090
    assert "WARNING" not in capsys.readouterr().out


def test_main_warns_on_routable_bind(monkeypatch, capsys):
    import uvicorn

    calls = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.update(kwargs))
    monkeypatch.setenv("BOAT_AGENT_BIND", "0.0.0.0")

    runpy.run_path(str(_UI_DIR / "launcher_agent.py"), run_name="__main__")

    assert calls["host"] == "0.0.0.0"
    assert "WARNING" in capsys.readouterr().out
