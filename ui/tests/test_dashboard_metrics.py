# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

"""Metrics-panel state tests for `ui/dashboard.py`."""

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT / "ui"))
sys.path.insert(0, str(_ROOT / "boat-platform" / "sdk" / "python"))

from boat.v1 import metrics_pb2  # noqa: E402


def _dashboard():
    try:
        import dashboard
        return dashboard
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"cannot import dashboard: {exc}")


def test_metrics_snapshot_keeps_only_latest_per_name():
    dashboard = _dashboard()
    state = dashboard.DashboardState()
    state.push_metric(metrics_pb2.MetricPoint(name="can.vcan0.rx_dropped", value=1, tick=10))
    state.push_metric(metrics_pb2.MetricPoint(name="event_bus.dropped", value=2, tick=11))
    state.push_metric(metrics_pb2.MetricPoint(name="can.vcan0.rx_dropped", value=3, tick=12))

    points = state.metrics_snapshot()
    assert [point["name"] for point in points] == [
        "can.vcan0.rx_dropped",
        "event_bus.dropped",
    ]
    assert points[0]["value"] == 3
    assert points[0]["tick"] == 12
    assert points[1]["value"] == 2


def test_api_metrics_returns_snapshot(monkeypatch):
    dashboard = _dashboard()
    state = dashboard.DashboardState()
    state.push_metric(metrics_pb2.MetricPoint(name="event_bus.dropped", value=7, tick=42))
    monkeypatch.setattr(dashboard, "dash", state)

    payload = dashboard.api_metrics()
    assert payload["total"] == 1
    assert payload["points"][0]["name"] == "event_bus.dropped"
    assert payload["points"][0]["value"] == 7
