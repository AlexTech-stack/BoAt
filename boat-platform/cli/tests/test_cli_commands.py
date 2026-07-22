from __future__ import annotations

import struct
from types import SimpleNamespace
from unittest.mock import Mock, patch

import can
import grpc
from typer.testing import CliRunner

from boat.v1 import can_tp_pb2
from boat_cli.main import app

runner = CliRunner()


def _fake_client() -> SimpleNamespace:
    simulation = SimpleNamespace(
        CreateSimulation=Mock(return_value=SimpleNamespace(simulation=SimpleNamespace(simulation_id="sim-1", state=1, tick=0))),
        StartSimulation=Mock(return_value=SimpleNamespace(simulation=SimpleNamespace(state=1))),
        PauseSimulation=Mock(return_value=SimpleNamespace(simulation=SimpleNamespace(state=2))),
        StepSimulation=Mock(return_value=SimpleNamespace(simulation=SimpleNamespace(tick=12, state=1))),
        StopSimulation=Mock(return_value=SimpleNamespace(simulation=SimpleNamespace(state=3))),
        GetSimulationState=Mock(return_value=SimpleNamespace(simulation=SimpleNamespace(state=1))),
        ListSimulations=Mock(return_value=SimpleNamespace(simulations=[SimpleNamespace(simulation_id="sim-1", state=1)])),
        WatchSimulation=Mock(return_value=[]),
    )
    scenario = SimpleNamespace(
        CreateScenario=Mock(return_value=SimpleNamespace(scenario=SimpleNamespace(scenario_id="scn-1", name="s", content="{}"))),
        GetScenario=Mock(return_value=SimpleNamespace(scenario=SimpleNamespace(scenario_id="scn-1", name="s", content="{}"))),
        ListScenarios=Mock(return_value=SimpleNamespace(scenarios=[SimpleNamespace(scenario_id="scn-1", name="s")])),
        DeleteScenario=Mock(return_value=SimpleNamespace(deleted=True)),
        ValidateScenario=Mock(return_value=SimpleNamespace(valid=True, issues=[])),
    )
    replay = SimpleNamespace(
        StartReplay=Mock(return_value=SimpleNamespace(accepted=True, replay_id="r1")),
        SeekReplay=Mock(return_value=SimpleNamespace(accepted=True)),
        StreamReplay=Mock(return_value=[]),
        PauseReplay=Mock(return_value=SimpleNamespace(accepted=True)),
        ResumeReplay=Mock(return_value=SimpleNamespace(accepted=True)),
        StopReplay=Mock(return_value=SimpleNamespace(accepted=True)),
        StartReplayFromEvents=Mock(return_value=SimpleNamespace(accepted=True, replay_id="r-evt")),
    )
    plugin = SimpleNamespace(
        RegisterPlugin=Mock(return_value=SimpleNamespace(plugin=SimpleNamespace(plugin_id="p1", name="plug", version="1.0", loaded=True, config_json=""))),
        ListPlugins=Mock(return_value=SimpleNamespace(plugins=[SimpleNamespace(plugin_id="p1", name="plug", loaded=True, config_json="")])),
        GetPluginInfo=Mock(return_value=SimpleNamespace(plugin=SimpleNamespace(plugin_id="p1", name="plug", version="1.0", loaded=True, config_json=""))),
        UnloadPlugin=Mock(return_value=SimpleNamespace(unloaded=True)),
    )
    node_plugin = SimpleNamespace(
        ListNodePlugins=Mock(return_value=SimpleNamespace(plugins=[
            SimpleNamespace(plugin_id="./can_tp.so?iface=vcan0", name="./can_tp.so?iface=vcan0",
                            loaded=True, config_json='{"iface":"vcan0"}'),
        ])),
        GetNodePluginInfo=Mock(return_value=SimpleNamespace(plugin=SimpleNamespace(
            plugin_id="./can_tp.so?iface=vcan0", name="./can_tp.so?iface=vcan0",
            version="unknown", loaded=True, config_json='{"iface":"vcan0"}'))),
        UnloadNodePlugin=Mock(return_value=SimpleNamespace(unloaded=True)),
    )
    can_tp = SimpleNamespace(
        Configure=Mock(return_value=SimpleNamespace(ok=True, iface="vcan0")),
        Send=Mock(return_value=SimpleNamespace(result=can_tp_pb2.SEND_RESULT_SINGLE_FRAME)),
        ListSessions=Mock(return_value=SimpleNamespace(sessions=[
            SimpleNamespace(iface="vcan0", nsdu_id=0x7E0, source_addr=0x7E0, target_addr=0x7E8,
                            block_size=0, st_min=0, can_dlc=8, extended_addressing=False,
                            rx_state="IDLE", tx_state="IDLE"),
        ])),
    )
    can = SimpleNamespace(
        ListBuses=Mock(return_value=SimpleNamespace(
            buses=[SimpleNamespace(iface="vcan0", driver="vcan",
                                   state="unknown", fd_support=False, bitrate=0)]
        )),
        SendCanFrame=Mock(return_value=SimpleNamespace(accepted=True)),
        SubscribeCanFrames=Mock(return_value=[]),
    )
    eth_stream = Mock()
    eth_stream.__iter__ = Mock(return_value=iter([]))
    eth_stream.cancel = Mock()
    ethernet = SimpleNamespace(
        ListInterfaces=Mock(return_value=SimpleNamespace(ifaces=["veth0", "veth1"])),
        SendFrame=Mock(return_value=SimpleNamespace(accepted=True)),
        SubscribeFrames=Mock(return_value=eth_stream),
    )
    return SimpleNamespace(
        simulation=simulation, scenario=scenario, replay=replay, plugin=plugin,
        node_plugin=node_plugin, can=can, can_tp=can_tp, ethernet=ethernet, close=lambda: None,
    )


