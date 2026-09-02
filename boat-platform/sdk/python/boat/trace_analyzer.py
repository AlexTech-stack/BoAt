# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

"""BLF trace analyzer — reads BLF files and derives PDU database skeletons.

Usage::

    from boat.trace_analyzer import TraceAnalyzer

    analyzer = TraceAnalyzer("recordings/capture.blf")
    analyzer.analyze()

    pdu_db = analyzer.to_pdu_db(
        bus_mapping={1: "Powertrain_CAN", 2: "Body_CAN"},
        message_names={0x123: "EngineSpeed", 0x456: "CoolantTemp"},
    )

    import json
    print(json.dumps(pdu_db, indent=2))
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class TimingProfile:
    """How a message is scheduled, beyond a single cycle time.

    Many messages are not purely cyclic: they run at a base period and
    additionally transmit early when their content changes. Detecting that
    is what fills the PDU database's `CycleTimeFast`, `NrOfRepetitions` and
    per-signal `SigSendType`, none of which a single median gap can reach.
    """
    base_ms: float = 0.0
    fast_ms: float = 0.0
    repetitions: int = 0
    # Indices into payload_samples of frames that arrived early. Kept so a
    # caller can ask which *signal* changed on them -- that is exactly what
    # SigSendType means.
    early_indices: list[int] = field(default_factory=list)
    change_given_early: float = 0.0
    change_given_on_time: float = 0.0
    # Fraction of all frames whose payload differs from the one before.
    # Near 1.0 on a message with no stable period means it transmits
    # because something changed, not on a schedule.
    change_fraction: float = 0.0

    @property
    def has_fast_cycle(self) -> bool:
        return self.fast_ms > 0.0 and self.repetitions > 0


@dataclass
class TimingAnomaly:
    """A stretch of a capture where a message did not behave the way the
    rest of the capture says it should.

    Deliberately descriptive rather than accusatory: a burst is a burst,
    whether it came from a diagnostic session, a gateway hiccup or an
    injection. What it is for is narrowing a long capture down to the few
    seconds worth looking at.
    """
    kind: str            # "burst" | "gap" | "late_onset" | "early_offset" | "sporadic"
    start_ts: float
    end_ts: float
    frames: int = 0
    detail: str = ""


@dataclass
class CanIdStats:
    """Statistics for a single CAN ID observed in a trace."""
    channel: int
    arbitration_id: int
    is_extended: bool
    is_fd: bool
    count: int = 0
    dlc_values: list[int] = field(default_factory=list)
    payload_samples: list[bytes] = field(default_factory=list)
    timestamps: list[float] = field(default_factory=list)
    bit_changes: list[set[int]] = field(default_factory=list)
    # Other channel(s) this same arbitration ID was also observed on, with
    # their frame counts -- populated only when _resolve_multi_channel_ids()
    # decided this channel is the original source and the others are
    # gateway/relay duplicates excluded from cycle time and signal analysis.
    duplicate_channels: dict[int, int] = field(default_factory=dict)
    # Own cycle time, in ms. Only populated for routed copies (see
    # TraceAnalysis.routed_copies); a source message's cycle time lives in
    # TraceAnalysis.cycle_times_ms, keyed by CAN ID. A gateway is free to
    # forward at its own rate, so a copy's period is measured, never
    # inherited.
    cycle_time_ms: float = 0.0
    # Populated for every message by _detect_cycle_times().
    timing: "TimingProfile | None" = None


@dataclass
class TraceAnalysis:
    """Result of analyzing a BLF trace file."""
    path: str
    total_frames: int = 0
    unique_ids: int = 0
    channels: set[int] = field(default_factory=set)
    can_stats: dict[int, CanIdStats] = field(default_factory=dict)
    cycle_times_ms: dict[int, float] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    # Routed copies of a message, keyed by the CAN ID of the source that
    # stayed in `can_stats`. A message carried on more than one channel is
    # the same logical message relayed by a gateway, so only the source is
    # analysed and each copy inherits its layout -- see
    # _resolve_multi_channel_ids(). Copies are reported as messages in
    # their own right, with their own DbId, rather than folded into the
    # source.
    routed_copies: dict[int, list[CanIdStats]] = field(default_factory=dict)


class TraceAnalyzer:
    """Analyze a BLF trace file and produce PDU database skeletons."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._analysis: TraceAnalysis | None = None

    def analyze(self) -> TraceAnalysis:
        """Read the trace file and compute per-ID CAN statistics.

        Supports ``.blf``/``.asc`` (via python-can -- the same
        ``BLFReader``/``ASCReader`` classes ``trace_replay.py`` already uses
        for the same purpose), ``.log`` (candump/canutils text logs, parsed
        natively -- see :meth:`_read_candump`), ``.trace`` (the gateway's own binary format,
        via :meth:`TraceReplayer.parse_binary`), and ``.pcapng`` (via
        :mod:`boat.pcapng` -- a pcapng file may carry both CAN and Ethernet
        interfaces; only the CAN/CAN-FD records are analyzed here). Both
        ``.trace`` and ``.pcapng`` may contain non-CAN frames
        (Ethernet/TCP/PDU); those are counted and reported in
        ``analysis.errors``, not analyzed -- this tool is CAN-focused.
        ``.pcap`` (Ethernet-only) is rejected outright.

        A CAN ID observed on more than one channel is first tracked
        per-channel, then collapsed to a single entry via
        :meth:`_resolve_multi_channel_ids` -- see its docstring for why.
        """
        suffix = self._path.suffix.lower()
        analysis = TraceAnalysis(path=str(self._path))
        per_channel_stats: dict[tuple[int, int], CanIdStats] = {}

        if suffix in (".blf", ".asc"):
            self._read_python_can(suffix, analysis, per_channel_stats)
        elif suffix == ".log":
            self._read_candump(analysis, per_channel_stats)
        elif suffix == ".trace":
            self._read_trace_binary(analysis, per_channel_stats)
        elif suffix == ".pcapng":
            self._read_pcapng(analysis, per_channel_stats)
        elif suffix == ".pcap":
            raise ValueError(
                ".pcap captures are Ethernet-only and not analyzed by this CAN-focused tool"
            )
        else:
            raise ValueError(
                f"Unsupported format: {suffix} (expected .blf, .asc, .log, .trace, or .pcapng)"
            )

        analysis.can_stats = self._resolve_multi_channel_ids(per_channel_stats, analysis)
        analysis.unique_ids = len(analysis.can_stats)
        self._analysis = analysis

        self._detect_cycle_times(analysis)
        self._compute_bit_liveness(analysis)
        return analysis

    def _read_python_can(
        self, suffix: str, analysis: TraceAnalysis, stats: dict[tuple[int, int], CanIdStats]
    ) -> None:
        import can as python_can

        reader_cls = python_can.BLFReader if suffix == ".blf" else python_can.ASCReader
        reader = reader_cls(str(self._path))
        with reader:
            for msg in reader:
                analysis.total_frames += 1
                aid = msg.arbitration_id
                ch = getattr(msg, "channel", 1) or 1
                key = (aid, ch)
                if key not in stats:
                    stats[key] = CanIdStats(
                        channel=ch,
                        arbitration_id=aid,
                        is_extended=getattr(msg, "is_extended_id", False),
                        is_fd=getattr(msg, "is_fd", False),
                    )
                s = stats[key]
                s.count += 1
                s.dlc_values.append(len(msg.data))
                s.payload_samples.append(bytes(msg.data))
                s.timestamps.append(msg.timestamp)
                analysis.channels.add(ch)

    def _read_candump(
        self, analysis: TraceAnalysis, stats: dict[tuple[int, int], CanIdStats]
    ) -> None:
        """Read a candump/canutils text log -- what ``candump -l`` writes and
        ``canplayer`` replays::

            (1508687283.891357) slcan0 12E#C680027FD0FFFF00

        Three whitespace-separated fields: an absolute epoch timestamp in
        parentheses, the capture interface, and ``ID#DATA``. Parsed here
        rather than via python-can's ``CanutilsLogReader`` so that ``.log``
        captures stay readable with no python-can installed -- unlike
        ``.blf``/``.asc``, which hard-require it. This is also why renaming a
        candump log to ``.asc`` does not work: Vector's ASC is an unrelated
        format (header block, per-frame direction/DLC tokens, timestamps
        relative to capture start), and ``ASCReader`` rejects these lines.

        Interface names map to channel numbers in order of first appearance,
        1-based -- the analyzer keys on an int channel, and starting at 1
        keeps the ``channel or 1`` fallbacks used by the other readers
        meaningful. candump writes 3 hex digits for a standard ID and 8 for
        an extended one, which is what `is_extended` is taken from.

        Frames carrying no analyzable payload are counted and reported in
        ``analysis.errors`` rather than dropped silently: remote frames
        (``ID#R``), error frames (the ``CAN_ERR_FLAG`` bit set on an
        8-digit ID), and any line that does not parse at all.
        """
        CAN_ERR_FLAG = 0x20000000
        channel_of: dict[str, int] = {}
        no_payload = 0
        malformed = 0

        with self._path.open("r", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                analysis.total_frames += 1

                parts = line.split()
                if len(parts) != 3:
                    malformed += 1
                    continue
                ts_raw, iface, frame = parts

                if not (ts_raw.startswith("(") and ts_raw.endswith(")")):
                    malformed += 1
                    continue
                try:
                    timestamp = float(ts_raw[1:-1])
                except ValueError:
                    malformed += 1
                    continue

                id_str, sep, payload_str = frame.partition("#")
                if not sep or not id_str:
                    malformed += 1
                    continue

                # "ID##<flags><data>" is candump's CAN FD form; the single
                # nibble after the second '#' is the FD flags field (BRS/ESI),
                # not payload.
                is_fd = payload_str.startswith("#")
                if is_fd:
                    if len(payload_str) < 2:
                        malformed += 1
                        continue
                    payload_str = payload_str[2:]

                if payload_str[:1].upper() == "R":
                    no_payload += 1  # remote transmission request
                    continue

                try:
                    aid = int(id_str, 16)
                    data = bytes.fromhex(payload_str)
                except ValueError:
                    malformed += 1
                    continue

                is_extended = len(id_str) > 3
                if is_extended and aid & CAN_ERR_FLAG:
                    no_payload += 1  # error frame -- a bus event, not a message
                    continue

                ch = channel_of.setdefault(iface, len(channel_of) + 1)
                key = (aid, ch)
                if key not in stats:
                    stats[key] = CanIdStats(
                        channel=ch,
                        arbitration_id=aid,
                        is_extended=is_extended,
                        is_fd=is_fd,
                    )
                s = stats[key]
                s.count += 1
                s.dlc_values.append(len(data))
                s.payload_samples.append(data)
                s.timestamps.append(timestamp)
                analysis.channels.add(ch)

        if no_payload:
            analysis.errors.append(
                f"skipped {no_payload} remote/error frame(s) -- no payload to analyze"
            )
        if malformed:
            analysis.errors.append(
                f"skipped {malformed} unparseable line(s) -- not candump format"
            )
        if len(channel_of) > 1:
            names = ", ".join(f"{n}=channel {c}" for n, c in channel_of.items())
            analysis.errors.append(f"interface to channel mapping: {names}")

    def _read_trace_binary(
        self, analysis: TraceAnalysis, stats: dict[tuple[int, int], CanIdStats]
    ) -> None:
        from boat.trace_replay import TraceReplayer
        from boat.v1 import frame_pb2

        frames = TraceReplayer.parse_binary(self._path.read_bytes())
        skipped = 0
        for frame in frames:
            analysis.total_frames += 1
            if frame.bus_type not in (frame_pb2.Frame.CAN, frame_pb2.Frame.CANFD):
                skipped += 1
                continue
            aid = frame.can.can_id
            ch = frame.can.channel or 1
            key = (aid, ch)
            if key not in stats:
                stats[key] = CanIdStats(
                    channel=ch,
                    arbitration_id=aid,
                    is_extended=aid > 0x7FF,
                    is_fd=frame.bus_type == frame_pb2.Frame.CANFD,
                )
            s = stats[key]
            s.count += 1
            s.dlc_values.append(len(frame.payload))
            s.payload_samples.append(bytes(frame.payload))
            s.timestamps.append(frame.timestamp_ns / 1e9)  # ns -> seconds, matches python-can's convention
            analysis.channels.add(ch)
        if skipped:
            analysis.errors.append(
                f"skipped {skipped} non-CAN frame(s) (ETHERNET/TCP/PDU) -- not analyzed by this tool"
            )

    def _read_pcapng(
        self, analysis: TraceAnalysis, stats: dict[tuple[int, int], CanIdStats]
    ) -> None:
        from boat.pcapng import PcapngError, PcapngReader

        skipped = 0
        try:
            with PcapngReader(str(self._path)) as reader:
                for record in reader:
                    analysis.total_frames += 1
                    if hasattr(record, "ethertype"):
                        skipped += 1
                        continue
                    aid = record.arbitration_id
                    ch = record.channel or 1
                    key = (aid, ch)
                    if key not in stats:
                        stats[key] = CanIdStats(
                            channel=ch,
                            arbitration_id=aid,
                            is_extended=record.is_extended_id,
                            is_fd=record.is_fd,
                        )
                    s = stats[key]
                    s.count += 1
                    s.dlc_values.append(len(record.data))
                    s.payload_samples.append(bytes(record.data))
                    s.timestamps.append(record.timestamp)
                    analysis.channels.add(ch)
        except PcapngError as e:
            raise ValueError(f"Invalid pcapng file: {e}") from e
        if skipped:
            analysis.errors.append(
                f"skipped {skipped} non-CAN frame(s) (ETHERNET) -- not analyzed by this tool"
            )

    # ── Multi-channel duplicate resolution ──────────────────────────────

    @staticmethod
    def _resolve_multi_channel_ids(
        per_channel_stats: dict[tuple[int, int], CanIdStats],
        analysis: TraceAnalysis,
    ) -> dict[int, CanIdStats]:
        """Collapse per-(ID, channel) stats down to one entry per CAN ID.

        A CAN ID observed on only one channel passes through unchanged. An
        ID observed on multiple channels is assumed to be the same logical
        message relayed across buses (e.g. by a gateway ECU, sometimes at a
        slower or delayed cycle) -- :meth:`_select_original_channel` picks
        whichever channel's payload changes *lead* the others' as the
        original source, and only that channel's data is used for cycle
        time detection and signal reverse-engineering. The other channel(s)
        are recorded on the winner's `duplicate_channels` and reported as a
        warning, not silently merged or silently dropped.
        """
        by_id: dict[int, dict[int, CanIdStats]] = defaultdict(dict)
        for (aid, ch), s in per_channel_stats.items():
            by_id[aid][ch] = s

        resolved: dict[int, CanIdStats] = {}
        routed: dict[int, list[CanIdStats]] = {}
        duplicate_notes: list[str] = []
        repacked_notes: list[str] = []
        for aid, candidates in by_id.items():
            if len(candidates) == 1:
                ch, s = next(iter(candidates.items()))
                resolved[aid] = s
                continue

            winner_ch, winner_stats = TraceAnalyzer._select_original_channel(candidates)
            winner_stats.duplicate_channels = {
                ch: s.count for ch, s in candidates.items() if ch != winner_ch
            }
            resolved[aid] = winner_stats

            # A copy is paired with its source on CAN ID and payload
            # length. Payload equality is deliberately *not* required:
            # routing can be asynchronous, a gateway can drop or mangle
            # frames, and the capture itself can miss some -- none of which
            # stops the two being the same logical message. Differing
            # length does mean a repack, which is signal routing rather
            # than the 1:1 message routing handled here.
            source_len = max(winner_stats.dlc_values) if winner_stats.dlc_values else 0
            copies: list[CanIdStats] = []
            for ch, s in sorted(candidates.items()):
                if ch == winner_ch:
                    continue
                if (max(s.dlc_values) if s.dlc_values else -1) == source_len:
                    copies.append(s)
                else:
                    repacked_notes.append(f"0x{aid:X} ch{ch}")
            if copies:
                routed[aid] = copies
            duplicate_notes.append(f"0x{aid:X} (channel {winner_ch} selected)")

        if duplicate_notes:
            preview = ", ".join(duplicate_notes[:10])
            more = f", and {len(duplicate_notes) - 10} more" if len(duplicate_notes) > 10 else ""
            analysis.errors.append(
                f"{len(duplicate_notes)} CAN ID(s) seen on multiple channels -- the apparent "
                f"original channel was analysed for each and the rest recorded as routed "
                f"copies of it: {preview}{more}"
            )
        if repacked_notes:
            preview = ", ".join(repacked_notes[:10])
            more = f", and {len(repacked_notes) - 10} more" if len(repacked_notes) > 10 else ""
            analysis.errors.append(
                f"{len(repacked_notes)} copy/copies share a CAN ID with a source but not its "
                f"payload length, so they are repacked rather than 1:1 routed and are not "
                f"reported: {preview}{more}"
            )

        analysis.routed_copies = routed
        return resolved

    @staticmethod
    def _select_original_channel(
        candidates: dict[int, CanIdStats],
    ) -> tuple[int, CanIdStats]:
        """Among several channels carrying the same CAN ID, pick the one
        whose payload changes lead the others' -- the presumed original
        source, with the rest being a gateway/relay forwarding the same
        signal.

        For every payload value that changes on more than one channel,
        whichever channel's timestamp for that value is earliest scores a
        "lead" point against the others; the channel with the most lead
        points wins. If no comparable transitions exist at all (e.g. the
        channels' value sets never overlap, or every channel is constant),
        falls back to whichever channel has the most distinct value
        changes, then to the lowest channel number for determinism.
        """
        # Per-channel change events: (timestamp, payload) whenever payload
        # differs from the previous frame *on that channel*.
        change_events: dict[int, list[tuple[float, bytes]]] = {}
        for ch, s in candidates.items():
            events: list[tuple[float, bytes]] = []
            prev: bytes | None = None
            for ts, payload in zip(s.timestamps, s.payload_samples):
                if payload != prev:
                    events.append((ts, payload))
                    prev = payload
            change_events[ch] = events

        # Earliest time each payload value appeared as a change, per channel.
        first_seen: dict[bytes, dict[int, float]] = defaultdict(dict)
        for ch, events in change_events.items():
            for ts, payload in events:
                if ch not in first_seen[payload] or ts < first_seen[payload][ch]:
                    first_seen[payload][ch] = ts

        lead_score: dict[int, int] = {ch: 0 for ch in candidates}
        for payload, per_channel_ts in first_seen.items():
            if len(per_channel_ts) < 2:
                continue  # this value only appeared as a change on one channel -- no comparison possible
            leader = min(per_channel_ts, key=per_channel_ts.get)
            lead_score[leader] += 1

        if any(lead_score.values()):
            winner = max(candidates, key=lambda ch: (lead_score[ch], len(change_events[ch]), -ch))
        else:
            winner = max(candidates, key=lambda ch: (len(change_events[ch]), -ch))

        return winner, candidates[winner]

    # Frames a message needs before a period may be claimed for it.
    #
    # The median-plus-MAD test says nothing useful about three frames: two
    # gaps of roughly equal length always pass it, so any ID that happened
    # to appear three times acquired a "period". That is how four injected
    # IDs on a fuzzing capture escaped notice -- each was handed a 5.6 s
    # cycle from two gaps and thereby looked like an ordinary slow message
    # rather than the handful of stray frames it was. Nine gaps is still
    # modest: the slowest genuine message in these captures runs at 3 s and
    # appears 91 times.
    _MIN_FRAMES_FOR_PERIOD = 10

    @staticmethod
    def _cycle_time_of(s: CanIdStats) -> float:
        """Cycle time in ms for one message, or 0.0 if it is not periodic."""
        if s.count < TraceAnalyzer._MIN_FRAMES_FOR_PERIOD:
            return 0.0
        gaps = [
            (s.timestamps[i] - s.timestamps[i - 1]) * 1000.0
            for i in range(1, len(s.timestamps))
            if s.timestamps[i] > s.timestamps[i - 1]
        ]
        if not gaps:
            return 0.0
        median_gap = statistics.median(gaps)
        mad = statistics.median(abs(g - median_gap) for g in gaps)
        if mad >= median_gap * 0.3:
            return 0.0
        return TraceAnalyzer._snap_to_canonical_cycle_time(median_gap)

    # A message with no stable period whose payload changes this often is
    # transmitting because the content changed, not on a schedule. Well
    # below 1.0, because a sender that re-sends an unchanged value
    # occasionally is still event-driven.
    _ONCHANGE_MIN_CHANGE_FRACTION = 0.9

    # A frame counts as "early" when it arrives materially sooner than the
    # base period. 0.6 is loose enough to ignore ordinary arbitration
    # jitter and tight enough to catch a genuine fast-cycle transmission.
    _EARLY_GAP_FRACTION = 0.6

    # An early frame is only evidence of a change-triggered transmission if
    # it is far likelier to carry changed content than an on-time frame is.
    # Bus contention also delivers frames early, but with no such bias --
    # measured on real captures, on-time frames change on 0.7-1.8% while
    # early frames change on 48-65%, a thirty-fold difference, so these
    # bounds sit nowhere near either population.
    _CHANGE_ENRICHMENT_RATIO = 5.0
    _CHANGE_ENRICHMENT_FLOOR = 0.20
    _MIN_EARLY_FRAMES = 20

    # A run of frames arriving this many times faster than the message's
    # own period, for at least this many frames, is a burst. Well beyond
    # the fast-cycle factor (_EARLY_GAP_FRACTION), so an ordinary
    # change-triggered transmission is not mistaken for one.
    _BURST_RATE_FACTOR = 4.0
    _BURST_MIN_FRAMES = 8

    # A silence this many times the message's own period is a gap. A
    # cyclic message missing a couple of frames is normal; missing
    # hundreds is not.
    _GAP_FACTOR = 20.0

    # A message that starts this far into the capture, or stops this far
    # before the end, was not simply always there.
    _ONSET_FACTOR = 50.0

    # An ID with no established period and no more than this many frames
    # across the whole capture is barely present at all.
    #
    # Timing cannot see such an ID: too few frames to have a period, and
    # if its handful of appearances happen to straddle the capture it
    # triggers neither onset nor offset. Count does see it. On a Renault
    # Clio capture every legitimate ID appears between 91 and 27509 times
    # and every one of them is periodic, while injected IDs appear three
    # or four times with no schedule -- so the two populations are nowhere
    # near each other. Requiring *both* no period and a tiny count keeps
    # genuinely slow messages (one at 3 s, 91 frames, perfectly periodic)
    # out of it.
    _SPORADIC_MAX_FRAMES = 20

    @staticmethod
    def find_timing_anomalies(analysis: TraceAnalysis) -> dict[int, list[TimingAnomaly]]:
        """Per CAN ID, the stretches where its timing departs from its own
        established behaviour.

        Three shapes, each measured against the message's *own* period
        rather than any absolute threshold, so a 10 ms message and a 1 s
        message are judged on the same terms:

        - **burst** -- a sustained run far faster than its period.
        - **gap** -- a silence far longer than its period.
        - **late_onset / early_offset** -- the message is absent from the
          start or the end of the capture, so it is not simply part of the
          background traffic.
        - **sporadic** -- barely present at all: no period, and a handful
          of frames across the whole capture.

        A message with no established period is judged on the last two
        only; without a baseline there is nothing to call fast or slow.
        """
        starts = [s.timestamps[0] for s in analysis.can_stats.values() if s.timestamps]
        ends = [s.timestamps[-1] for s in analysis.can_stats.values() if s.timestamps]
        if not starts:
            return {}
        capture_start, capture_end = min(starts), max(ends)

        found: dict[int, list[TimingAnomaly]] = {}
        for aid, s in analysis.can_stats.items():
            # One frame is enough to examine. A single stray appearance is
            # the most sporadic thing a CAN ID can do, and skipping it was
            # how a one-packet injection went unnoticed.
            if not s.timestamps:
                continue
            base = s.timing.base_ms if s.timing else 0.0
            out: list[TimingAnomaly] = []

            if base:
                burst_cut = base / TraceAnalyzer._BURST_RATE_FACTOR
                gap_cut = base * TraceAnalyzer._GAP_FACTOR
                run_start, run_len = None, 0
                for i in range(1, len(s.timestamps)):
                    gap = (s.timestamps[i] - s.timestamps[i - 1]) * 1000.0
                    if 0 < gap < burst_cut:
                        if run_start is None:
                            run_start = s.timestamps[i - 1]
                        run_len += 1
                        continue
                    if run_len >= TraceAnalyzer._BURST_MIN_FRAMES:
                        out.append(TimingAnomaly(
                            "burst", run_start, s.timestamps[i - 1], run_len + 1,
                            f"{run_len + 1} frames faster than {burst_cut:.2f} ms "
                            f"(period {base:.1f} ms)",
                        ))
                    run_start, run_len = None, 0
                    if gap > gap_cut:
                        out.append(TimingAnomaly(
                            "gap", s.timestamps[i - 1], s.timestamps[i], 0,
                            f"silent for {gap / 1000.0:.2f} s (period {base:.1f} ms)",
                        ))
                if run_len >= TraceAnalyzer._BURST_MIN_FRAMES:
                    out.append(TimingAnomaly(
                        "burst", run_start, s.timestamps[-1], run_len + 1,
                        f"{run_len + 1} frames faster than {burst_cut:.2f} ms "
                        f"(period {base:.1f} ms)",
                    ))

            if not base and len(s.timestamps) <= TraceAnalyzer._SPORADIC_MAX_FRAMES:
                out.append(TimingAnomaly(
                    "sporadic", s.timestamps[0], s.timestamps[-1], len(s.timestamps),
                    f"{len(s.timestamps)} frame(s) in the whole capture, no period",
                ))

            margin = (base / 1000.0) * TraceAnalyzer._ONSET_FACTOR if base else 1.0
            if s.timestamps[0] > capture_start + margin:
                out.append(TimingAnomaly(
                    "late_onset", capture_start, s.timestamps[0], 0,
                    f"first seen {s.timestamps[0] - capture_start:.2f} s into the capture",
                ))
            if s.timestamps[-1] < capture_end - margin:
                out.append(TimingAnomaly(
                    "early_offset", s.timestamps[-1], capture_end, 0,
                    f"last seen {capture_end - s.timestamps[-1]:.2f} s before the end",
                ))
            if out:
                found[aid] = out
        return found

    @staticmethod
    def _profile_timing(s: CanIdStats) -> TimingProfile:
        """Base period, fast period, and whether early frames are actually
        driven by content changes. See the constants above."""
        profile = TimingProfile(base_ms=TraceAnalyzer._cycle_time_of(s))
        if len(s.payload_samples) >= 2:
            changed = sum(
                s.payload_samples[i] != s.payload_samples[i - 1]
                for i in range(1, len(s.payload_samples))
            )
            profile.change_fraction = changed / (len(s.payload_samples) - 1)
        base = profile.base_ms
        if not base or len(s.timestamps) < 3:
            return profile

        cutoff = base * TraceAnalyzer._EARLY_GAP_FRACTION
        early_gaps: list[float] = []
        early_changed = on_time = on_time_changed = 0
        run, runs = 0, []
        for i in range(1, len(s.timestamps)):
            gap = (s.timestamps[i] - s.timestamps[i - 1]) * 1000.0
            changed = (
                i < len(s.payload_samples)
                and s.payload_samples[i] != s.payload_samples[i - 1]
            )
            if 0 < gap < cutoff:
                profile.early_indices.append(i)
                early_gaps.append(gap)
                early_changed += changed
                run += 1
            else:
                on_time += 1
                on_time_changed += changed
                if run:
                    runs.append(run)
                    run = 0
        if run:
            runs.append(run)
        if not early_gaps:
            return profile

        profile.change_given_early = early_changed / len(early_gaps)
        profile.change_given_on_time = on_time_changed / on_time if on_time else 0.0

        enriched = (
            len(early_gaps) >= TraceAnalyzer._MIN_EARLY_FRAMES
            and profile.change_given_early >= TraceAnalyzer._CHANGE_ENRICHMENT_FLOOR
            and profile.change_given_early
            >= TraceAnalyzer._CHANGE_ENRICHMENT_RATIO * max(profile.change_given_on_time, 1e-9)
        )
        if enriched:
            profile.fast_ms = TraceAnalyzer._snap_to_canonical_cycle_time(
                statistics.median(early_gaps)
            )
            profile.repetitions = int(statistics.median(runs)) if runs else 0
        else:
            # Early frames with no bias toward changed content are jitter,
            # not a schedule -- the indices stay for inspection but nothing
            # is claimed.
            profile.early_indices.clear()
        return profile

    @staticmethod
    def _detect_cycle_times(analysis: TraceAnalysis) -> None:
        """Detect periodic messages by analyzing inter-message gaps.

        Routed copies are timed separately rather than inheriting their
        source's period: a gateway may forward at its own rate, and that
        difference is exactly what shows up as a counter advancing by more
        than one on the copy.
        """
        for aid, s in analysis.can_stats.items():
            s.timing = TraceAnalyzer._profile_timing(s)
            if s.timing.base_ms:
                analysis.cycle_times_ms[aid] = s.timing.base_ms
        for copies in analysis.routed_copies.values():
            for s in copies:
                s.timing = TraceAnalyzer._profile_timing(s)
                s.cycle_time_ms = s.timing.base_ms

    # Standard automotive scheduling raster values. A raw inter-frame gap is
    # noisy (arbitration delays, bus load, timer granularity), so a message
    # actually intended to run at e.g. 50ms typically measures as something
    # like 49.8 or 50.1ms in a real capture -- reporting that raw number
    # instead of the raster it's clearly jittering around is misleading.
    _CANONICAL_CYCLE_TIMES_MS = (
        1, 2, 5, 10, 20, 25, 40, 50, 80, 100, 200, 250, 400, 450, 500, 1000, 2000, 5000,
    )

    @staticmethod
    def _snap_to_canonical_cycle_time(value_ms: float, tolerance: float = 0.1) -> float:
        """Snap to the nearest standard cycle time if within `tolerance`
        (10% by default) of it; otherwise report the raw measured value
        unchanged, since forcing an unrelated gap onto a raster would be
        just as misleading as reporting jitter as if it were exact."""
        closest = min(TraceAnalyzer._CANONICAL_CYCLE_TIMES_MS, key=lambda c: abs(c - value_ms))
        if abs(closest - value_ms) <= closest * tolerance:
            return float(closest)
        return round(value_ms, 1)

    @staticmethod
    def _compute_bit_liveness(analysis: TraceAnalysis) -> None:
        """Track which bit positions ever change per CAN ID."""
        for aid, s in analysis.can_stats.items():
            if s.count < 2:
                continue
            max_len = max(len(p) for p in s.payload_samples)
            changed = [set() for _ in range(max_len)]
            prev_bytes = [None] * max_len
            for payload in s.payload_samples:
                for byte_idx in range(len(payload)):
                    b = payload[byte_idx]
                    if prev_bytes[byte_idx] is not None and b != prev_bytes[byte_idx]:
                        diff = b ^ prev_bytes[byte_idx]
                        for bit in range(8):
                            if diff & (1 << bit):
                                changed[byte_idx].add(bit)
                    prev_bytes[byte_idx] = b
            s.bit_changes = changed

    # ── PDU Database generation ─────────────────────────────────────────

    def to_pdu_db(
        self,
        bus_mapping: dict[int, str] | None = None,
        message_names: dict[int, str] | None = None,
        include_signals: bool = False,
    ) -> dict:
        """Derive a complete PDU database JSON dict from the trace analysis.

        Args:
            bus_mapping:  Map BLF channel number → bus name.
            message_names: Map CAN arbitration ID → message name.
            include_signals: If True, attempt signal discovery (requires
                           numpy and is experimental).

        Returns:
            A dict matching the PDU database schema (schema_version 1.0).
        """
        if self._analysis is None:
            raise RuntimeError("Call analyze() before to_pdu_db()")

        bus_mapping = bus_mapping or {}
        message_names = message_names or {}
        analysis = self._analysis

        messages: list[dict] = []
        next_db_id = 1

        for aid in sorted(analysis.can_stats.keys()):
            s = analysis.can_stats[aid]
            max_dlc = max(s.dlc_values) if s.dlc_values else 8

            msg: dict[str, Any] = {
                "DbId": next_db_id,
                "MessageName": message_names.get(aid, f"Msg_0x{aid:X}"),
                "Bus": bus_mapping.get(s.channel, f"CAN_{s.channel}"),
                "BusType": "CANFD" if s.is_fd else "CAN",
                "MessageType": 0,
                "Direction": 0,
                "RoutingType": 0,
                "TargetDbIds": None,
                "SourceDbId": None,
                "isE2E": 0,
                "SendType": "Cyclic" if aid in analysis.cycle_times_ms else "Spontaneous",
                "CycleTime": int(analysis.cycle_times_ms.get(aid, 0)),
                "CycleTimeFast": 0,
                "NrOfRepetitions": 0,
                "Identifier": aid & 0x1FFFFFFF,
                "FrameType": 1 if s.is_extended else 0,
                "Length": max_dlc,
                "BRS": s.is_fd,
                "signalcount": 0,
                "signals": [],
            }

            if include_signals:
                signals = self._derive_signals(s)
                msg["signals"] = signals
                msg["signalcount"] = len(signals)

            messages.append(msg)
            next_db_id += 1

        return {
            "schema_version": "1.0",
            "messages": messages,
            "signal_routes": [],
        }

    @staticmethod
    def _derive_signals(s: CanIdStats) -> list[dict]:
        """Basic signal derivation from payload samples (placeholder).

        This is intentionally minimal — the real reverse-engineering
        heuristics live in trace_reverse_engineer.py.
        """
        if not s.payload_samples:
            return []
        max_len = max(len(p) for p in s.payload_samples)
        signals = []
        if s.bit_changes and any(ch for ch in s.bit_changes):
            sig_id = 1
            pos = 0
            for byte_idx in range(max_len):
                changed = s.bit_changes[byte_idx] if byte_idx < len(s.bit_changes) else set()
                if not changed:
                    pos += 8
                    continue
                for bit in range(8):
                    if bit in changed:
                        signals.append({
                            "id": sig_id,
                            "SignalName": f"Signal_{sig_id}",
                            "Length": 1,
                            "StartPos": pos + bit,
                            "ByteOrder": 0,
                            "ValueType": "Unsigned",
                            "SigSendType": False,
                            "Repetitions": 0,
                            "InitValue": 0,
                            "Factor": 1.0,
                            "Offset": 0.0,
                            "Min": 0.0,
                            "Max": 1.0,
                            "Unit": "",
                            "EnumValues": None,
                    "IsMuxor": False,
                    "MuxValue": None,
                    "Comment": "",
                        })
                        sig_id += 1
                pos += 8
        return signals

    def save_pdu_db(self, path: str | Path, **kwargs) -> Path:
        """Analyze and save the derived PDU database directly to a JSON file.

        All extra keyword arguments are forwarded to :meth:`to_pdu_db`.
        """
        pdu_db = self.to_pdu_db(**kwargs)
        out = Path(path)
        out.write_text(json.dumps(pdu_db, indent=2))
        return out
