# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

"""boat trace — start / stop / list / replay trace recording sessions.

The start/stop/status commands communicate with the BoAt recorder daemon
(demo/recorder.py, default port 8083) rather than the gateway directly.
Start the recorder before using those commands.

The replay command reads a local .asc or .blf file and re-injects CAN
frames directly through the gateway via gRPC.

The score command is fully local (no gateway, no recorder): it rates a
trace file's information value for reverse engineering.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import typer

from .output import print_error, print_table

_ETHERTYPE_NAMES: dict[str, int] = {
    "ipv4": 0x0800, "ip": 0x0800,
    "arp": 0x0806,
    "ipv6": 0x86DD,
    "vlan": 0x8100,
    "boat": 0x88B5,
}

_PROTOCOL_NAMES: dict[str, int] = {
    "icmp": 1, "icmpv4": 1,
    "igmp": 2,
    "tcp": 6,
    "udp": 17,
    "ipv6": 41,
    "icmpv6": 58,
    "ospf": 89,
    "sctp": 132,
}


def _resolve_value(value: str, table: dict[str, int]) -> int:
    """Resolve a name or numeric value to an integer.

    Tries *table* lookup first, then hex (``0x...``), then decimal.
    """
    key = value.lower()
    if key in table:
        return table[key]
    if value.startswith("0x") or value.startswith("0X"):
        return int(value, 16)
    return int(value, 10)


trace_app = typer.Typer(help="Manage trace recording sessions and replay trace files.")

_DEFAULT_RECORDER = "http://localhost:8083"


def _client(recorder_url: str):
    """Return a TraceRecorder pointing at *recorder_url*."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "sdk" / "python"))
        from boat.trace_recorder import TraceRecorder
        return TraceRecorder(recorder_url=recorder_url)
    except ImportError as e:
        print_error(f"Cannot import boat SDK: {e}")
        raise typer.Exit(1)


def _die(msg: str) -> None:
    print_error(msg)
    raise typer.Exit(1)


# ── Commands ───────────────────────────────────────────────────────────────────

@trace_app.command("start")
def cmd_start(
    ctx:      typer.Context,
    fmt:      str  = typer.Option("asc",  "--format", "-f",
                        help="Output format: asc | blf | pcap | pcapng (pcapng recommended "
                             "for mixed CAN+Ethernet -- one file, multiple interfaces)"),
    buses:    str  = typer.Option("",     "--buses",  "-b",
                        help="Comma-separated CAN buses, e.g. vcan0,vcan1  (default: all)"),
    eth:      str  = typer.Option("",     "--eth",
                        help="Comma-separated Ethernet interfaces (pcap/pcapng only)"),
    signals:  bool = typer.Option(True,   "--signals/--no-signals",
                        help="Record BoAt bus signals to .jsonl sidecar"),
    output:   str  = typer.Option("traces", "--output", "-o",
                        help="Output directory for trace files"),
    name:     str  = typer.Option("",     "--name", "-n",
                        help="Optional session label"),
    recorder: str  = typer.Option(_DEFAULT_RECORDER, "--recorder",
                        help="Recorder daemon URL"),
) -> None:
    """Start a new recording session."""
    bus_list = [b.strip() for b in buses.split(",") if b.strip()] if buses else []
    eth_list = [e.strip() for e in eth.split(",")   if e.strip()] if eth   else []

    gateway = ctx.obj["host"] if ctx.obj else "localhost:50051"

    try:
        rec     = _client(recorder)
        rec.gateway = gateway
        session = rec.start(
            buses           = bus_list,
            eth_ifaces      = eth_list,
            include_signals = signals,
            fmt             = fmt,
            output_dir      = output,
            name            = name,
        )
    except Exception as e:
        _die(str(e))
        return

    files = ", ".join(f["name"] for f in session.get("files", []))
    print_table(
        ["session_id", "format", "buses", "signals", "files"],
        [[
            session["session_id"],
            session["format"],
            ", ".join(session["buses"]) or "all",
            str(session["include_signals"]),
            files or "(pending)",
        ]],
        ctx.obj.get("json_mode", False) if ctx.obj else False,
    )