def test_sim_commands_call_expected_methods() -> None:
    fake_client = _fake_client()
    with patch("boat_cli.main.BoAtClient", return_value=fake_client):
        assert runner.invoke(app, ["sim", "create", "--scenario", "s1"]).exit_code == 0
        assert runner.invoke(app, ["sim", "start", "sim-1"]).exit_code == 0
        assert runner.invoke(app, ["sim", "pause", "sim-1"]).exit_code == 0
        assert runner.invoke(app, ["sim", "step", "sim-1", "--ticks", "12"]).exit_code == 0
        assert runner.invoke(app, ["sim", "stop", "sim-1"]).exit_code == 0
        assert runner.invoke(app, ["sim", "state", "sim-1"]).exit_code == 0
        assert runner.invoke(app, ["sim", "list"]).exit_code == 0
        assert runner.invoke(app, ["sim", "watch", "sim-1"]).exit_code == 0

    assert fake_client.simulation.CreateSimulation.called
    assert fake_client.simulation.StartSimulation.called
    assert fake_client.simulation.PauseSimulation.called
    assert fake_client.simulation.StepSimulation.called
    assert fake_client.simulation.StopSimulation.called
    assert fake_client.simulation.GetSimulationState.called
    assert fake_client.simulation.ListSimulations.called
    assert fake_client.simulation.WatchSimulation.called


def test_scenario_commands_call_expected_methods(tmp_path) -> None:
    fake_client = _fake_client()
    scenario_file = tmp_path / "scenario.json"
    scenario_file.write_text("{}", encoding="utf-8")

    with patch("boat_cli.main.BoAtClient", return_value=fake_client):
        assert runner.invoke(app, ["scenario", "create", "--file", str(scenario_file)]).exit_code == 0
        assert runner.invoke(app, ["scenario", "get", "scn-1"]).exit_code == 0
        assert runner.invoke(app, ["scenario", "list"]).exit_code == 0
        assert runner.invoke(app, ["scenario", "delete", "scn-1"]).exit_code == 0
        assert runner.invoke(app, ["scenario", "validate", "--file", str(scenario_file)]).exit_code == 0

    assert fake_client.scenario.CreateScenario.called
    assert fake_client.scenario.GetScenario.called
    assert fake_client.scenario.ListScenarios.called
    assert fake_client.scenario.DeleteScenario.called
    assert fake_client.scenario.ValidateScenario.called


def test_replay_and_plugin_commands_call_expected_methods() -> None:
    fake_client = _fake_client()
    with patch("boat_cli.main.BoAtClient", return_value=fake_client):
        assert runner.invoke(app, ["replay", "start", "--trace", "trace-1"]).exit_code == 0
        assert runner.invoke(app, ["replay", "seek", "--tick", "10"]).exit_code == 0
        assert runner.invoke(app, ["replay", "stream", "--trace", "trace-1"]).exit_code == 0
        assert runner.invoke(app, ["plugin", "register", "--path", "libdemo.so"]).exit_code == 0
        assert runner.invoke(app, ["plugin", "list"]).exit_code == 0
        assert runner.invoke(app, ["plugin", "info", "p1"]).exit_code == 0
        # No --scope -> defaults to "sim", unchanged from before node scope existed.
        assert runner.invoke(app, ["plugin", "unload", "p1"]).exit_code == 0

    assert fake_client.replay.StartReplay.called
    assert fake_client.replay.SeekReplay.called
    assert fake_client.replay.StreamReplay.called
    assert fake_client.plugin.RegisterPlugin.called
    assert fake_client.plugin.ListPlugins.called
    assert fake_client.plugin.GetPluginInfo.called
    assert fake_client.plugin.UnloadPlugin.called
    # `plugin list` merges both PluginManager scopes into one table.
    assert fake_client.node_plugin.ListNodePlugins.called


