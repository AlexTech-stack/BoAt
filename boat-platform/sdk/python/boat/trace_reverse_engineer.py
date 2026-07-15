"""Reverse engineering engine for CAN trace signal discovery.

Builds on :mod:`boat.trace_analyzer` to discover signal boundaries,
value types, scaling factors, enumerations, counters, and checksums
from raw CAN payload observations.

Usage::

    from boat.trace_analyzer import TraceAnalyzer
    from boat.trace_reverse_engineer import TraceReverseEngineer

    analyzer = TraceAnalyzer("trace.blf")
    analyzer.analyze()

    engineer = TraceReverseEngineer(analyzer)
    results = engineer.reverse_engineer()
"""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from boat.trace_analyzer import CanIdStats, TraceAnalysis, TraceAnalyzer

_HAS_NUMPY = False
try:
    import numpy as np

    _HAS_NUMPY = True
except ImportError:
    np = None  # type: ignore[assignment]


@dataclass
class DiscoveredSignal:
    """A signal discovered through trace reverse engineering."""
    id: int
    name: str
    start_pos: int
    length: int
    byte_order: int
    value_type: str
    factor: float
    offset: float
    min_val: float
    max_val: float
    unit: str
    enum_values: dict[str, str] | None
    is_counter: bool
    is_checksum: bool
    confidence: float
    raw_values: list[int] = field(default_factory=list)
    physical_values: list[float] = field(default_factory=list)


@dataclass
class ReverseEngineeredMessage:
    """A message with reverse-engineered signals."""
    can_id: int
    channel: int
    db_id: int
    message_name: str
    bus: str
    bus_type: str
    identifier: int
    is_extended: bool
    is_fd: bool
    length: int
    cycle_time_ms: float
    send_type: str
    signals: list[DiscoveredSignal] = field(default_factory=list)


@dataclass
class ReverseEngineeringResult:
    """Full reverse engineering result."""
    messages: list[ReverseEngineeredMessage] = field(default_factory=list)
    total_can_ids: int = 0
    total_signals_discovered: int = 0
    numpy_available: bool = False


