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
import boat.debug_node as debug_node
import boat.fault_node as fault_node
import boat.frame_node as frame_node
import boat.metrics_node as metrics_node
import boat.pdu_message_node as pdu_message_node
import boat.pdu_node as pdu_node
import boat.scenario_node as scenario_node
import boat.simulation_node as simulation_node
import boat.trace_node as trace_node
from boat.v1 import common_pb2, debug_pb2, fault_pb2, metrics_pb2, scenario_pb2, simulation_pb2, trace_pb2


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
        self.scenario = SimpleNamespace()
        self.simulation = SimpleNamespace()
        self.debug = SimpleNamespace()
        self.trace = SimpleNamespace()

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
        debug_node.DebugNode(),
        fault_node.FaultNode(),
        metrics_node.MetricsNode(),
        scenario_node.ScenarioNode(),
        simulation_node.SimulationNode(),
        trace_node.TraceNode(),
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
        debug_node.DebugNode("explicit.example:50053"),
        fault_node.FaultNode("explicit.example:50053"),
        metrics_node.MetricsNode("explicit.example:50053"),
        scenario_node.ScenarioNode("explicit.example:50053"),
        simulation_node.SimulationNode("explicit.example:50053"),
        trace_node.TraceNode("explicit.example:50053"),
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


def test_scenario_node_calls_expected_rpcs():
    client = _FakeClient()
    captured = {}

    def create(req):
        captured["create"] = req
        return scenario_pb2.ScenarioResponse(scenario=req.scenario)

    def list_scenarios(req):
        captured["list_page"] = req
        return scenario_pb2.ListScenariosResponse(
            scenarios=[scenario_pb2.Scenario(scenario_id="scn-1", name="Scenario")],
            page=common_pb2.PaginationResponse(next_page_token="next"),
        )

    client.scenario = SimpleNamespace(
        CreateScenario=create,
        GetScenario=lambda req: scenario_pb2.ScenarioResponse(
            scenario=scenario_pb2.Scenario(scenario_id=req.scenario_id, name="Scenario")),
        ListScenarios=list_scenarios,
        ValidateScenario=lambda req: scenario_pb2.ValidateScenarioResponse(valid=True, issues=[]),
        DeleteScenario=lambda req: scenario_pb2.DeleteScenarioResponse(deleted=True),
    )

    node = scenario_node.ScenarioNode(client)
    created = node.create("scn-1", "Scenario", "{}")
    assert created.scenario_id == "scn-1"
    assert captured["create"].scenario.content == "{}"
    assert node.get("scn-1").name == "Scenario"
    assert node.list_page(page_size=2, page_token="tok").page.next_page_token == "next"
    assert captured["list_page"].page.page_size == 2
    assert node.list()[0].scenario_id == "scn-1"
    assert node.validate("{}").valid is True
    assert node.delete("scn-1") is True


def test_simulation_node_calls_expected_rpcs():
    client = _FakeClient()
    captured = {}

    def step(req):
        captured["step"] = req
        return simulation_pb2.SimulationResponse(
            simulation=simulation_pb2.Simulation(simulation_id=req.simulation_id, tick=req.ticks))

    client.simulation = SimpleNamespace(
        CreateSimulation=lambda req: simulation_pb2.SimulationResponse(
            simulation=simulation_pb2.Simulation(simulation_id="sim-1", scenario_id=req.scenario_id)),
        StartSimulation=lambda req: simulation_pb2.SimulationResponse(
            simulation=simulation_pb2.Simulation(simulation_id=req.simulation_id,
                                                  state=common_pb2.SIMULATION_STATE_RUNNING)),
        PauseSimulation=lambda req: simulation_pb2.SimulationResponse(
            simulation=simulation_pb2.Simulation(simulation_id=req.simulation_id,
                                                  state=common_pb2.SIMULATION_STATE_PAUSED)),
        StepSimulation=step,
        StopSimulation=lambda req: simulation_pb2.SimulationResponse(
            simulation=simulation_pb2.Simulation(simulation_id=req.simulation_id,
                                                  state=common_pb2.SIMULATION_STATE_STOPPED)),
        ResetSimulation=lambda req: simulation_pb2.SimulationResponse(
            simulation=simulation_pb2.Simulation(simulation_id=req.simulation_id,
                                                  state=common_pb2.SIMULATION_STATE_IDLE)),
        GetSimulationState=lambda req: simulation_pb2.SimulationResponse(
            simulation=simulation_pb2.Simulation(simulation_id=req.simulation_id,
                                                  state=common_pb2.SIMULATION_STATE_IDLE)),
        ListSimulations=lambda req: simulation_pb2.ListSimulationsResponse(
            simulations=[simulation_pb2.Simulation(simulation_id="sim-1")],
            page=common_pb2.PaginationResponse(next_page_token="next")),
        WatchSimulation=lambda req: iter([
            simulation_pb2.SimulationResponse(
                simulation=simulation_pb2.Simulation(simulation_id=req.simulation_id,
                                                      state=common_pb2.SIMULATION_STATE_RUNNING)),
        ]),
    )

    node = simulation_node.SimulationNode(client)
    assert node.create("scn-1").scenario_id == "scn-1"
    assert node.start("sim-1").state == common_pb2.SIMULATION_STATE_RUNNING
    assert node.pause("sim-1").state == common_pb2.SIMULATION_STATE_PAUSED
    assert node.step("sim-1", 7).tick == 7
    assert captured["step"].ticks == 7
    assert node.stop("sim-1").state == common_pb2.SIMULATION_STATE_STOPPED
    assert node.reset("sim-1").state == common_pb2.SIMULATION_STATE_IDLE
    assert node.state("sim-1").state == common_pb2.SIMULATION_STATE_IDLE
    assert node.list_page(page_size=2).page.next_page_token == "next"
    assert node.list()[0].simulation_id == "sim-1"
    assert list(node.watch("sim-1"))[0].state == common_pb2.SIMULATION_STATE_RUNNING


def test_debug_and_trace_nodes_call_expected_rpcs():
    client = _FakeClient()
    event = debug_pb2.RpcEvent(method="/boat.v1.FrameService/SendFrame")
    client.debug = SimpleNamespace(
        GetEffectiveConfig=lambda req: debug_pb2.GetEffectiveConfigResponse(config_json='{"ok":true}'),
        StreamEvents=lambda req: iter([event]),
    )
    trace_event = trace_pb2.TraceEvent(trace_id="trace-1", tick=3, category="frame")
    client.trace = SimpleNamespace(
        GetTrace=lambda req: trace_pb2.TraceResponse(events=[trace_event]),
        ListTraces=lambda req: trace_pb2.TraceResponse(events=[trace_event]),
        StreamTrace=lambda req: iter([trace_event]),
        MarkStep=lambda req: trace_pb2.MarkStepResponse(marker_timestamp_ns=123),
    )

    debug = debug_node.DebugNode(client)
    trace = trace_node.TraceNode(client)
    assert debug.effective_config() == '{"ok":true}'
    assert list(debug.stream_events("FrameService")) == [event]
    assert list(trace.get("trace-1")) == [trace_event]
    assert list(trace.list()) == [trace_event]
    assert list(trace.stream("trace-1")) == [trace_event]
    assert trace.mark_step("trace-1", 1, "step", sim_tick=3) == 123
