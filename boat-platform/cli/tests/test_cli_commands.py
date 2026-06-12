from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

from typer.testing import CliRunner

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
        StartReplay=Mock(return_value=SimpleNamespace(accepted=True)),
        SeekReplay=Mock(return_value=SimpleNamespace(accepted=True)),
        StreamReplay=Mock(return_value=[]),
    )
    plugin = SimpleNamespace(
        RegisterPlugin=Mock(return_value=SimpleNamespace(plugin=SimpleNamespace(plugin_id="p1", name="plug", version="1.0", loaded=True))),
        ListPlugins=Mock(return_value=SimpleNamespace(plugins=[SimpleNamespace(plugin_id="p1", name="plug", loaded=True)])),
        GetPluginInfo=Mock(return_value=SimpleNamespace(plugin=SimpleNamespace(plugin_id="p1", name="plug", version="1.0", loaded=True))),
        UnloadPlugin=Mock(return_value=SimpleNamespace(unloaded=True)),
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
        can=can, ethernet=ethernet, close=lambda: None,
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
        assert runner.invoke(app, ["replay", "stream"]).exit_code == 0
        assert runner.invoke(app, ["plugin", "register", "--path", "libdemo.so"]).exit_code == 0
        assert runner.invoke(app, ["plugin", "list"]).exit_code == 0
        assert runner.invoke(app, ["plugin", "info", "p1"]).exit_code == 0
        assert runner.invoke(app, ["plugin", "unload", "p1"]).exit_code == 0

    assert fake_client.replay.StartReplay.called
    assert fake_client.replay.SeekReplay.called
    assert fake_client.replay.StreamReplay.called
    assert fake_client.plugin.RegisterPlugin.called
    assert fake_client.plugin.ListPlugins.called
    assert fake_client.plugin.GetPluginInfo.called
    assert fake_client.plugin.UnloadPlugin.called


def test_eth_commands_call_expected_methods() -> None:
    fake_client = _fake_client()
    with patch("boat_cli.main.BoAtClient", return_value=fake_client):
        assert runner.invoke(app, ["eth", "list-ifaces"]).exit_code == 0
        assert runner.invoke(app, [
            "eth", "send",
            "--iface", "veth0",
            "--payload", "DEADBEEF",
        ]).exit_code == 0
        assert runner.invoke(app, [
            "eth", "send",
            "--iface", "veth0",
            "--src", "AA:BB:CC:DD:EE:FF",
            "--dst", "11:22:33:44:55:66",
            "--ethertype", "0x0800",
            "--payload", "DEADBEEF",
        ]).exit_code == 0
        assert runner.invoke(app, ["eth", "subscribe"]).exit_code == 0
        assert runner.invoke(app, ["eth", "subscribe", "--iface", "veth0", "--ethertype", "0x0800"]).exit_code == 0

    assert fake_client.ethernet.ListInterfaces.called
    assert fake_client.ethernet.SendFrame.called
    assert fake_client.ethernet.SubscribeFrames.called