class TraceReverseEngineer:
    """Reverse-engineer signal definitions from CAN trace data.

    Args:
        analyzer: A :class:`~boat.trace_analyzer.TraceAnalyzer` instance
                  that has already been run via ``analyze()``.
        min_confidence: Minimum confidence (0-1) to include a signal.
    """

    def __init__(
        self,
        analyzer: TraceAnalyzer,
        min_confidence: float = 0.3,
    ) -> None:
        self._analyzer = analyzer
        self._analysis: TraceAnalysis | None = analyzer._analysis
        self._min_confidence = min_confidence

    def reverse_engineer(self) -> ReverseEngineeringResult:
        """Run the full reverse engineering pipeline."""
        if self._analysis is None:
            raise RuntimeError("Call analyzer.analyze() before reverse_engineer()")

        result = ReverseEngineeringResult(
            numpy_available=_HAS_NUMPY,
        )

        for aid in sorted(self._analysis.can_stats.keys()):
            s = self._analysis.can_stats[aid]

            # Bit-correlation clustering needs >=2 samples to mean anything,
            # but a CAN ID observed only once is still a real message that
            # belongs in the export -- only the signal *discovery* is
            # skipped, not the message itself (matches TraceAnalyzer.to_pdu_db(),
            # which includes every observed CAN ID regardless of count).
            discovered_signals: list[DiscoveredSignal] = []
            if s.count >= 2:
                discovered_signals = self._analyze_can_id(s)
                discovered_signals = [
                    sig for sig in discovered_signals
                    if sig.confidence >= self._min_confidence
                ]

            length = max(s.dlc_values) if s.dlc_values else 8
            cycle_ms = self._analysis.cycle_times_ms.get(aid, 0)

            msg = ReverseEngineeredMessage(
                can_id=aid,
                channel=s.channel,
                db_id=aid + 1,
                message_name=f"Msg_0x{aid:X}",
                bus=f"CAN_{s.channel}",
                bus_type="CANFD" if s.is_fd else "CAN",
                identifier=aid & 0x1FFFFFFF,
                is_extended=s.is_extended,
                is_fd=s.is_fd,
                length=length,
                cycle_time_ms=cycle_ms,
                send_type="Cyclic" if cycle_ms > 0 else "Spontaneous",
                signals=discovered_signals,
            )
            result.messages.append(msg)
            result.total_signals_discovered += len(discovered_signals)

        result.total_can_ids = len(result.messages)
        return result

    # ── Per-CAN-ID analysis pipeline ──────────────────────────────────

    def _analyze_can_id(self, stats: CanIdStats) -> list[DiscoveredSignal]:
        """Run the full signal analysis pipeline for a single CAN ID."""
        if not stats.payload_samples or stats.count < 2:
            return []

        bit_matrix = self._build_bit_matrix(stats)
        if bit_matrix is None or len(bit_matrix) < 2:
            return []

        clusters = self._cluster_correlated_bits(bit_matrix)

        grouped: dict[int, list[int]] = {}
        for bit_idx, cluster_id in clusters.items():
            grouped.setdefault(cluster_id, []).append(bit_idx)

        signals: list[DiscoveredSignal] = []
        sig_id = 1

        raw_values = self._compute_raw_values(stats)

        for cluster_id in sorted(grouped.keys()):
            if cluster_id == -1:
                continue  # "not active enough to cluster" bucket -- not a real signal group
            bits = sorted(grouped[cluster_id])
            if not bits:
                continue

            start_pos = min(bits)
            length = len(bits)

            contiguous = self._find_contiguous_groups(bits)
            if contiguous and len(contiguous) > 1:
                for group in contiguous:
                    sig = self._build_signal(
                        sig_id, group, raw_values, stats
                    )
                    if sig:
                        signals.append(sig)
                        sig_id += 1
            else:
                sig = self._build_signal(
                    sig_id, bits, raw_values, stats
                )
                if sig:
                    signals.append(sig)
                    sig_id += 1

        signals = self._post_process_signals(signals, stats)
        return signals

    # ── Bit matrix construction ───────────────────────────────────────

    @staticmethod
    def _build_bit_matrix(stats: CanIdStats) -> list[list[int]] | None:
        """Build a matrix: rows=frames, columns=bit_positions, values=0/1.

        Returns None if samples are too few or too short.
        """
        if not stats.payload_samples:
            return None
        max_len = max(len(p) for p in stats.payload_samples)
        if max_len == 0:
            return None

        matrix: list[list[int]] = []
        for payload in stats.payload_samples:
            row: list[int] = []
            for byte_idx in range(max_len):
                b = payload[byte_idx] if byte_idx < len(payload) else 0
                for bit in range(7, -1, -1):
                    row.append((b >> bit) & 1)
            matrix.append(row)
        return matrix

    # ── Bit correlation clustering ────────────────────────────────────

    @staticmethod
    def _cluster_correlated_bits(matrix: list[list[int]]) -> dict[int, int]:
        """Cluster bit positions by change correlation.

        Returns a dict mapping bit_position → cluster_id.
        """
        n_frames = len(matrix)
        n_bits = len(matrix[0])

        if n_frames <= 1:
            return {}

        if _HAS_NUMPY:
            return TraceReverseEngineer._cluster_numpy(matrix)
        else:
            return TraceReverseEngineer._cluster_pure_python(matrix, n_frames, n_bits)

    @staticmethod
    def _active_bit_indices(matrix: list[list[int]], min_minority_fraction: float = 0.05) -> list[int]:
        """Bit positions with enough real variability to be signal candidates.

        A bit that's constant, or that only flips a handful of times across
        the whole capture (a stray glitch, a rare fault bit, a reserved bit
        that happens to toggle once), isn't a meaningful signal. Require the
        minority value to occur at least `min_minority_fraction` of the time
        -- "changed at least once" (the old pure-Python filter) or "variance
        above a small fixed threshold" (the old numpy filter, which a single
        flip in a ~30-frame capture already clears) both let far too many
        near-constant bits through, showing up as a wall of trivial
        always-0-except-once Bool "signals". Shared by both clustering paths
        so results don't depend on whether numpy happens to be installed.
        """
        n_frames = len(matrix)
        if n_frames == 0:
            return []
        n_bits = len(matrix[0])
        active = []
        for bit in range(n_bits):
            ones = sum(row[bit] for row in matrix)
            minority = min(ones, n_frames - ones)
            if minority / n_frames >= min_minority_fraction:
                active.append(bit)
        return active

    @staticmethod
    def _cluster_numpy(matrix: list[list[int]]) -> dict[int, int]:
        arr = np.array(matrix, dtype=np.int8)
        n_bits = arr.shape[1]

        active = np.array(TraceReverseEngineer._active_bit_indices(matrix), dtype=np.int64)
        if len(active) < 2:
            cluster_of = {int(i): i for i in active}
            for i in range(n_bits):
                if i not in cluster_of:
                    cluster_of[i] = -1
            return cluster_of

        active_arr = arr[:, active]
        corr = np.corrcoef(active_arr.T)
        corr = np.nan_to_num(corr, nan=0.0)

        threshold = 0.6
        n_active = len(active)
        visited = set()
        cluster_of: dict[int, int] = {}
        next_cluster = 0

        for i in range(n_active):
            if i in visited:
                continue
            cluster_idx = active[i]
            cluster_of[int(cluster_idx)] = next_cluster
            visited.add(i)
            stack = [i]
            while stack:
                cur = stack.pop()
                for j in range(n_active):
                    if j not in visited and abs(corr[cur, j]) > threshold:
                        visited.add(j)
                        cluster_of[int(active[j])] = next_cluster
                        stack.append(j)
            next_cluster += 1

        for i in range(n_bits):
            if i not in cluster_of:
                cluster_of[i] = -1

        return cluster_of

    @staticmethod
    def _cluster_pure_python(
        matrix: list[list[int]], n_frames: int, n_bits: int
    ) -> dict[int, int]:
        """Fallback clustering without numpy using Jaccard similarity on bit
        transitions."""
        transitions: dict[int, list[int]] = {}
        for bit in range(n_bits):
            t = []
            prev = None
            for row in matrix:
                val = row[bit]
                if prev is not None and val != prev:
                    t.append(1)
                else:
                    t.append(0)
                prev = val
            transitions[bit] = t

        active_bits = TraceReverseEngineer._active_bit_indices(matrix)

        cluster_of: dict[int, int] = {}
        next_cluster = 0
        visited = set()

        def _jaccard(a: list[int], b: list[int]) -> float:
            and_count = sum(1 for i in range(len(a)) if a[i] and b[i])
            or_count = sum(1 for i in range(len(a)) if a[i] or b[i])
            return and_count / or_count if or_count > 0 else 0.0

        for bit in active_bits:
            if bit in visited:
                continue
            cluster_of[bit] = next_cluster
            visited.add(bit)
            stack = [bit]
            while stack:
                cur = stack.pop()
                for other in active_bits:
                    if other not in visited:
                        sim = _jaccard(transitions[cur], transitions[other])
                        if sim > 0.5:
                            visited.add(other)
                            cluster_of[other] = next_cluster
                            stack.append(other)
            next_cluster += 1

        for bit in range(n_bits):
            if bit not in cluster_of:
                cluster_of[bit] = -1

        return cluster_of

    # ── Contiguous group splitting ────────────────────────────────────

    @staticmethod
    def _find_contiguous_groups(bits: list[int]) -> list[list[int]]:
        """Split a sorted list of bit positions into contiguous groups."""
        if not bits:
            return []
        groups: list[list[int]] = []
        current = [bits[0]]
        for i in range(1, len(bits)):
            if bits[i] == bits[i - 1] + 1:
                current.append(bits[i])
            else:
                groups.append(current)
                current = [bits[i]]
        groups.append(current)
        return groups

    # ── Raw value computation ─────────────────────────────────────────

    @staticmethod
    def _compute_raw_values(stats: CanIdStats) -> list[dict[str, Any]]:
        """Compute raw values for each payload byte/position across samples."""
        max_len = max(len(p) for p in stats.payload_samples) if stats.payload_samples else 0
        pos_values: list[list[int]] = [[] for _ in range(max_len * 8)]

        for payload in stats.payload_samples:
            for byte_idx in range(len(payload)):
                b = payload[byte_idx]
                for bit in range(8):
                    pos = byte_idx * 8 + bit
                    pos_values[pos].append((b >> (7 - bit)) & 1)

        raw: list[dict[str, Any]] = []
        for pos, values in enumerate(pos_values):
            raw.append({
                "pos": pos,
                "byte": pos // 8,
                "bit": pos % 8,
                "values": values,
            })
        return raw

    # ── Signal building ───────────────────────────────────────────────

    def _build_signal(
        self,
        sig_id: int,
        bits: list[int],
        raw_values: list[dict[str, Any]],
        stats: CanIdStats,
    ) -> DiscoveredSignal | None:
        """Build a DiscoveredSignal from a group of related bit positions."""
        if not bits:
            return None

        start_pos = min(bits)
        length = len(bits)
        byte_order = self._detect_byte_order(bits, stats)
        raw_nums = self._extract_raw_numbers(
            bits, byte_order, stats
        )

        if not raw_nums:
            return None

        value_type, min_val, max_val, factor, offset, enum_vals, is_signed = (
            self._analyze_values(raw_nums, length)
        )

        is_counter = self._detect_counter(raw_nums, length)
        is_checksum = self._detect_checksum(raw_nums, stats, bits)
        confidence = self._compute_confidence(
            bits, raw_nums, stats, is_counter, is_checksum
        )

        if is_counter:
            # AUTOSAR counters are always unsigned, with a 1:1 raw<->physical
            # mapping across their full valid range (0..2^length-1) -- not
            # whatever range _analyze_values happened to observe in this
            # particular sample window, since a short capture may not have
            # caught the counter's full wrap cycle. Overrides whatever
            # _analyze_values guessed independently (e.g. "Bool" if every
            # sample so far happened to be 0 or 1).
            value_type = "Unsigned"
            is_signed = False
            factor = 1.0
            offset = 0.0
            min_val = 0.0
            max_val = float((1 << length) - 1)
            enum_vals = None

        # physical_values must use the same reference frame factor/offset
        # were derived from: the sign-converted numeric value for signed
        # signals, the raw bit pattern otherwise (always the latter for a
        # counter, since is_signed is forced False above).
        working_nums = (
            self._to_signed(raw_nums, length) if is_signed else raw_nums
        )

        return DiscoveredSignal(
            id=sig_id,
            name=f"Signal_{sig_id}",
            start_pos=start_pos,
            length=length,
            byte_order=byte_order,
            value_type=value_type,
            factor=factor,
            offset=offset,
            min_val=min_val,
            max_val=max_val,
            unit="",
            enum_values=enum_vals,
            is_counter=is_counter,
            is_checksum=is_checksum,
            confidence=confidence,
            raw_values=raw_nums[:100],
            physical_values=[v * factor + offset for v in working_nums[:100]],
        )

    # ── Byte order detection ──────────────────────────────────────────

    @staticmethod
    def _detect_byte_order(
        bits: list[int], stats: CanIdStats
    ) -> int:
        """Detect Intel (0) vs Motorola (1) byte order for a signal.

        Heuristic: if the signal spans multiple bytes, Intel tends
        to produce smoother (less jumpy) sequences than Motorola.
        """
        if not stats.payload_samples or len(bits) <= 8:
            return 0

        intel_raw = TraceReverseEngineer._extract_raw_numbers(
            bits, 0, stats
        )
        motorola_raw = TraceReverseEngineer._extract_raw_numbers(
            bits, 1, stats
        )

        if not intel_raw or not motorola_raw:
            return 0

        def _smoothness(values: list[int]) -> float:
            diffs = [abs(values[i] - values[i - 1]) for i in range(1, len(values))]
            return statistics.mean(diffs) if diffs else 0

        intel_jitter = _smoothness(intel_raw)
        motorola_jitter = _smoothness(motorola_raw)

        if motorola_jitter < intel_jitter * 0.7:
            return 1
        return 0

    # ── Raw number extraction ─────────────────────────────────────────

    @staticmethod
    def _extract_raw_numbers(
        bits: list[int],
        byte_order: int,
        stats: CanIdStats,
    ) -> list[int]:
        """Extract raw integer values for a signal from payload samples.

        Bits are indexed MSB-first (bit 0 = MSB of byte 0).

        Both byte orders build the value by shifting left and OR-ing in bits
        most-significant-first; they differ only in *which byte* is most
        significant -- a byte's own internal bit order (MSB-first) never
        flips. Motorola (big-endian): earlier byte = more significant, so
        ascending ``pos`` order (byte0 MSB..LSB, then byte1 MSB..LSB, ...)
        is already MSB-first end-to-end. Intel (little-endian): later byte
        = more significant, so bytes are visited highest-index-first, but
        each byte's own bits still go MSB-first within it.

        Args:
            bits: Bit positions (MSB-first indexing: bit 0 = byte 0 MSB).
            byte_order: 0=Intel (LSB first), 1=Motorola (MSB first).
            stats: CAN ID statistics with payload samples.

        Returns:
            List of raw integer values, one per frame.
        """
        if not stats.payload_samples:
            return []

        max_len = max(len(p) for p in stats.payload_samples)

        if byte_order == 0:
            ordered_bits = sorted(bits, key=lambda p: (-(p // 8), p))
        else:
            ordered_bits = sorted(bits)

        results: list[int] = []
        for payload in stats.payload_samples:
            padded = bytearray(payload) + b"\x00" * (max_len - len(payload))
            value = 0
            for bit_pos in ordered_bits:
                byte_idx = bit_pos // 8
                bit_idx = bit_pos % 8
                b = padded[byte_idx]
                value = (value << 1) | ((b >> (7 - bit_idx)) & 1)
            results.append(value)

        return results

    # ── Value analysis ────────────────────────────────────────────────

    @staticmethod
    def _to_signed(raw_values: list[int], length: int) -> list[int]:
        """Two's-complement conversion: a raw value >= 2^(length-1) is
        negative. Used so factor/offset/min/max and physical_values are all
        derived from the *same* numeric reference frame for signed signals,
        instead of mixing raw bit patterns with sign-converted numbers."""
        if length <= 0:
            return list(raw_values)
        sign_bit = 1 << (length - 1)
        modulus = 1 << length
        return [v - modulus if v >= sign_bit else v for v in raw_values]

    @staticmethod
    def _analyze_values(
        raw_values: list[int],
        length: int,
    ) -> tuple[str, float, float, float, float, dict[str, str] | None, bool]:
        """Determine value type, range, scaling, and enumerations.

        Returns (value_type, min_val, max_val, factor, offset, enum_vals, is_signed).
        """
        if not raw_values:
            return "Unsigned", 0.0, 1.0, 1.0, 0.0, None, False

        unique = list(set(raw_values))
        full_range = (1 << length) - 1

        enum_vals = None
        if len(unique) <= 8 and len(unique) <= full_range * 0.5:
            enum_vals = {}
            for i, v in enumerate(sorted(unique)):
                enum_vals[str(v)] = f"State_{i}"

        if length == 1:
            return "Bool", 0.0, 1.0, 1.0, 0.0, {"0": "False", "1": "True"}, False

        is_bool = all(v in (0, 1) for v in unique)
        if is_bool:
            return "Bool", 0.0, 1.0, 1.0, 0.0, {"0": "Off", "1": "On"}, False

        # A raw value >= 2^(length-1) uses the sign bit in two's complement,
        # but "any single value crosses the midpoint" is a weak signal on
        # its own -- an ordinary unsigned byte legitimately takes values
        # above 127 all the time. Require values comfortably on *both*
        # sides of the wrap (bottom and top quarter of the range) before
        # concluding the field is actually signed, not just numerically
        # large.
        sign_bit = 1 << (length - 1)
        low_threshold = sign_bit // 2
        high_threshold = sign_bit + sign_bit // 2
        is_signed = (
            any(v < low_threshold for v in unique)
            and any(v >= high_threshold for v in unique)
        )

        if length in (32, 64):
            try:
                import struct
                float_candidates: list[float] = []
                for rv in unique:
                    try:
                        if length == 32:
                            v = struct.unpack(">f", struct.pack(">I", rv))[0]
                        else:
                            v = struct.unpack(">d", struct.pack(">Q", rv))[0]
                        if not (math.isnan(v) or math.isinf(v)):
                            float_candidates.append(v)
                    except Exception:
                        pass
                if float_candidates and all(
                    abs(v - round(v)) > 0.001 for v in float_candidates
                ):
                    return "Float", min(float_candidates), max(float_candidates), 1.0, 0.0, None, False
            except Exception:
                pass

        # factor/offset/min/max are all derived from the same "working"
        # values: sign-converted for a signed signal, raw bit pattern
        # otherwise -- and phys_min/phys_max use the identical v*factor+offset
        # formula _build_signal uses for physical_values, so the reported
        # Min/Max always match what the sample data actually shows.
        working = TraceReverseEngineer._to_signed(raw_values, length) if is_signed else raw_values
        w_min, w_max = min(working), max(working)
        if w_max != w_min:
            factor = (w_max - w_min) / full_range
            if factor <= 0:
                factor = 1.0
        else:
            factor = 1.0

        offset = float(w_min)
        phys_min = w_min * factor + offset
        phys_max = w_max * factor + offset

        value_type = "Signed" if is_signed else "Unsigned"
        return value_type, float(phys_min), float(phys_max), float(factor), offset, enum_vals, is_signed

    # ── Counter detection ─────────────────────────────────────────────

    @staticmethod
    def _detect_counter(raw_values: list[int], length: int) -> bool:
        """Detect if signal is a rolling AUTOSAR-style counter.

        A counter increments by 1 each frame and wraps at its own bit
        width -- AUTOSAR counters are always unsigned and only ever 4, 8,
        or 32 bits wide (masking wraparound to a fixed 8 bits regardless of
        the signal's actual length would misjudge any other width's wrap
        transition as a huge jump instead of +1). Restricting to those
        three widths also rules out the degenerate 1-bit case, which is
        indistinguishable from a plain boolean toggle under modular
        arithmetic: incrementing by 1 mod 2 *is* flipping the bit, so an
        ordinary 0,1,0,1,... flag would otherwise trivially score as a
        "counter" too.
        """
        if length not in (4, 8, 32):
            return False
        if len(raw_values) < 5:
            return False

        mask = (1 << length) - 1
        diffs = [(raw_values[i] - raw_values[i - 1]) & mask for i in range(1, len(raw_values))]
        if not diffs:
            return False

        one_count = sum(1 for d in diffs if d == 1)
        return one_count / len(diffs) > 0.7

    # ── Checksum candidate detection ──────────────────────────────────

    @staticmethod
    def _detect_checksum(
        raw_values: list[int],
        stats: CanIdStats,
        signal_bits: list[int],
    ) -> bool:
        """Detect if signal might be a checksum (weak heuristic)."""
        if not stats.payload_samples or len(raw_values) < 3:
            return False

        if len(signal_bits) < 8:
            return False

        max_len = max(len(p) for p in stats.payload_samples) if stats.payload_samples else 0
        signal_bytes = {b // 8 for b in signal_bits}
        other_bytes = [i for i in range(max_len) if i not in signal_bytes]

        if not other_bytes:
            return False

        # Check if signal correlates with XOR of other bytes
        xor_correlation = 0
        for i, payload in enumerate(stats.payload_samples):
            if i >= len(raw_values):
                break
            others_xor = 0
            for byte_idx in other_bytes:
                if byte_idx < len(payload):
                    others_xor ^= payload[byte_idx]
            expected = others_xor & ((1 << len(signal_bits)) - 1)
            if expected == raw_values[i]:
                xor_correlation += 1

        if len(raw_values) > 0 and xor_correlation / len(raw_values) > 0.5:
            return True
        return False

    # ── Confidence computation ────────────────────────────────────────

    @staticmethod
    def _compute_confidence(
        bits: list[int],
        raw_values: list[int],
        stats: CanIdStats,
        is_counter: bool,
        is_checksum: bool,
    ) -> float:
        """Compute a confidence score (0-1) for a discovered signal."""
        if not raw_values or not stats.payload_samples:
            return 0.0

        scores: list[float] = []

        if len(bits) >= 2:
            scores.append(0.8)
        else:
            scores.append(0.4)

        unique = len(set(raw_values))
        if unique >= 2:
            scores.append(min(1.0, unique / 10))
        else:
            scores.append(0.2)

        if stats.count >= 10:
            scores.append(1.0)
        elif stats.count >= 5:
            scores.append(0.7)
        else:
            scores.append(0.4)

        if is_counter:
            scores.append(0.9)
        if is_checksum:
            scores.append(0.6)

        return statistics.mean(scores) if scores else 0.5

    # ── Post-processing ───────────────────────────────────────────────

    @staticmethod
    def _post_process_signals(
        signals: list[DiscoveredSignal],
        stats: CanIdStats,
    ) -> list[DiscoveredSignal]:
        """Post-process signals: merge adjacent, fix overlaps, name counters."""
        if not signals:
            return []

        signals.sort(key=lambda s: s.start_pos)

        counter_idx = 0
        for sig in signals:
            if sig.is_counter:
                counter_idx += 1
                sig.name = f"Counter_{counter_idx}"
            elif sig.is_checksum:
                sig.name = "Checksum"
            elif sig.enum_values and len(sig.enum_values) <= 4:
                sig.name = f"State_{sig.id}"
            elif len(sig.raw_values) >= 3:
                sig.name = f"Signal_{sig.id}"

        return signals

    # ── Export to PDU database ────────────────────────────────────────

    def to_pdu_db(
        self,
        bus_mapping: dict[int, str] | None = None,
        message_names: dict[int, str] | None = None,
        result: ReverseEngineeringResult | None = None,
    ) -> dict:
        """Export reverse-engineered results as a PDU database dict.

        Args:
            bus_mapping:  Map BLF channel number → bus name.
            message_names: Map CAN arbitration ID → message name.
            result: A pre-computed :class:`ReverseEngineeringResult` (from
                :meth:`reverse_engineer`), to avoid re-running the expensive
                bit-correlation clustering when the caller already has one.
                Runs :meth:`reverse_engineer` itself if omitted.

        Returns:
            A dict matching the PDU database schema.
        """
        result = result if result is not None else self.reverse_engineer()
        bus_mapping = bus_mapping or {}
        message_names = message_names or {}

        messages: list[dict] = []

        # Sequential DbId (matching TraceAnalyzer.to_pdu_db()'s scheme) --
        # deliberately not can_id + 1, so toggling signal reverse-engineering
        # on/off and re-exporting doesn't renumber every message.
        for db_id, msg in enumerate(result.messages, start=1):
            signals_list: list[dict] = []
            for sig in msg.signals:
                signals_list.append({
                    "id": sig.id,
                    "SignalName": sig.name,
                    "Length": sig.length,
                    "StartPos": sig.start_pos,
                    "ByteOrder": sig.byte_order,
                    "ValueType": sig.value_type,
                    "SigSendType": sig.is_counter or False,
                    "Repetitions": 0,
                    "InitValue": 0,
                    "Factor": sig.factor,
                    "Offset": sig.offset,
                    "Min": sig.min_val,
                    "Max": sig.max_val,
                    "Unit": sig.unit,
                    "EnumValues": sig.enum_values,
                    "IsMuxor": False,
                    "MuxValue": None,
                    "Comment": "",
                })

            messages.append({
                "DbId": db_id,
                "MessageName": message_names.get(
                    msg.can_id, msg.message_name
                ),
                "Bus": bus_mapping.get(msg.channel, msg.bus),
                "BusType": msg.bus_type,
                "MessageType": 0,
                "Direction": 0,
                "RoutingType": 0,
                "TargetDbIds": None,
                "SourceDbId": None,
                "isE2E": 0,
                "SendType": msg.send_type,
                "CycleTime": int(msg.cycle_time_ms),
                "CycleTimeFast": 0,
                "NrOfRepetitions": 0,
                "Identifier": msg.identifier,
                "FrameType": 1 if msg.is_extended else 0,
                "Length": msg.length,
                "BRS": msg.is_fd,
                "signalcount": len(signals_list),
                "signals": signals_list,
                "Comment": "",
                "Node": "",
            })

        return {
            "schema_version": "1.0",
            "messages": messages,
            "signal_routes": [],
        }

    def save_pdu_db(self, path: str | Path, **kwargs) -> Path:
        """Reverse-engineer and save the derived PDU database to a JSON file."""
        import json
        pdu_db = self.to_pdu_db(**kwargs)
        out = Path(path)
        out.write_text(json.dumps(pdu_db, indent=2))
        return out