@trace_app.command("stop")
def cmd_stop(
    ctx:        typer.Context,
    session_id: Optional[str] = typer.Argument(None,
                    help="Session ID to stop (omit to stop all running sessions)"),
    recorder:   str = typer.Option(_DEFAULT_RECORDER, "--recorder", help="Recorder daemon URL."),
) -> None:
    """Stop a recording session (or all sessions if no ID given)."""
    try:
        rec = _client(recorder)
        if session_id:
            result = rec.stop(session_id)
            rows   = [[result["session_id"], result["can_count"],
                       result["eth_count"],  result["sig_count"],
                       str(result.get("stopped_at", ""))]]
            headers = ["session_id", "can_frames", "eth_frames", "signals", "stopped_at"]
        else:
            result  = rec.stop_all()
            stopped = result.get("stopped", [])
            rows    = [[sid] for sid in stopped] or [["(none running)"]]
            headers = ["stopped_session_id"]
    except Exception as e:
        _die(str(e))
        return

    json_mode = ctx.obj.get("json_mode", False) if ctx.obj else False
    print_table(headers, rows, json_mode)


@trace_app.command("status")
def cmd_status(
    ctx:      typer.Context,
    recorder: str = typer.Option(_DEFAULT_RECORDER, "--recorder", help="Recorder daemon URL."),
) -> None:
    """Show all recording sessions (active and completed)."""
    try:
        sessions = _client(recorder).sessions()
    except Exception as e:
        _die(str(e))
        return

    if not sessions:
        typer.echo("No sessions recorded yet.")
        return

    rows = []
    for s in sessions:
        files = " ".join(f["name"] for f in s.get("files", []))
        rows.append([
            s["session_id"],
            s.get("name") or "—",
            s["format"],
            ", ".join(s["buses"]) or "all",
            "running" if s["running"] else "done",
            s["can_count"],
            s["sig_count"],
            files or "—",
        ])

    json_mode = ctx.obj.get("json_mode", False) if ctx.obj else False
    print_table(
        ["session_id", "name", "format", "buses", "status",
         "can_frames", "signals", "files"],
        rows,
        json_mode,
    )


