from __future__ import annotations

import typer

from boat.v1 import replay_pb2

from .output import print_table

replay_app = typer.Typer()


@replay_app.command("start")
def start_replay(ctx: typer.Context, trace: str = typer.Option(..., "--trace")) -> None:
    response = ctx.obj["client"].replay.StartReplay(replay_pb2.StartReplayRequest(trace_id=trace))
    print_table(["accepted"], [[bool(response.accepted)]], ctx.obj["json_mode"])


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