def test_plugin_node_scope_commands() -> None:
    fake_client = _fake_client()
    with patch("boat_cli.main.BoAtClient", return_value=fake_client):
        info_result = runner.invoke(app, [
            "plugin", "info", "./can_tp.so?iface=vcan0", "--scope", "node",
        ])
        assert info_result.exit_code == 0
        assert fake_client.node_plugin.GetNodePluginInfo.called

        # Node unload requires --yes -- refused without it, client-side,
        # before even attempting the RPC.
        refused = runner.invoke(app, [
            "plugin", "unload", "./can_tp.so?iface=vcan0", "--scope", "node",
        ])
        assert refused.exit_code != 0
        assert not fake_client.node_plugin.UnloadNodePlugin.called

        confirmed = runner.invoke(app, [
            "plugin", "unload", "./can_tp.so?iface=vcan0", "--scope", "node", "--yes",
        ])
        assert confirmed.exit_code == 0
        assert fake_client.node_plugin.UnloadNodePlugin.called
        unload_request = fake_client.node_plugin.UnloadNodePlugin.call_args[0][0]
        assert unload_request.confirm is True


def test_plugin_invalid_scope_rejected() -> None:
    fake_client = _fake_client()
    with patch("boat_cli.main.BoAtClient", return_value=fake_client):
        result = runner.invoke(app, ["plugin", "info", "p1", "--scope", "bogus"])

    assert result.exit_code != 0
    assert not fake_client.plugin.GetPluginInfo.called
    assert not fake_client.node_plugin.GetNodePluginInfo.called


def test_can_tp_commands_call_expected_methods() -> None:
    fake_client = _fake_client()
    with patch("boat_cli.main.BoAtClient", return_value=fake_client):
        assert runner.invoke(app, [
            "can-tp", "configure", "--nsdu-id", "0x7E0",
            "--source-addr", "0x7E0", "--target-addr", "0x7E8",
            "--bs", "0", "--stmin", "0",
        ]).exit_code == 0
        assert runner.invoke(app, [
            "can-tp", "send", "--nsdu-id", "0x7E0",
            "--source-addr", "0x7E0", "--target-addr", "0x7E8",
            "--data", "0123",
        ]).exit_code == 0

    assert fake_client.can_tp.Configure.called
    assert fake_client.can_tp.Send.called

    # The single-frame gate that used to reject small payloads must stay gone.
    send_request = fake_client.can_tp.Send.call_args[0][0]
    assert send_request.data == bytes.fromhex("0123")


def test_can_tp_list_sessions() -> None:
    fake_client = _fake_client()
    with patch("boat_cli.main.BoAtClient", return_value=fake_client):
        result = runner.invoke(app, ["can-tp", "list-sessions"])

    assert result.exit_code == 0
    assert fake_client.can_tp.ListSessions.called
    # Default call lists across every loaded instance -- no iface filter.
    assert fake_client.can_tp.ListSessions.call_args[0][0].iface == ""
    assert "0x7E0" in result.output
    assert "vcan0" in result.output

    fake_client2 = _fake_client()
    with patch("boat_cli.main.BoAtClient", return_value=fake_client2):
        result2 = runner.invoke(app, ["can-tp", "list-sessions", "--iface", "vcan1"])

    assert result2.exit_code == 0
    assert fake_client2.can_tp.ListSessions.call_args[0][0].iface == "vcan1"