@trace_app.command("replay")
def cmd_replay(
    ctx:     typer.Context,
    file:    Path = typer.Argument(..., help="Path to .asc or .blf CAN trace file, or a "
                                              ".pcapng file (only its CAN/CAN-FD records "
                                              "are replayed; any Ethernet records are "
                                              "skipped)"),
    buses:   str  = typer.Option("",    "--buses",  "-b",
                        help="Comma-separated CAN interfaces for channel mapping "
                             "(ch1->first, ch2->second, ...). Default: vcan0"),
    speed:   float = typer.Option(1.0,  "--speed",  "-s",
                        help="Playback speed multiplier (1.0=real-time, 0=max)"),
    loop:    Optional[int] = typer.Option(None, "--loop", "-l",
                        help="Loop the file with N ms gap between the last message of one "
                             "run and the first message of the next. Omit to replay once."),
    sim_id:  str   = typer.Option("",   "--sim-id",
                        help="Simulation ID forwarded with every frame"),
    verbose: bool  = typer.Option(False, "--verbose", "-v",
                        help="Print every frame as it is sent"),
    channel: int | None = typer.Option(None, "--channel", "-c",
                        help="Only replay frames from this CAN channel (1-based)"),
    can_id: str | None = typer.Option(None, "--id", "-i",
                        help="Only replay frames with this CAN ID (hex, e.g. 0x100). "
                             "Comma-separated for multiple IDs."),
) -> None:
    """Replay a CAN trace file (.asc, .blf, or the CAN records of a
    .pcapng) through the gateway in real time, sending each frame
    individually via gRPC.

    For Ethernet replay (.pcap, or the Ethernet records of a .pcapng), use
    `boat replay import` + `boat replay start`/`stream` instead -- this
    command supports CAN only.
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "sdk" / "python"))
        from boat.trace_replay import TraceReplayer, TraceReplayError
    except ImportError as e:
        print_error(f"Cannot import boat SDK: {e}")
        raise typer.Exit(1)

    file = file.resolve()
    if not file.exists():
        print_error(f"File not found: {file}")
        raise typer.Exit(1)
    if file.suffix.lower() == ".pcap":
        print_error(
            "boat trace replay only supports CAN traces (.asc/.blf/.pcapng). "
            "For Ethernet/pcap replay, use `boat replay import` + "
            "`boat replay start`/`stream` instead."
        )
        raise typer.Exit(1)

    bus_list = [b.strip() for b in buses.split(",") if b.strip()] if buses else []
    gateway  = ctx.obj["host"] if ctx.obj else "localhost:50051"
    id_set: set[int] | None = None
    if can_id:
        id_set = {int(s.strip(), 16) for s in can_id.split(",") if s.strip()}

    def _on_frame(idx: int, msg) -> None:
        if verbose:
            iface = bus_list[min(max(0, (getattr(msg, "channel", 1) or 1) - 1),
                                 len(bus_list) - 1)] if bus_list else "vcan0"
            typer.echo(
                f"[{idx:6d}] t={msg.timestamp:.6f}  "
                f"id=0x{msg.arbitration_id:08X}  "
                f"iface={iface}  "
                f"data={msg.data.hex()}"
            )

    replayer = TraceReplayer(
        gateway        = gateway,
        buses          = bus_list,
        speed          = speed,
        simulation_id  = sim_id,
        on_frame       = _on_frame if verbose else None,
        channel_filter = channel,
        id_filter      = id_set,
    )

    speed_label = f"{speed}x" if speed > 0 else "max"
    ch_label = f" ch={channel}" if channel is not None else ""
    id_label = f" id={[hex(i) for i in sorted(id_set)]}" if id_set else ""
    typer.echo(
        f"Replaying {file.name} -> {gateway}  "
        f"[speed={speed_label}  loop={loop or 'off'}{ch_label}{id_label}"
        f"  buses={bus_list or ['vcan0']}]"
    )

    try:
        total = replayer.replay(str(file), loop=loop)
    except TraceReplayError as e:
        print_error(str(e))
        raise typer.Exit(1)
    except KeyboardInterrupt:
        typer.echo("\nInterrupted.")
        raise typer.Exit(0)

    typer.echo(f"Done -- {total} frame(s) sent.")


@trace_app.command("score")
def cmd_score(
    ctx:  typer.Context,
    file: Path = typer.Argument(..., help="CAN trace to score (.blf, .asc, .log, "
                                          ".trace, or .pcapng)"),
    triage_only: bool = typer.Option(False, "--triage-only",
                        help="Stage 1 only: skip the reverse-engineering pass. "
                             "Fast, but bits stay unclassified, so no buckets, "
                             "audit, or grade."),
    mask: bool = typer.Option(True, "--mask/--no-mask",
                        help="Feed the stage-1 statistics back into the "
                             "reverse-engineering pass, so it skips payload "
                             "regions that cannot hold an application signal. "
                             "Use --no-mask to keep the stage-2 audit as an "
                             "independent measurement of the RE pass."),
    top: int = typer.Option(15, "--top", "-n",
                        help="Number of message rows to print, busiest-entropy "
                             "first (0 = all). Ignored with --json, which always "
                             "carries every message."),
) -> None:
    """Score a trace's information value for reverse engineering.

    Runs the two-stage information analysis: stage 1 (triage) rates the
    capture and decides whether it is worth the expensive
    reverse-engineering pass at all; stage 2 then runs that pass,
    classifies every payload bit into entropy buckets (const / derived /
    seq / signal / residual), audits the claimed signals against their own
    statistics, and grades the capture for structure extraction. If the
    stage-1 gate says the trace is not worth it, stage 2 is skipped and
    the reasons are printed. See backlog/trace_information_value.md for
    the concept and the measurements behind the thresholds.
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "sdk" / "python"))
        from boat.trace_analyzer import TraceAnalyzer
        from boat.trace_information import TraceInformationScorer
        from boat.trace_reverse_engineer import TraceReverseEngineer
    except ImportError as e:
        print_error(f"Cannot import boat SDK: {e}")
        raise typer.Exit(1)

    file = file.resolve()
    if not file.exists():
        _die(f"File not found: {file}")

    analyzer = TraceAnalyzer(file)
    try:
        analysis = analyzer.analyze()
    except ValueError as e:
        _die(str(e))

    scorer = TraceInformationScorer(analyzer)
    profile = scorer.triage()

    staged = False
    masked = False
    if triage_only:
        pass
    elif not profile.worth_re_pass:
        typer.echo("stage-1 gate: not worth the reverse-engineering pass — "
                   "skipping stage 2", err=True)
    else:
        search_mask = scorer.search_mask(profile) if mask else None
        if search_mask:
            typer.echo(
                f"mask: skipping {search_mask.masked_bits} bits across "
                f"{len(search_mask.excluded_bits)} messages "
                f"({len(search_mask.skip_ids)} skipped entirely)", err=True)
        # Always on: a necessary condition, so it cannot change the result,
        # only how fast the counter scan reaches it.
        hints = scorer.counter_lsb_hints(profile)
        typer.echo("running reverse-engineering pass (this can take a while "
                   "on large captures)...", err=True)
        result = TraceReverseEngineer(
            analyzer, search_mask=search_mask, counter_lsb_hints=hints
        ).reverse_engineer()
        profile = scorer.classify(result, profile)
        staged = True
        masked = search_mask is not None

    json_mode = ctx.obj.get("json_mode", False) if ctx.obj else False
    if json_mode:
        payload = _score_to_dict(profile, stage=2 if staged else 1)
        payload["search_mask_applied"] = masked
        typer.echo(json.dumps(payload))
        return

    _print_score(profile, analysis.errors, staged, top, masked)


