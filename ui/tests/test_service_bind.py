# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

"""Bind-address tests for every standalone web service.

The `ui/` and `tools/` FastAPI services are unauthenticated development tools.
They must default to loopback and only listen on a routable address when the
operator explicitly sets the service's `BOAT_*_BIND` variable.
"""

import importlib
import inspect
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
_UI_DIR = _ROOT / "ui"
_TOOLS_DIR = _ROOT / "tools"
for _path in (_UI_DIR, _TOOLS_DIR, _ROOT / "boat-platform" / "sdk" / "python"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

# module name, env var
_SERVICES = [
    ("launcher", "BOAT_LAUNCHER_BIND"),
    ("commander", "BOAT_CMD_BIND"),
    ("control_panel", "BOAT_CP_BIND"),
    ("dashboard", "BOAT_DASH_BIND"),
    ("debug", "BOAT_DEBUG_BIND"),
    ("recorder", "BOAT_REC_BIND"),
    ("system_dashboard", "BOAT_SYSTEM_BIND"),
    ("pdu_editor", "BOAT_PDU_EDITOR_BIND"),
    ("trace_editor", "BOAT_TRACE_EDITOR_BIND"),
    ("trace_analyzer", "BOAT_TRACE_ANALYZER_BIND"),
    ("eth_trace_analyzer", "BOAT_ETH_ANALYZER_BIND"),
]


def _load(module_name: str):
    try:
        module = importlib.import_module(module_name)
        return importlib.reload(module)
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"cannot import {module_name}: {exc}")


@pytest.mark.parametrize("module_name,env_var", _SERVICES)
def test_unset_bind_defaults_to_loopback(monkeypatch, module_name, env_var):
    monkeypatch.delenv(env_var, raising=False)
    module = _load(module_name)
    try:
        assert module._BIND == "127.0.0.1"
    finally:
        monkeypatch.delenv(env_var, raising=False)
        _load(module_name)


@pytest.mark.parametrize("module_name,env_var", _SERVICES)
def test_empty_bind_defaults_to_loopback(monkeypatch, module_name, env_var):
    monkeypatch.setenv(env_var, "   ")
    module = _load(module_name)
    try:
        assert module._BIND == "127.0.0.1"
    finally:
        monkeypatch.delenv(env_var, raising=False)
        _load(module_name)


@pytest.mark.parametrize("module_name,env_var", _SERVICES)
def test_bind_env_override(monkeypatch, module_name, env_var):
    monkeypatch.setenv(env_var, "0.0.0.0")
    module = _load(module_name)
    try:
        assert module._BIND == "0.0.0.0"
    finally:
        monkeypatch.delenv(env_var, raising=False)
        _load(module_name)


@pytest.mark.parametrize("module_name,env_var", _SERVICES)
def test_main_does_not_hardcode_wildcard_bind(module_name, env_var):
    module = _load(module_name)
    source = inspect.getsource(module)
    assert 'host="0.0.0.0"' not in source
    assert "host=_BIND" in source