def test_can_tp_iface_flag_is_threaded_into_requests() -> None:
    # --iface used to be a client-side post-hoc check; it must now be sent
    # on the wire so the gateway can select among multiple loaded instances.
    fake_client = _fake_client()
    with patch("boat_cli.main.BoAtClient", return_value=fake_client):
        assert runner.invoke(app, [
            "can-tp", "configure", "--nsdu-id", "0x7E0",
            "--source-addr", "0x7E0", "--target-addr", "0x7E8",
            "--iface", "vcan1",
        ]).exit_code == 0
        assert runner.invoke(app, [
            "can-tp", "send", "--nsdu-id", "0x7E0",
            "--source-addr", "0x7E0", "--target-addr", "0x7E8",
            "--data", "0123", "--iface", "vcan1",
        ]).exit_code == 0

    configure_request = fake_client.can_tp.Configure.call_args[0][0]
    assert configure_request.iface == "vcan1"
    send_request = fake_client.can_tp.Send.call_args[0][0]
    assert send_request.iface == "vcan1"


def test_can_tp_configure_reports_ambiguous_instance_error() -> None:
    fake_client = _fake_client()

    class _AmbiguousError(grpc.RpcError):
        def code(self):
            return grpc.StatusCode.FAILED_PRECONDITION

        def details(self):
            return "multiple CanTp instances loaded (vcan0, vcan1); specify --iface to disambiguate"

    fake_client.can_tp.Configure = Mock(side_effect=_AmbiguousError())

    with patch("boat_cli.main.BoAtClient", return_value=fake_client):
        result = runner.invoke(app, [
            "can-tp", "configure", "--nsdu-id", "0x7E0",
            "--source-addr", "0x7E0", "--target-addr", "0x7E8",
        ])

    assert result.exit_code != 0
    assert "specify --iface to disambiguate" in result.output


def test_can_tp_send_reports_rpc_error() -> None:
    fake_client = _fake_client()

    class _FakeRpcError(grpc.RpcError):
        def code(self):
            return grpc.StatusCode.FAILED_PRECONDITION

        def details(self):
            return "no N-SDU connection configured for nsdu_id=0x7e0; call Configure first"

    fake_client.can_tp.Send = Mock(side_effect=_FakeRpcError())

    with patch("boat_cli.main.BoAtClient", return_value=fake_client):
        result = runner.invoke(app, [
            "can-tp", "send", "--nsdu-id", "0x7E0",
            "--source-addr", "0x7E0", "--target-addr", "0x7E8",
            "--data", "0123",
        ])

    assert result.exit_code != 0
    assert "FAILED_PRECONDITION" in result.output


def test_new_replay_commands_call_expected_methods() -> None:
    fake_client = _fake_client()
    with patch("boat_cli.main.BoAtClient", return_value=fake_client):
        assert runner.invoke(app, ["replay", "pause"]).exit_code == 0
        assert runner.invoke(app, ["replay", "resume"]).exit_code == 0
        assert runner.invoke(app, ["replay", "stop"]).exit_code == 0
        assert runner.invoke(app, [
            "replay", "from-events", "--sim-id", "sim-1",
            "--speed", "accelerated", "--multiplier", "2.0",
        ]).exit_code == 0
        assert runner.invoke(app, [
            "replay", "start", "--trace", "t1",
            "--speed", "step", "--multiplier", "3.0", "--sim-id", "s1",
        ]).exit_code == 0

    assert fake_client.replay.PauseReplay.called
    assert fake_client.replay.ResumeReplay.called
    assert fake_client.replay.StopReplay.called
    assert fake_client.replay.StartReplayFromEvents.called


def test_trace_replay_rejects_pcap(tmp_path) -> None:
    pcap_file = tmp_path / "capture.pcap"
    pcap_file.write_bytes(b"")

    result = runner.invoke(app, ["trace", "replay", str(pcap_file)])

    assert result.exit_code != 0
    assert "boat replay import" in result.output


def test_trace_replay_help_has_no_server_side_or_ethernet_flags() -> None:
    result = runner.invoke(app, ["trace", "replay", "--help"])

    assert result.exit_code == 0
    for removed_flag in (
        "--server-side", "--ip-filter", "--ip-map", "--ethertype",
        "--protocol", "--src-ip-filter", "--dst-ip-filter", "--src-port",
        "--dst-port", "--replay-src-ip", "--replay-dst-ip",
        "--replay-src-mac", "--replay-dst-mac", "--mac-map",
    ):
        assert removed_flag not in result.output, f"{removed_flag} should have been removed"
    for kept_flag in ("--buses", "--speed", "--loop", "--sim-id", "--verbose", "--channel", "--id"):
        assert kept_flag in result.output, f"{kept_flag} should still be present"