def _score_to_dict(profile, stage: int) -> dict:
    """Flatten a TraceInformationProfile for --json, without per-bit detail
    (a CAN FD capture carries hundreds of thousands of bits)."""
    messages = []
    for m in sorted(profile.messages.values(), key=lambda m: -m.h_marginal):
        messages.append({
            "can_id": f"0x{m.can_id:X}",
            "channel": m.channel,
            "count": m.count,
            "is_fd": m.is_fd,
            "is_extended": m.is_extended,
            "lengths": {str(k): v for k, v in m.lengths.items()},
            "width_bits": m.width_bits,
            "live_bits": m.live_bits,
            "live_bits_steady": m.live_bits_steady,
            "h_marginal": round(m.h_marginal, 3),
            "h_conditional": round(m.h_conditional, 3),
            "mutual_information": round(m.mutual_information, 3),
            "distinct_payloads": m.distinct_payloads,
            "payload_saturation": round(m.payload_saturation, 3),
            "saturation_verdict": m.saturation_verdict,
            "under_sampled": m.under_sampled,
            "frames_per_second": round(m.frames_per_second, 1),
            "innovation_bits_per_s": round(m.innovation_bits_per_s, 1),
            "net_innovation_bits_per_s": (
                round(m.net_innovation_bits_per_s, 1)
                if m.net_innovation_bits_per_s is not None else None),
            "bucket_bits": m.bucket_bits,
            "opaque_tail_bytes": m.opaque_tail_bytes,
            "suspect_signals": m.suspect_signals,
            "flags": m.flags,
        })
    return {
        "path": profile.path,
        "stage": stage,
        "total_frames": profile.total_frames,
        "duration_s": round(profile.duration_s, 3),
        "unique_ids": profile.unique_ids,
        "channels": sorted(profile.channels),
        "staged_discovery": profile.staged_discovery,
        "phases": [{"label": p.label, "start_s": round(p.start_s, 3),
                    "end_s": round(p.end_s, 3),
                    "ids_first_seen": p.ids_first_seen}
                   for p in profile.phases],
        "components": {
            "breadth": round(profile.breadth, 3),
            "excitation": round(profile.excitation, 3),
            "sufficiency": round(profile.sufficiency, 3),
            "explainability": _round_opt(profile.explainability),
            "redundancy": _round_opt(profile.redundancy),
            "opacity": _round_opt(profile.opacity),
            "signal_share": _round_opt(profile.signal_share),
            "grade": _round_opt(profile.grade),
        },
        "gross_innovation_bits_per_s": round(profile.gross_innovation_bits_per_s, 1),
        "net_innovation_bits_per_s": _round_opt(profile.net_innovation_bits_per_s, 1),
        "worth_re_pass": profile.worth_re_pass,
        "reasons": profile.reasons,
        "actions": profile.actions,
        "suspect_signals": profile.suspect_signals,
        "messages": messages,
    }


