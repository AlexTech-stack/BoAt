from __future__ import annotations

import typer

from boat.v1 import replay_pb2

from .output import print_table

replay_app = typer.Typer()

_SPEED_MAP = {
    "real-time":   replay_pb2.REPLAY_SPEED_REAL_TIME,
    "accelerated": replay_pb2.REPLAY_SPEED_ACCELERATED,
    "step":        replay_pb2.REPLAY_SPEED_STEP_BY_STEP,
}


@replay_app.command("start")
def start_replay(
    ctx: typer.Context,
    trace: str = typer.Option(..., "--trace"),
    speed: str = typer.Option("real-time", "--speed", "-s",
                              help="Replay speed: real-time, accelerated, step"),
    multiplier: float = typer.Option(1.0, "--multiplier", "-m",
                                     help="Speed multiplier (>0). 2.0 = twice as fast."),
    sim_id: str = typer.Option("", "--sim-id", help="Simulation ID"),
) -> None:
    proto_speed = _SPEED_MAP.get(speed, replay_pb2.REPLAY_SPEED_REAL_TIME)
    response = ctx.obj["client"].replay.StartReplay(
        replay_pb2.StartReplayRequest(
            trace_id=trace,
            simulation_id=sim_id,
            speed=proto_speed,
            speed_multiplier=multiplier,
        )
    )
    print_table(["accepted", "replay_id"], [[bool(response.accepted), response.replay_id]], ctx.obj["json_mode"])


@replay_app.command("seek")
def seek_replay(ctx: typer.Context, tick: int = typer.Option(..., "--tick"), replay_id: str = "") -> None:
    response = ctx.obj["client"].replay.SeekReplay(
        replay_pb2.SeekReplayRequest(replay_id=replay_id, tick=tick)
    )
    print_table(["accepted"], [[bool(response.accepted)]], ctx.obj["json_mode"])


@replay_app.command("stream")
def stream_replay(ctx: typer.Context, replay_id: str = "") -> None:
    stream = ctx.obj["client"].replay.StreamReplay(replay_pb2.StreamReplayRequest(replay_id=replay_id))
    for item in stream:
        print_table(["replay_id", "tick", "payload"], [[item.replay_id, item.tick, item.payload]], ctx.obj["json_mode"])


@replay_app.command("pause")
def pause_replay(ctx: typer.Context, replay_id: str = "") -> None:
    response = ctx.obj["client"].replay.PauseReplay(
        replay_pb2.PauseReplayRequest(replay_id=replay_id)
    )
    print_table(["accepted"], [[bool(response.accepted)]], ctx.obj["json_mode"])


@replay_app.command("resume")
def resume_replay(ctx: typer.Context, replay_id: str = "") -> None:
    response = ctx.obj["client"].replay.ResumeReplay(
        replay_pb2.ResumeReplayRequest(replay_id=replay_id)
    )
    print_table(["accepted"], [[bool(response.accepted)]], ctx.obj["json_mode"])


@replay_app.command("stop")
def stop_replay(ctx: typer.Context, replay_id: str = "") -> None:
    response = ctx.obj["client"].replay.StopReplay(
        replay_pb2.StopReplayRequest(replay_id=replay_id)
    )
    print_table(["accepted"], [[bool(response.accepted)]], ctx.obj["json_mode"])


@replay_app.command("from-events")
def start_replay_from_events(
    ctx: typer.Context,
    sim_id: str = typer.Option(..., "--sim-id", help="Simulation ID to replay events from"),
    signal_id: str = typer.Option("", "--signal-id", help="Filter by signal ID"),
    tick_min: int = typer.Option(0, "--tick-min", help="Minimum tick"),
    tick_max: int = typer.Option(0, "--tick-max", help="Maximum tick"),
    speed: str = typer.Option("real-time", "--speed", "-s",
                              help="Replay speed: real-time, accelerated, step"),
    multiplier: float = typer.Option(1.0, "--multiplier", "-m",
                                     help="Speed multiplier (>0). 2.0 = twice as fast."),
) -> None:
    proto_speed = _SPEED_MAP.get(speed, replay_pb2.REPLAY_SPEED_REAL_TIME)
    response = ctx.obj["client"].replay.StartReplayFromEvents(
        replay_pb2.StartReplayFromEventsRequest(
            simulation_id=sim_id,
            signal_id=signal_id,
            tick_min=tick_min,
            tick_max=tick_max,
            speed=proto_speed,
            speed_multiplier=multiplier,
        )
    )
    print_table(["accepted", "replay_id"], [[bool(response.accepted), response.replay_id]], ctx.obj["json_mode"])