def test_replay_import_reports_correct_frame_count(tmp_path) -> None:
    asc_file = tmp_path / "sample.asc"
    with can.ASCWriter(str(asc_file)) as writer:
        writer.on_message_received(can.Message(arbitration_id=0x100, data=[1, 2, 3, 4], channel=1))
        writer.on_message_received(can.Message(arbitration_id=0x200, data=[5, 6, 7, 8], channel=1))
        writer.on_message_received(can.Message(arbitration_id=0x300, data=[9, 10], channel=1))

    fake_client = _fake_client()
    fake_client.replay = SimpleNamespace(
        **vars(fake_client.replay),
        ImportTraceData=Mock(return_value=SimpleNamespace(accepted=True)),
    )

    with patch("boat_cli.main.BoAtClient", return_value=fake_client):
        result = runner.invoke(app, ["replay", "import", str(asc_file), "--trace-id", "t-count"])

    assert result.exit_code == 0
    assert fake_client.replay.ImportTraceData.called
    uploaded = fake_client.replay.ImportTraceData.call_args[0][0].data

    # Independently verify the actual record count by walking the same
    # length-prefixed stream the fix is supposed to parse correctly.
    expected_frames = 0
    off = 0
    while off + 4 <= len(uploaded):
        (record_len,) = struct.unpack_from("<I", uploaded, off)
        off += 4 + record_len
        expected_frames += 1
    assert expected_frames == 3

    assert "3" in result.output


def test_trace_replay_applies_id_filter_and_shows_it_in_banner(tmp_path) -> None:
    # `boat trace replay` does NOT go through the CLI's injected BoAtClient --
    # TraceReplayer opens its own gRPC stub via _get_stub(), so that (not
    # boat_cli.main.BoAtClient) is what must be mocked here.
    asc_file = tmp_path / "mixed.asc"
    with can.ASCWriter(str(asc_file)) as writer:
        writer.on_message_received(can.Message(arbitration_id=0x583, data=[1], channel=4))
        writer.on_message_received(can.Message(arbitration_id=0x100, data=[2], channel=4))
        writer.on_message_received(can.Message(arbitration_id=0x583, data=[3], channel=4))
        writer.on_message_received(can.Message(arbitration_id=0x200, data=[4], channel=4))

    fake_stub = Mock()
    fake_stub.SendCanFrame = Mock(return_value=SimpleNamespace(accepted=True))

    with patch("boat.trace_replay.TraceReplayer._get_stub", return_value=fake_stub):
        result = runner.invoke(app, [
            "trace", "replay", str(asc_file),
            "--channel", "4", "--id", "0x583", "--buses", "vcan0", "--speed", "0",
        ])

    assert result.exit_code == 0
    assert "0x583" in result.output  # banner echoes the applied id filter

    sent_ids = [call.args[0].frame.can_id for call in fake_stub.SendCanFrame.call_args_list]
    assert sent_ids == [0x583, 0x583]


def test_replay_stream_shows_progress_without_verbose() -> None:
    fake_client = _fake_client()
    fake_events = [SimpleNamespace(tick=1000 + i * 200, payload=b"x") for i in range(3)]
    fake_client.replay.StreamReplay = Mock(return_value=fake_events)

    with patch("boat_cli.main.BoAtClient", return_value=fake_client):
        result = runner.invoke(app, ["replay", "stream", "--trace", "t1"])

    assert result.exit_code == 0
    assert "Streaming..." in result.output
    assert "Done — 3 frame(s)." in result.output
    assert "payload=" not in result.output  # that detail is verbose-only


def test_replay_stream_verbose_shows_per_frame_detail() -> None:
    fake_client = _fake_client()
    fake_events = [SimpleNamespace(tick=1000, payload=b"\xde\xad\xbe\xef")]
    fake_client.replay.StreamReplay = Mock(return_value=fake_events)

    with patch("boat_cli.main.BoAtClient", return_value=fake_client):
        result = runner.invoke(app, ["replay", "stream", "--trace", "t1", "--verbose"])

    assert result.exit_code == 0
    assert "tick=1000" in result.output
    assert "payload=deadbeef" in result.output
    assert "Streaming..." not in result.output  # progress counter is non-verbose-only


def test_frame_send_rejects_tcp_client_side() -> None:
    fake_client = _fake_client()
    fake_client.frame = SimpleNamespace(SendFrame=Mock())

    with patch("boat_cli.main.BoAtClient", return_value=fake_client):
        result = runner.invoke(app, ["frame", "send", "--bus-type", "tcp", "--data", "AA"])

    assert result.exit_code != 0
    assert "does not support TCP" in result.output
    # Rejected client-side -- never even attempts the RPC.
    assert not fake_client.frame.SendFrame.called



