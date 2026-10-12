# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

"""Bind-address tests for `ui/launcher_agent.py`.

The agent's API is unauthenticated remote process execution: it spawns
gateway binaries and node scripts from caller-supplied paths (issue #39).
Listening on a routable interface therefore has to be an explicit opt-in, and
the default has to stay loopback for the tests below to mean anything.
"""

import importlib
import sys
from pathlib import Path

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
