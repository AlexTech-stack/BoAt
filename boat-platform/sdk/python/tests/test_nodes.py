# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

"""Contract tests for the Python node wrappers.

These cover the shared address-resolution order (explicit > BOAT_HOST >
default) and the error contracts that differ per class. Before this file the
node classes -- the SDK's primary public API -- had no tests at all.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import grpc
import pytest

import boat.bus_node as bus_node
import boat.can_tp as can_tp
import boat.fault_node as fault_node
import boat.frame_node as frame_node
import boat.metrics_node as metrics_node
import boat.pdu_message_node as pdu_message_node
import boat.pdu_node as pdu_node
from boat.v1 import common_pb2, fault_pb2, metrics_pb2


class _FakeClient:
    """Minimal BoAtClient stand-in with only the service surface each test uses."""

    def __init__(self) -> None:
        self.closed = False
        self.channel = SimpleNamespace(close=Mock())
        self.bus = SimpleNamespace()
        self.pdu = SimpleNamespace()
        self.can_tp = SimpleNamespace()
        self.fault = SimpleNamespace()
        self.metrics = SimpleNamespace()

    def close(self) -> None:
        self.closed = True


def _patch_client(monkeypatch, module, client):
    """Make `module`'s BoAtClient constructor return `client`."""
    class FakeBoAtClient:
        def __new__(cls, address=None):
            return client

    monkeypatch.setattr(module, "BoAtClient", FakeBoAtClient)


def _make_nodes():
    return [
        frame_node.FrameNode(),
        pdu_node.PduNode(),
        bus_node.BusNode(),
        can_tp.CanTpHandle(),
        fault_node.FaultNode(),
        metrics_node.MetricsNode(),
    ]


def _close_nodes(nodes):
    for node in nodes:
        node._client.close()


def test_node_addresses_resolve_boat_host(monkeypatch):
    monkeypatch.setenv("BOAT_HOST", "gateway.example:50052")
    nodes = _make_nodes()
    try:
        assert all(node._client.address == "gateway.example:50052" for node in nodes)
    finally:
        _close_nodes(nodes)


def test_explicit_node_address_wins_over_boat_host(monkeypatch):
    monkeypatch.setenv("BOAT_HOST", "env.example:50052")
    nodes = [
        frame_node.FrameNode("explicit.example:50053"),
        pdu_node.PduNode("explicit.example:50053"),
        bus_node.BusNode("explicit.example:50053"),
        can_tp.CanTpHandle("explicit.example:50053"),
        fault_node.FaultNode("explicit.example:50053"),
        metrics_node.MetricsNode("explicit.example:50053"),
    ]
    try:
        assert all(node._client.address == "explicit.example:50053" for node in nodes)
    finally:
        _close_nodes(nodes)


def test_frame_node_reconnects_after_send_failure(monkeypatch):
    first = _FakeClient()
    second = _FakeClient()
    first.frame = SimpleNamespace(SendFrame=lambda req: (_ for _ in ()).throw(grpc.RpcError()))
    clients = iter([first, second])

    class FakeBoAtClient:
        def __new__(cls, address=None):
            return next(clients)

    monkeypatch.setattr(frame_node, "BoAtClient", FakeBoAtClient)

    node = frame_node.FrameNode()
    with pytest.raises(grpc.RpcError):
        node.send_can("vcan0", 0x123, b"\x01")

    assert node._client is second
    first.channel.close.assert_called_once()


def test_pdu_node_returns_false_on_rpc_error(monkeypatch):
    client = _FakeClient()
    client.pdu = SimpleNamespace(
        ConfigureRoute=lambda req: (_ for _ in ()).throw(grpc.RpcError()),
        ListRoutes=lambda req: (_ for _ in ()).throw(grpc.RpcError()),
    )
    _patch_client(monkeypatch, pdu_node, client)

    node = pdu_node.PduNode()
    assert node.configure_route(0x100, 0, "vcan0") is False
    assert node.list_routes() == []


def test_bus_node_returns_false_on_publish_error(monkeypatch):
    client = _FakeClient()
    client.bus = SimpleNamespace(Publish=lambda req: (_ for _ in ()).throw(grpc.RpcError()))
    _patch_client(monkeypatch, bus_node, client)

    node = bus_node.BusNode()
    assert node.publish("vehicle.speed", 12.0) is False


def test_can_tp_handle_raises_on_rpc_error(monkeypatch):
    client = _FakeClient()
    client.can_tp = SimpleNamespace(
        Configure=lambda req: (_ for _ in ()).throw(grpc.RpcError()),
    )
    _patch_client(monkeypatch, can_tp, client)

    node = can_tp.CanTpHandle()
    with pytest.raises(grpc.RpcError):
        node.configure(0x7E0, 0x7E0, 0x7E8)


