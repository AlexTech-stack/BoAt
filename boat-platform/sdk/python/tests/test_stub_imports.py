import importlib


def test_generated_stub_modules_import_from_boat_v1_package():
    modules = [
        "boat.v1.can_tp_pb2",
        "boat.v1.can_tp_pb2_grpc",
        "boat.v1.common_pb2",
        "boat.v1.fault_pb2",
        "boat.v1.fault_pb2_grpc",
        "boat.v1.metrics_pb2",
        "boat.v1.metrics_pb2_grpc",
        "boat.v1.node_plugin_pb2",
        "boat.v1.node_plugin_pb2_grpc",
        "boat.v1.plugin_pb2",
        "boat.v1.plugin_pb2_grpc",
        "boat.v1.replay_pb2",
        "boat.v1.replay_pb2_grpc",
        "boat.v1.scenario_pb2",
        "boat.v1.scenario_pb2_grpc",
        "boat.v1.signal_pb2",
        "boat.v1.signal_pb2_grpc",
        "boat.v1.simulation_pb2",
        "boat.v1.simulation_pb2_grpc",
        "boat.v1.trace_pb2",
        "boat.v1.trace_pb2_grpc",
    ]
    for module_name in modules:
        importlib.import_module(module_name)


def test_boat_client_loads_all_service_stubs(boat_client):
    assert boat_client.can_tp is not None
    assert boat_client.simulation is not None
    assert boat_client.signal is not None
    assert boat_client.scenario is not None
    assert boat_client.replay is not None
    assert boat_client.plugin is not None
    assert boat_client.node_plugin is not None
    assert boat_client.metrics is not None
    assert boat_client.trace is not None
    assert boat_client.fault is not None


def test_replay_stub_has_new_rpcs():
    from boat.v1 import replay_pb2, replay_pb2_grpc

    assert hasattr(replay_pb2, "PauseReplayRequest")
    assert hasattr(replay_pb2, "ResumeReplayRequest")
    assert hasattr(replay_pb2, "StopReplayRequest")
    assert hasattr(replay_pb2, "ImportTraceDataRequest")
    assert hasattr(replay_pb2, "StartReplayFromEventsRequest")
    assert hasattr(replay_pb2, "ReplaySpeed")
    assert hasattr(replay_pb2, "REPLAY_SPEED_REAL_TIME")
    assert hasattr(replay_pb2, "REPLAY_SPEED_ACCELERATED")
    assert hasattr(replay_pb2, "REPLAY_SPEED_STEP_BY_STEP")

    assert hasattr(replay_pb2_grpc.ReplayServiceServicer, "PauseReplay")
    assert hasattr(replay_pb2_grpc.ReplayServiceServicer, "ResumeReplay")
    assert hasattr(replay_pb2_grpc.ReplayServiceServicer, "StopReplay")
    assert hasattr(replay_pb2_grpc.ReplayServiceServicer, "ImportTraceData")
    assert hasattr(replay_pb2_grpc.ReplayServiceServicer, "StartReplayFromEvents")


def test_can_tp_stub_has_list_sessions_rpc():
    from boat.v1 import can_tp_pb2, can_tp_pb2_grpc

    assert hasattr(can_tp_pb2, "ListSessionsRequest")
    assert hasattr(can_tp_pb2, "ListSessionsResponse")
    assert hasattr(can_tp_pb2, "CanTpSession")
    assert hasattr(can_tp_pb2_grpc.CanTpServiceServicer, "ListSessions")

    session = can_tp_pb2.CanTpSession(iface="vcan0", nsdu_id=0x7E0, rx_state="IDLE", tx_state="IDLE")
    assert session.iface == "vcan0"
    assert session.nsdu_id == 0x7E0


def test_can_tp_stub_has_remove_session_and_subscribe_rpcs():
    from boat.v1 import can_tp_pb2, can_tp_pb2_grpc

    assert hasattr(can_tp_pb2, "RemoveSessionRequest")
    assert hasattr(can_tp_pb2, "RemoveSessionResponse")
    assert hasattr(can_tp_pb2, "SubscribeRequest")
    assert hasattr(can_tp_pb2, "CanTpRxEvent")
    assert hasattr(can_tp_pb2_grpc.CanTpServiceServicer, "RemoveSession")
    assert hasattr(can_tp_pb2_grpc.CanTpServiceServicer, "Subscribe")

    event = can_tp_pb2.CanTpRxEvent(iface="vcan0", nsdu_id=0x7E0, data=b"\xAA\xBB")
    assert event.iface == "vcan0"
    assert event.data == b"\xAA\xBB"

    # source_addr/target_addr in CanTpConfig no longer fall back to nsdu_id --
    # both are just required, non-zero fields with no special-cased default.
    config = can_tp_pb2.CanTpConfig(nsdu_id=0x7E0, source_addr=0x7E0, target_addr=0x7E8)
    assert config.source_addr == 0x7E0
    assert config.target_addr == 0x7E8


def test_start_replay_request_has_speed_fields():
    from boat.v1 import replay_pb2

    req = replay_pb2.StartReplayRequest(
        trace_id="test",
        speed=replay_pb2.REPLAY_SPEED_ACCELERATED,
        speed_multiplier=2.5,
    )
    assert req.trace_id == "test"
    assert req.speed == replay_pb2.REPLAY_SPEED_ACCELERATED
    assert req.speed_multiplier == 2.5
    # mac_map field should exist
    assert hasattr(req, "mac_map")
    req.mac_map["192.168.0.100"] = "02:de:ad:be:ef:01"
    assert req.mac_map["192.168.0.100"] == "02:de:ad:be:ef:01"
