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


class TraceAnalyzer:
    """Analyze a BLF trace file and produce PDU database skeletons."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._analysis: TraceAnalysis | None = None

    def analyze(self) -> TraceAnalysis:
        """Read the trace file and compute per-ID CAN statistics.

        Supports ``.blf``/``.asc`` (via python-can -- the same
        ``BLFReader``/``ASCReader`` classes ``trace_replay.py`` already uses
        for the same purpose) and ``.trace`` (the gateway's own binary
        format, via :meth:`TraceReplayer.parse_binary`). ``.trace`` files
        may contain non-CAN frames (Ethernet/TCP/PDU); those are counted and
        reported in ``analysis.errors``, not analyzed -- this tool is
        CAN-focused. ``.pcap`` (Ethernet-only) is rejected outright.
        """
        suffix = self._path.suffix.lower()
        analysis = TraceAnalysis(path=str(self._path))
        stats: dict[int, CanIdStats] = {}

        if suffix in (".blf", ".asc"):
            self._read_python_can(suffix, analysis, stats)
        elif suffix == ".trace":
            self._read_trace_binary(analysis, stats)
        elif suffix == ".pcap":
            raise ValueError(
                ".pcap captures are Ethernet-only and not analyzed by this CAN-focused tool"
            )
        else:
            raise ValueError(f"Unsupported format: {suffix} (expected .blf, .asc, or .trace)")

        analysis.can_stats = stats
        analysis.unique_ids = len(stats)
        self._analysis = analysis

        self._detect_cycle_times(analysis)
        self._compute_bit_liveness(analysis)
        return analysis

    def _read_python_can(
        self, suffix: str, analysis: TraceAnalysis, stats: dict[int, CanIdStats]
    ) -> None:
        import can as python_can

        reader_cls = python_can.BLFReader if suffix == ".blf" else python_can.ASCReader
        reader = reader_cls(str(self._path))
        with reader:
            for msg in reader:
                analysis.total_frames += 1
                aid = msg.arbitration_id
                ch = getattr(msg, "channel", 1) or 1
                if aid not in stats:
                    stats[aid] = CanIdStats(
                        channel=ch,
                        arbitration_id=aid,
                        is_extended=getattr(msg, "is_extended_id", False),
                        is_fd=getattr(msg, "is_fd", False),
                    )
                s = stats[aid]
                s.count += 1
                s.dlc_values.append(len(msg.data))
                s.payload_samples.append(bytes(msg.data))
                s.timestamps.append(msg.timestamp)
                analysis.channels.add(ch)

    def _read_trace_binary(
        self, analysis: TraceAnalysis, stats: dict[int, CanIdStats]
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
            if aid not in stats:
                stats[aid] = CanIdStats(
                    channel=ch,
                    arbitration_id=aid,
                    is_extended=aid > 0x7FF,
                    is_fd=frame.bus_type == frame_pb2.Frame.CANFD,
                )
            s = stats[aid]
            s.count += 1
            s.dlc_values.append(len(frame.payload))
            s.payload_samples.append(bytes(frame.payload))
            s.timestamps.append(frame.timestamp_ns / 1e9)  # ns -> seconds, matches python-can's convention
            analysis.channels.add(ch)
        if skipped:
            analysis.errors.append(
                f"skipped {skipped} non-CAN frame(s) (ETHERNET/TCP/PDU) -- not analyzed by this tool"
            )

    @staticmethod
    def _detect_cycle_times(analysis: TraceAnalysis) -> None:
        """Detect periodic messages by analyzing inter-message gaps."""
        for aid, s in analysis.can_stats.items():
            if s.count < 3:
                continue
            gaps = []
            for i in range(1, len(s.timestamps)):
                gap = (s.timestamps[i] - s.timestamps[i - 1]) * 1000.0
                if gap > 0:
                    gaps.append(gap)
            if not gaps:
                continue

            median_gap = statistics.median(gaps)
            mad = statistics.median(abs(g - median_gap) for g in gaps)
            if mad < median_gap * 0.3:
                analysis.cycle_times_ms[aid] = TraceAnalyzer._snap_to_canonical_cycle_time(median_gap)

    # Standard automotive scheduling raster values. A raw inter-frame gap is
    # noisy (arbitration delays, bus load, timer granularity), so a message
    # actually intended to run at e.g. 50ms typically measures as something
    # like 49.8 or 50.1ms in a real capture -- reporting that raw number
    # instead of the raster it's clearly jittering around is misleading.
    _CANONICAL_CYCLE_TIMES_MS = (
        1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000,
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