def test_pdu_message_node_passes_address_and_reports_missing_messages(monkeypatch):
    class FakePduNode:
        def __init__(self, address=None):
            self.address = address
            self.configured = []

        def configure_route(self, **kwargs):
            self.configured.append(kwargs)

        def configure_container(self, **kwargs):
            self.configured.append(kwargs)

        def send(self, pdu_id, payload):
            return True

    class FakeDb:
        def messages(self):
            return []

        def by_name_and_bus(self, name, bus):
            return None

        def by_id(self, db_id):
            return None

    monkeypatch.setattr(pdu_message_node, "PduNode", FakePduNode)
    monkeypatch.setattr(pdu_message_node, "PduDatabase", lambda path: FakeDb())

    node = pdu_message_node.PduMessageNode("unused.json", address=None)
    assert node._pdu.address is None
    with pytest.raises(LookupError):
        node.send_message("missing", "CAN")


def test_fault_type_names_cover_all_engine_modes():
    assert {
        fault_pb2.FAULT_TYPE_SIGNAL_STUCK,
        fault_pb2.FAULT_TYPE_SIGNAL_CORRUPTION,
        fault_pb2.FAULT_TYPE_PACKET_DROP,
        fault_pb2.FAULT_TYPE_SIGNAL_INVERT,
        fault_pb2.FAULT_TYPE_DELAY,
    }.issubset(set(fault_node.FAULT_TYPES.values()))


def test_fault_node_maps_the_full_request():
    client = _FakeClient()
    captured = {}

    def inject(req):
        captured["request"] = req
        return fault_pb2.InjectFaultResponse(
            fault=fault_pb2.FaultEvent(
                fault_id="fault-1",
                simulation_id=req.simulation_id,
                target=req.target,
                type=req.type,
                tick=req.tick,
                end_tick=req.end_tick,
                magnitude=req.magnitude,
                delay_ticks=req.delay_ticks,
            )
        )

    client.fault = SimpleNamespace(InjectFault=inject)

    node = fault_node.FaultNode(client)
    event = node.inject_by_name(
        "vehicle.speed", "stuck", 10, end_tick=20, magnitude=42.5, simulation_id="sim-1"
    )

    request = captured["request"]
    assert request.type == fault_pb2.FAULT_TYPE_SIGNAL_STUCK
    assert request.end_tick == 20
    assert request.magnitude == 42.5
    assert event.fault_id == "fault-1"


def test_fault_node_maps_delay_request():
    client = _FakeClient()
    captured = {}

    def inject(req):
        captured["request"] = req
        return fault_pb2.InjectFaultResponse(fault=fault_pb2.FaultEvent(fault_id="fault-delay"))

    client.fault = SimpleNamespace(InjectFault=inject)
    node = fault_node.FaultNode(client)
    node.inject_by_name("vehicle.speed", "delay", 5, delay_ticks=3)

    request = captured["request"]
    assert request.type == fault_pb2.FAULT_TYPE_DELAY
    assert request.delay_ticks == 3


def test_fault_node_lists_faults_with_pagination():
    client = _FakeClient()
    captured = {}

    def list_faults(req):
        captured["request"] = req
        return fault_pb2.ListFaultsResponse(
            faults=[fault_pb2.FaultEvent(fault_id="fault-1", target="speed")],
            page=common_pb2.PaginationResponse(next_page_token="next2", total_size=1),
        )

    client.fault = SimpleNamespace(ListFaults=list_faults)

    node = fault_node.FaultNode(client)
    faults = node.list_faults("sim-1", page_size=5, page_token="next")

    request = captured["request"]
    assert request.simulation_id == "sim-1"
    assert request.page.page_size == 5
    assert request.page.page_token == "next"
    assert faults[0].fault_id == "fault-1"
    assert node.list_faults_page("sim-1").page.next_page_token == "next2"


def test_fault_node_rejects_unknown_type_and_bad_range():
    node = fault_node.FaultNode(_FakeClient())

    with pytest.raises(ValueError):
        node.inject_by_name("x", "not-a-fault", 1)
    with pytest.raises(ValueError):
        node.inject("x", fault_pb2.FAULT_TYPE_SIGNAL_STUCK, 10, end_tick=9)


def test_metrics_node_returns_snapshot_and_stream():
    point = metrics_pb2.MetricPoint(name="can.vcan0.rx_dropped", value=3.0, tick=7)
    client = _FakeClient()
    client.metrics = SimpleNamespace(
        GetMetrics=lambda req: metrics_pb2.MetricsResponse(points=[point]),
        StreamMetrics=lambda req: iter([point]),
    )

    node = metrics_node.MetricsNode(client)
    assert node.get() == [point]
    assert list(node.stream()) == [point]