def _round_opt(v: float | None, digits: int = 3) -> float | None:
    return round(v, digits) if v is not None else None


def _print_score(profile, errors: list[str], staged: bool, top: int,
                 masked: bool) -> None:
    phases = ", ".join(
        f"{p.label} {p.start_s:.1f}-{p.end_s:.1f}s" for p in profile.phases
    )
    typer.echo(f"{profile.path}")
    typer.echo(
        f"  {profile.total_frames} frames, {profile.duration_s:.1f}s, "
        f"{profile.unique_ids} IDs, phases: {phases}"
    )
    typer.echo(
        f"  breadth={profile.breadth:.2f}  excitation={profile.excitation:.2f}  "
        f"sufficiency={profile.sufficiency:.2f}"
    )
    if staged:
        typer.echo(
            f"  explainability={profile.explainability:.2f}  "
            f"redundancy={profile.redundancy:.2f}  "
            f"opacity={profile.opacity:.2f}  "
            f"signal_share={profile.signal_share:.2f}"
        )
        typer.echo(
            f"  innovation: {profile.gross_innovation_bits_per_s:.0f} bits/s gross, "
            f"{profile.net_innovation_bits_per_s:.0f} bits/s net "
            f"(after counter/CRC/audit subtraction)"
        )
        typer.echo(f"  grade: {profile.grade:.2f}")
    else:
        typer.echo(
            f"  innovation: {profile.gross_innovation_bits_per_s:.0f} bits/s "
            f"gross (stage 1 — before counter/CRC subtraction)"
        )
    verdict = "worth the RE pass" if profile.worth_re_pass else "NOT worth the RE pass"
    typer.echo(f"  stage-1 gate: {verdict}")
    if masked:
        typer.echo("  search mask: applied (audit is not independent "
                   "evidence here — rerun with --no-mask to measure the "
                   "RE pass itself)")
    for r in profile.reasons:
        typer.echo(f"    {r}")
    if profile.actions:
        typer.echo("  actions:")
        for a in profile.actions:
            typer.echo(f"    - {a}")
    for e in errors:
        typer.echo(f"  note: {e}")

    msgs = sorted(profile.messages.values(), key=lambda m: -m.h_marginal)
    if top > 0:
        msgs = msgs[:top]
    if staged:
        headers = ["ID", "N", "live/width", "Hmarg", "Hcond", "MI",
                   "sig", "der", "seq", "res", "net b/s", "verdict", "flags"]
        rows = [[
            f"0x{m.can_id:X}", m.count, f"{m.live_bits}/{m.width_bits}",
            f"{m.h_marginal:.1f}", f"{m.h_conditional:.1f}",
            f"{m.mutual_information:.1f}",
            (m.bucket_bits or {}).get("signal", 0),
            (m.bucket_bits or {}).get("derived", 0),
            (m.bucket_bits or {}).get("seq", 0),
            (m.bucket_bits or {}).get("residual", 0),
            f"{m.net_innovation_bits_per_s:.0f}"
            if m.net_innovation_bits_per_s is not None else "-",
            m.saturation_verdict, ",".join(m.flags),
        ] for m in msgs]
    else:
        headers = ["ID", "N", "live/width", "Hmarg", "Hcond", "MI",
                   "distinct", "sat", "verdict", "flags"]
        rows = [[
            f"0x{m.can_id:X}", m.count, f"{m.live_bits}/{m.width_bits}",
            f"{m.h_marginal:.1f}", f"{m.h_conditional:.1f}",
            f"{m.mutual_information:.1f}", m.distinct_payloads,
            f"{m.payload_saturation:.2f}", m.saturation_verdict,
            ",".join(m.flags),
        ] for m in msgs]
    print_table(headers, rows, json_mode=False)
    if top > 0 and len(profile.messages) > top:
        typer.echo(f"  ({len(profile.messages) - top} more messages — "
                   f"use --top 0 to show all, or --json)")
