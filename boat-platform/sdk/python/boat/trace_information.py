# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

"""Stage-1 information triage for CAN traces.

Answers, *before* the expensive reverse-engineering pass runs, the questions
that decide whether it should run at all: is there any structure to find, is
the capture long enough for the estimators to mean anything, should the
logger keep recording or change the drive scenario, and which messages need
more data. The concept, the measurements behind every threshold, and the
stage-1/stage-2 split are documented in ``backlog/trace_information_value.md``.

Stage 1 computes only level-0 statistics (per-bit marginal and first-order
conditional entropy, per DLC partition) plus trace-level shape (phase
segmentation, payload-saturation curves). It deliberately does **not** assign
the ``H_derived``/``H_seq`` buckets — a CRC byte is statistically
indistinguishable from noise (measured MI/bit ~0.05) and a counter still
carries ~2.4 bits/frame of fake surprise at per-bit level, so any "final"
score at this stage would systematically overstate innovation. That
reclassification is stage 2 (:meth:`TraceInformationScorer.classify`), which
consumes the reverse-engineering result, audits its claims, and produces the
final bucket decomposition, explainability, and structure-extraction grade.

The input trace is assumed to be a *genuine* capture of a healthy bus.
Deliberately injected traffic (fuzzing, replay attacks) inflates liveness and
entropy while poisoning encoding inference; detecting and excluding it is out
of scope here.

Usage::

    from boat.trace_information import triage_trace

    profile = triage_trace("recordings/capture.blf")
    if not profile.worth_re_pass:
        print("\\n".join(profile.reasons))
    for action in profile.actions:
        print(action)

Or staged explicitly, sharing the analyzer's parsed frames::

    analyzer = TraceAnalyzer(path)
    analyzer.analyze()
    scorer = TraceInformationScorer(analyzer)
    profile = scorer.triage()                        # stage 1: gate
    if profile.worth_re_pass:
        result = TraceReverseEngineer(analyzer).reverse_engineer()
        profile = scorer.classify(result, profile)   # stage 2: buckets, audit, grade

Bit positions are indexed MSB-first (bit 0 = MSB of byte 0), matching
``trace_reverse_engineer.py`` and the PDU database's Motorola start-bit
convention, so a position printed here names the same bit a
``DiscoveredSignal`` would.
"""

from __future__ import annotations

import math
from bisect import bisect_left
from dataclasses import dataclass, field
from pathlib import Path

from boat.trace_analyzer import CanIdStats, TraceAnalysis, TraceAnalyzer
from boat.trace_reverse_engineer import (
    DiscoveredSignal,
    ReverseEngineeredMessage,
    ReverseEngineeringResult,
)

# ── Thresholds ──────────────────────────────────────────────────────────
#
# Every constant below traces back to a measurement or an estimator bound in
# backlog/trace_information_value.md; none is tunable folklore.

# Below this many frames the plug-in entropy estimator is dominated by its
# own bias and no per-bit verdict is emitted, only an `under_sampled` flag.
_MIN_SAMPLES = 30

# Distinct-payload tracking is capped to bound memory on high-entropy IDs
# (a modern capture showed >100k distinct payloads on a single ID). Once an
# ID blows the cap its payload entropy is pinned at the ceiling anyway.
_DISTINCT_CAP = 100_000

# Coupon-collector verdicts: fraction of all distinct payloads that first
# appeared in the last decile of the ID's frames. At or above the first
# bound the capture is still discovering (a constant discovery rate sits
# exactly at 1/10); below the second, more time buys nothing.
_UNSATURATED_LAST_DECILE = 0.10
_SATURATED_LAST_DECILE = 0.01

# ID-discovery shape: a capture is "flat" (logger attached to a running bus)
# when ≥90% of the IDs it will ever show appear within the first 1% of its
# duration — measured on OBD captures, where 100% appear that early. A
# startup-from-bus-sleep capture instead stages IDs in over tens of seconds.
_FLAT_DISCOVERY_WINDOW = 0.01
_FLAT_DISCOVERY_FRACTION = 0.90

# When discovery is staged, the warm-up phase ends where 95% of eventually-
# seen IDs have appeared, capped at half the capture so a trace that keeps
# discovering IDs forever still has a steady phase to score.
_WARMUP_PERCENTILE = 0.95
_WARMUP_MAX_FRACTION = 0.5

# Breadth normalization: ids / (ids + half-point). 50 IDs — between an
# OBD-filtered view (~55-85) and a single ECU bench setup (~17) — scores 0.5.
_BREADTH_HALF_POINT = 50.0

# Naive payload entropy within 5% of its ceiling means the estimate is
# pinned by sample size, not measuring the message.
_PINNED_SATURATION = 0.95

# Byte values CAN FD DLC padding is filled with in the wild (0xAA observed
# on a recent vehicle; 0x00/0xFF/0x55 are the other common fill patterns).
_PADDING_VALUES = frozenset({0x00, 0xFF, 0xAA, 0x55})

# For each byte value, which MSB-first bit offsets (0 = MSB) are set —
# lets the transition loop visit only *changed* bits of a XOR diff.
_BYTE_BITS: tuple[tuple[int, ...], ...] = tuple(
    tuple(j for j in range(8) if (v >> (7 - j)) & 1) for v in range(256)
)

# ── Stage-2 thresholds ──────────────────────────────────────────────────

# Audit: a claimed application signal whose live bits look i.i.d. is
# confabulated structure, not a signal. Measured per-bit mutual information:
# genuine application signals 0.31-0.45, CRC bytes 0.05-0.08, a rolling-code
# field 0.00-0.01. A signal is suspect when its bits carry high surprise
# (h_cond/bit >= 0.5 — i.e. they are busy, not a rarely-flipping boolean)
# yet essentially no structure (MI/bit <= 0.05).
_SUSPECT_MI_PER_BIT = 0.05
_SUSPECT_HCOND_PER_BIT = 0.5

# Confidence of a suspect signal is damped, not zeroed: the audit is
# statistical evidence against, not a functional disproof.
_SUSPECT_CONFIDENCE_FACTOR = 0.25

# Search mask (stage-1 -> RE feedback). A byte is "noise-like" when every
# live bit in it clears the same bars the stage-2 audit uses — deliberately
# the same constants, because it is the same failure mode caught earlier.
#
# Masking is done at BYTE granularity and only for runs of at least this
# many consecutive noise-like bytes. Per-bit masking would shred genuine
# wide signals, whose low bits legitimately look noisy (a 16-bit smooth
# signal's LSB is close to a fair coin). The measured opaque regions are
# multi-byte tails — 4B, 2B, 2B on the three worst Renault Clio messages —
# so a run requirement keeps what matters and drops the risky singletons.
_MASK_MIN_RUN_BYTES = 2

# Counter template scan. A counter's per-bit conditional entropy is
# analytically exact: bit k counted from the LSB flips every 2^k frames, so
# H_cond = H2(2^-k) -- 0, 1.0, 0.811, 0.544, ... Measured on two real
# counters (Renault Clio 0xC6 @51+4 and 0x29A @52+4): 0.00 / 1.00 / 0.81 /
# 0.54, matching the prediction to two decimals.
#
# The scan keys on the LSB alone, and on its *change rate* rather than its
# entropy. Two reasons:
#
#   - Only the LSB survives decimation. With an observed stride s the LSB
#     alternates deterministically whenever s is odd, and even strides are
#     rejected outright (_counter_stride). Measured on the Clio's stride-3
#     counter 0x511 @52+4: the higher bits deviate from the template (0.95
#     against a predicted 0.54) while the LSB still reads exactly 0.00.
#   - H_cond ~ 0 does not identify the LSB. H2(2^-k) tends to 0 for *large*
#     k too, so a counter's slow high bits clear an entropy bar just as its
#     LSB does (measured: an 8-bit counter's bits 5-7 all read below 0.25).
#     What separates them is that the LSB toggles on every frame while a
#     high bit almost never does.
#
# So the condition is p_change ~ 1: necessary for any counter this code will
# accept, and specific enough to prune hard. The tolerance absorbs a few
# dropped or duplicated frames; a heavily duplicated capture would need it
# loosened.
_COUNTER_LSB_MIN_CHANGE_RATE = 0.90
_COUNTER_LSB_P1_TOLERANCE = 0.10

# Structure-extraction grade: S and E gate whether inference is possible at
# all, so they weigh heaviest; then the signal share (the normalized I
# component), then breadth. X is reported separately — it measures the RE
# pass as much as the trace (see backlog/trace_information_value.md).
_GRADE_W_SUFFICIENCY = 0.30
_GRADE_W_EXCITATION = 0.30
_GRADE_W_SIGNAL_SHARE = 0.25
_GRADE_W_BREADTH = 0.15


def _h2(p: float) -> float:
    """Binary entropy of a Bernoulli(p), in bits."""
    if p <= 0.0 or p >= 1.0:
        return 0.0
    return -p * math.log2(p) - (1.0 - p) * math.log2(1.0 - p)


# ── Result dataclasses ──────────────────────────────────────────────────


@dataclass
class BitInformation:
    """Level-0 statistics for one bit position within one DLC partition."""
    position: int          # MSB-first: bit 0 = MSB of byte 0
    p1: float              # marginal P(bit = 1)
    p_change: float        # P(bit differs from the previous frame's)
    h_marginal: float      # H2(p1), bits/frame
    h_conditional: float   # first-order H(b_t | b_{t-1}), bits/frame
    # Stage 1 only separates dead bits ("const") from the rest
    # ("unclassified"). Stage 2 (classify()) reassigns every unclassified
    # bit to "derived" (CRC/checksum), "seq" (counter), "signal" (validated
    # application signal) or "residual" (unclaimed, or claimed but failing
    # the audit — opaque or not yet understood).
    bucket: str = "unclassified"

    @property
    def mutual_information(self) -> float:
        return self.h_marginal - self.h_conditional


@dataclass
class DlcPartition:
    """Per-bit statistics for the frames of one ID sharing one payload length.

    Bit position *i* means different things in a 20- and a 48-byte frame of
    the same ID, so all per-bit statistics are computed per observed length,
    never across lengths. Transitions are counted between consecutive frames
    *of this partition* — for the overwhelmingly common single-length ID that
    is simply consecutive frames.
    """
    length: int                       # payload bytes
    count: int                        # frames of this length
    bits: list[BitInformation] = field(default_factory=list)
    live_bits: int = 0
    h_marginal: float = 0.0           # sums over bits, bits/frame
    h_conditional: float = 0.0
    # Trailing bytes that never changed and hold a recognized fill value
    # (0x00/0xFF/0xAA/0x55) — CAN FD DLC padding. Constant per partition by
    # construction; flagged so stage 2 can label the region.
    padding_tail_bytes: int = 0


@dataclass
class MessageInformation:
    """Stage-1 information profile of a single CAN ID."""
    can_id: int
    channel: int
    is_extended: bool
    is_fd: bool
    count: int
    width_bits: int                   # max observed length × 8
    lengths: dict[int, int]           # payload length -> frame count
    partitions: list[DlcPartition]
    live_bits: int                    # positions live in any partition
    h_marginal: float                 # expected bits/frame across partitions
    h_conditional: float
    mutual_information: float
    distinct_payloads: int
    distinct_capped: bool             # hit _DISTINCT_CAP; entropy is a floor
    payload_entropy: float            # naive whole-payload plug-in entropy
    payload_entropy_ceiling: float    # min(width, log2 N) — the estimator cap
    payload_saturation: float         # entropy / ceiling; ~1.0 = pinned
    new_distinct_last_decile: float   # fraction of distinct payloads that
                                      # first appeared in the last decile
    saturation_verdict: str           # "insufficient"|"unsaturated"|"plateauing"|"saturated"
    under_sampled: bool               # count < _MIN_SAMPLES
    frames_per_second: float          # over the ID's own presence window
    innovation_bits_per_s: float      # gross: h_conditional × fps, level-0,
                                      # *before* counter/CRC subtraction
    flags: list[str] = field(default_factory=list)
    # Live bits counted over the steady phase only; None when the trace has
    # no warm-up phase (flat discovery) or the ID has <2 steady frames.
    live_bits_steady: int | None = None
    # ── Stage 2 (classify()) — None/empty until it runs ────────────────
    # Per-bucket totals over unique bit positions / expected bits per frame.
    # Keys: "const", "derived", "seq", "signal", "residual".
    bucket_bits: dict[str, int] | None = None
    bucket_h_marginal: dict[str, float] | None = None
    bucket_h_conditional: dict[str, float] | None = None
    # Names of claimed signals the audit flagged as i.i.d. noise.
    suspect_signals: list[str] = field(default_factory=list)
    # Trailing bytes whose live bits are all residual — the SecOC/crypto
    # "structured head + opaque tail" shape.
    opaque_tail_bytes: int = 0
    # Innovation restricted to the signal bucket: h_cond("signal") × fps.
    net_innovation_bits_per_s: float | None = None


@dataclass
class SearchMask:
    """Where the reverse-engineering pass should not bother looking.

    Produced from stage-1 statistics alone (:meth:`TraceInformationScorer.search_mask`),
    so it is available *before* the expensive pass runs. Passed to
    :class:`~boat.trace_reverse_engineer.TraceReverseEngineer` as
    ``search_mask=``; see its docstring for what it does and does not
    gate — functional detectors (CRC, counter) deliberately keep full
    access to every bit, because they can *prove* what they find. Only the
    statistical application-signal clustering is masked.
    """
    # CAN ID -> bit positions excluded from application-signal clustering.
    excluded_bits: dict[int, set[int]] = field(default_factory=dict)
    # CAN IDs whose every live bit is masked — nothing left to cluster.
    skip_ids: set[int] = field(default_factory=set)
    # CAN ID -> human-readable why, for the CLI and for the record.
    reasons: dict[int, str] = field(default_factory=dict)

    @property
    def masked_bits(self) -> int:
        return sum(len(v) for v in self.excluded_bits.values())

    def __bool__(self) -> bool:
        return bool(self.excluded_bits or self.skip_ids)


@dataclass
class TracePhase:
    """A stretch of the capture with qualitatively distinct behaviour."""
    label: str            # "warmup" | "steady"
    start_s: float        # relative to first frame
    end_s: float
    ids_first_seen: int   # IDs whose first frame falls in this phase


@dataclass
class TraceInformationProfile:
    """Stage-1 triage result for one trace.

    Carries the components computable *before* the reverse-engineering pass:
    breadth, excitation, sufficiency, and gross innovation. Explainability
    (X) and the net, redundancy-subtracted innovation only exist after
    stage 2 and are deliberately absent here.
    """
    path: str
    total_frames: int
    duration_s: float
    unique_ids: int
    channels: set[int]
    staged_discovery: bool
    phases: list[TracePhase]
    messages: dict[int, MessageInformation]
    breadth: float                    # 0-1, saturating in unique IDs
    excitation: float                 # 0-1, live-bit fraction (steady phase)
    sufficiency: float                # 0-1, fraction of IDs above sample floor
    gross_innovation_bits_per_s: float
    worth_re_pass: bool
    reasons: list[str]
    actions: list[str]                # ranked, most important first
    # ── Stage 2 (classify()) — None until it runs ──────────────────────
    # Shares of the trace's marginal-entropy budget (frame-count weighted).
    # Because unclaimed bits default pessimistically to residual,
    # explainability == 1 - opacity by construction; it is still named
    # separately because it grades the RE pass, not the trace.
    explainability: float | None = None   # X: (derived+seq+signal) / total
    redundancy: float | None = None       # (derived + seq) / total
    opacity: float | None = None          # residual / total
    signal_share: float | None = None     # signal / total — the normalized I
    net_innovation_bits_per_s: float | None = None
    suspect_signals: list[str] = field(default_factory=list)
    # Structure-extraction grade, 0-1. Weighted per _GRADE_W_*; None until
    # classify() runs.
    grade: float | None = None


# ── Per-partition accumulator ───────────────────────────────────────────


class _PartitionAccumulator:
    """One-pass per-bit statistics for the frames of one (ID, length).

    Occupancy is accounted by run length: a bit's 1-count only needs
    updating when the bit *changes*, so the cost per frame is proportional
    to the number of changed bits (via the XOR diff), not the payload
    width. Measured cost of this scheme is what makes triage ~18× cheaper
    than the reverse-engineering pass it gates.
    """

    __slots__ = ("length", "width", "n", "_ones", "_n01", "_n10",
                 "_last_change", "_prev")

    def __init__(self, length: int) -> None:
        self.length = length
        self.width = length * 8
        self.n = 0
        self._ones = [0] * self.width
        self._n01 = [0] * self.width
        self._n10 = [0] * self.width
        self._last_change = [0] * self.width
        self._prev: bytes | None = None

    def add(self, payload: bytes) -> None:
        prev = self._prev
        if prev is not None:
            j = self.n
            ones = self._ones
            n01 = self._n01
            n10 = self._n10
            last = self._last_change
            for bi in range(self.length):
                d = prev[bi] ^ payload[bi]
                if not d:
                    continue
                base = bi * 8
                pb = prev[bi]
                for off in _BYTE_BITS[d]:
                    pos = base + off
                    if (pb >> (7 - off)) & 1:
                        ones[pos] += j - last[pos]
                        n10[pos] += 1
                    else:
                        n01[pos] += 1
                    last[pos] = j
        self._prev = payload
        self.n += 1

    def finish(self) -> DlcPartition:
        part = DlcPartition(length=self.length, count=self.n)
        n = self.n
        if n == 0 or self._prev is None:
            return part
        final = self._prev
        h_marg_sum = 0.0
        h_cond_sum = 0.0
        live = 0
        byte_const = [True] * self.length
        for pos in range(self.width):
            # Flush the final run: the bit held its last value from its
            # last change to the end of the partition.
            fbit = (final[pos // 8] >> (7 - pos % 8)) & 1
            ones = self._ones[pos] + (n - self._last_change[pos]) * fbit
            p1 = ones / n
            h_marg = _h2(p1)
            h_cond = 0.0
            if 0 < ones < n:
                live += 1
                byte_const[pos // 8] = False
                if n >= 2:
                    # Transition contexts: occupancy over the first n-1
                    # frames (the last frame has no successor).
                    ctx1 = ones - fbit
                    ctx0 = (n - ones) - (1 - fbit)
                    if ctx0 > 0:
                        h_cond += (ctx0 / (n - 1)) * _h2(
                            min(self._n01[pos] / ctx0, 1.0)
                        )
                    if ctx1 > 0:
                        h_cond += (ctx1 / (n - 1)) * _h2(
                            min(self._n10[pos] / ctx1, 1.0)
                        )
            bucket = "const" if (ones == 0 or ones == n) else "unclassified"
            changes = self._n01[pos] + self._n10[pos]
            part.bits.append(
                BitInformation(
                    position=pos, p1=p1,
                    p_change=changes / (n - 1) if n > 1 else 0.0,
                    h_marginal=h_marg, h_conditional=h_cond, bucket=bucket,
                )
            )
            h_marg_sum += h_marg
            h_cond_sum += h_cond
        part.live_bits = live
        part.h_marginal = h_marg_sum
        part.h_conditional = h_cond_sum
        pad = 0
        for bi in range(self.length - 1, -1, -1):
            if byte_const[bi] and final[bi] in _PADDING_VALUES:
                pad += 1
            else:
                break
        part.padding_tail_bytes = pad
        return part


def _consecutive_runs(values: list[int]) -> list[list[int]]:
    """Split a sorted list into runs of consecutive integers."""
    runs: list[list[int]] = []
    for v in values:
        if runs and v == runs[-1][-1] + 1:
            runs[-1].append(v)
        else:
            runs.append([v])
    return runs


# ── Scorer ──────────────────────────────────────────────────────────────


class TraceInformationScorer:
    """Information triage and classification over an analyzed trace.

    Accepts either a :class:`TraceAnalyzer` whose :meth:`analyze` has run
    (mirroring :class:`TraceReverseEngineer`) or a :class:`TraceAnalysis`
    directly. :meth:`triage` is stage 1 (the pre-RE gate); :meth:`classify`
    is stage 2 (bucket assignment from the reverse-engineering result, the
    claim audit, and the grade). See ``backlog/trace_information_value.md``.
    """

    def __init__(self, analyzer: TraceAnalyzer | TraceAnalysis) -> None:
        analysis = getattr(analyzer, "_analysis", analyzer)
        if analysis is None:
            raise RuntimeError("Call analyze() before TraceInformationScorer")
        if not isinstance(analysis, TraceAnalysis):
            raise TypeError(
                f"expected TraceAnalyzer or TraceAnalysis, got {type(analyzer).__name__}"
            )
        self._analysis: TraceAnalysis = analysis

    # ── public API ──────────────────────────────────────────────────

    def triage(self) -> TraceInformationProfile:
        analysis = self._analysis
        t0, t_end = self._time_span()
        duration = max(t_end - t0, 0.0)

        staged, warmup_end, phases = self._segment_phases(t0, duration)

        messages: dict[int, MessageInformation] = {}
        for aid, s in analysis.can_stats.items():
            messages[aid] = self._score_message(s, duration)
        if staged:
            self._add_steady_liveness(messages, t0 + warmup_end)

        breadth = len(messages) / (len(messages) + _BREADTH_HALF_POINT)
        excitation = self._excitation(messages, staged)
        scored = [m for m in messages.values() if m.count >= 2]
        well_sampled = [m for m in scored if not m.under_sampled]
        sufficiency = len(well_sampled) / len(messages) if messages else 0.0
        gross_innovation = (
            sum(m.h_conditional * m.count for m in scored) / duration
            if duration > 0 else 0.0
        )

        worth, reasons = self._verdict(messages, scored, well_sampled)
        actions = self._actions(
            messages, well_sampled, staged, warmup_end, worth, reasons
        )

        return TraceInformationProfile(
            path=analysis.path,
            total_frames=analysis.total_frames,
            duration_s=duration,
            unique_ids=len(messages),
            channels=set(analysis.channels),
            staged_discovery=staged,
            phases=phases,
            messages=messages,
            breadth=breadth,
            excitation=excitation,
            sufficiency=sufficiency,
            gross_innovation_bits_per_s=gross_innovation,
            worth_re_pass=worth,
            reasons=reasons,
            actions=actions,
        )

    def classify(
        self,
        re_result: ReverseEngineeringResult,
        profile: TraceInformationProfile | None = None,
        damp_suspect_confidence: bool = True,
    ) -> TraceInformationProfile:
        """Stage 2: bucket assignment, claim audit, and the final grade.

        Consumes the reverse-engineering result to move bits out of the
        provisional stage-1 state: functionally proven checksums land in
        ``derived``, counters in ``seq``, claimed application signals in
        ``signal`` — unless the audit rejects them — and everything else
        that ever changed lands pessimistically in ``residual``.

        The audit: a claimed signal whose live bits carry high surprise but
        no structure (see ``_SUSPECT_MI_PER_BIT``) is confabulated — the
        measured case is a rolling-code field claimed as thirty-two 1-bit
        "signals". Such signals are listed in ``suspect_signals``, their
        bits are reclassified as ``residual``, and (unless
        ``damp_suspect_confidence`` is False) their ``confidence`` on the
        passed-in ``re_result`` is multiplied by
        ``_SUSPECT_CONFIDENCE_FACTOR`` in place, so a subsequent
        ``to_pdu_db()`` can threshold them away.

        ``profile`` is enriched in place when given (avoiding a second
        stage-1 pass); otherwise :meth:`triage` runs first. Messages the RE
        pass skipped keep all their live bits in ``residual`` — a weak RE
        result must not flatter the trace.
        """
        if profile is None:
            profile = self.triage()

        re_by_id: dict[int, ReverseEngineeredMessage] = {
            m.can_id: m for m in re_result.messages
        }
        for aid, info in profile.messages.items():
            self._classify_message(
                info, re_by_id.get(aid), damp_suspect_confidence
            )

        # Frame-count weighted trace totals: a 100 Hz message contributes
        # proportionally more of the trace's bit budget than a 1 Hz one.
        totals = {k: 0.0 for k in ("derived", "seq", "signal", "residual")}
        net_cond = 0.0
        suspects: list[str] = []
        for info in profile.messages.values():
            if info.bucket_h_marginal is None:
                continue
            for k in totals:
                totals[k] += info.bucket_h_marginal.get(k, 0.0) * info.count
            net_cond += (
                info.bucket_h_conditional.get("signal", 0.0) * info.count
            )
            suspects.extend(
                f"0x{info.can_id:X}:{name}" for name in info.suspect_signals
            )
        h_total = sum(totals.values())
        if h_total > 0:
            profile.redundancy = (totals["derived"] + totals["seq"]) / h_total
            profile.opacity = totals["residual"] / h_total
            profile.signal_share = totals["signal"] / h_total
            profile.explainability = 1.0 - profile.opacity
        else:
            profile.redundancy = profile.opacity = 0.0
            profile.signal_share = profile.explainability = 0.0
        profile.net_innovation_bits_per_s = (
            net_cond / profile.duration_s if profile.duration_s > 0 else 0.0
        )
        profile.suspect_signals = suspects
        profile.grade = (
            _GRADE_W_SUFFICIENCY * profile.sufficiency
            + _GRADE_W_EXCITATION * profile.excitation
            + _GRADE_W_SIGNAL_SHARE * profile.signal_share
            + _GRADE_W_BREADTH * profile.breadth
        )
        self._stage2_actions(profile)
        return profile

    def search_mask(
        self, profile: TraceInformationProfile | None = None
    ) -> SearchMask:
        """Build the stage-1 -> RE feedback mask: which regions the
        application-signal search should skip.

        Uses level-0 statistics only, so it is available *before* the
        reverse-engineering pass — that is the whole point. A byte is
        noise-like when every live bit in it carries high surprise and no
        structure (the measured signature of a rolling code or a MAC:
        MI/bit ~0.00 against 0.31-0.45 for genuine application signals),
        and only runs of :data:`_MASK_MIN_RUN_BYTES` or more consecutive
        such bytes are masked — see that constant for why per-bit masking
        would be wrong.

        Under-sampled messages are never masked: too few frames to call
        anything noise.

        .. note::
           The mask and the stage-2 audit target the same failure mode, by
           design and with the same constants. So a *masked* run legitimately
           finds nothing to flag, and a clean audit is then evidence that the
           mask worked — not independent evidence that the RE pass is sound.
           Keep masking off when using the audit as a measurement of the RE
           pass itself.
        """
        if profile is None:
            profile = self.triage()
        mask = SearchMask()
        for aid, info in profile.messages.items():
            if info.under_sampled or not info.partitions:
                continue
            part = info.partitions[0]        # largest, by construction
            live_by_byte: dict[int, list[BitInformation]] = {}
            for b in part.bits:
                if b.bucket != "const":
                    live_by_byte.setdefault(b.position // 8, []).append(b)
            if not live_by_byte:
                continue
            noisy = {
                bi for bi, bits in live_by_byte.items()
                if all(
                    b.h_conditional >= _SUSPECT_HCOND_PER_BIT
                    and b.mutual_information <= _SUSPECT_MI_PER_BIT
                    for b in bits
                )
            }
            excluded: set[int] = set()
            for run in _consecutive_runs(sorted(noisy)):
                if len(run) < _MASK_MIN_RUN_BYTES:
                    continue
                for bi in run:
                    excluded.update(b.position for b in live_by_byte[bi])
            if not excluded:
                continue
            mask.excluded_bits[aid] = excluded
            total_live = sum(len(v) for v in live_by_byte.values())
            if len(excluded) >= total_live:
                mask.skip_ids.add(aid)
                mask.reasons[aid] = (
                    f"every live bit ({total_live}) is high-surprise / "
                    "no-structure — opaque payload, nothing to cluster"
                )
            else:
                mask.reasons[aid] = (
                    f"{len(excluded)} of {total_live} live bits masked "
                    f"({len(excluded) // 8} byte(s) of high-surprise / "
                    "no-structure payload)"
                )
        return mask

    def counter_lsb_hints(
        self, profile: TraceInformationProfile | None = None
    ) -> dict[int, list[int]]:
        """Bit positions that could be a counter's least-significant bit.

        Returns ``{can_id: sorted positions}`` for well-sampled messages,
        for :class:`~boat.trace_reverse_engineer.TraceReverseEngineer`'s
        ``counter_lsb_hints=``. A counter of any accepted stride has an LSB
        that alternates on every frame, so ``p_change ~ 1`` with ``p1 ~ 0.5``
        there -- see :data:`_COUNTER_LSB_MIN_CHANGE_RATE` for the
        measurements behind the bars, and for why the change rate rather
        than the conditional entropy is the discriminating quantity.

        This is a *necessary* condition, not a sufficient one: an
        alternating one-bit flag looks identical and is a legitimate
        candidate for the authoritative check to reject. Its value is
        negative -- a candidate span containing no such position cannot
        hold a counter, and can be dropped before any frame extraction.

        Under-sampled messages are omitted entirely (the caller then scans
        them unpruned): too few frames for the alternation to be reliable.
        """
        if profile is None:
            profile = self.triage()
        hints: dict[int, list[int]] = {}
        for aid, info in profile.messages.items():
            if info.under_sampled or not info.partitions:
                continue
            hints[aid] = [
                b.position
                for b in info.partitions[0].bits
                if b.bucket != "const"
                and b.p_change >= _COUNTER_LSB_MIN_CHANGE_RATE
                and abs(b.p1 - 0.5) <= _COUNTER_LSB_P1_TOLERANCE
            ]
        return hints

    # ── stage-2 internals ───────────────────────────────────────────

    def _classify_message(
        self,
        info: MessageInformation,
        re_msg: ReverseEngineeredMessage | None,
        damp: bool,
    ) -> None:
        """Assign every bit of one message to its final bucket."""
        # Re-entrant: clear any earlier stage-2 state so classify() may be
        # called again (e.g. after re-running the RE pass). Note confidence
        # damping acts on the *re_result* and is applied once per call.
        info.suspect_signals = []
        info.flags = [f for f in info.flags
                      if f not in ("suspect_noise", "opaque_tail")]
        derived_pos: set[int] = set()
        seq_pos: set[int] = set()
        signal_pos: set[int] = set()
        suspect_pos: set[int] = set()

        for sig in (re_msg.signals if re_msg else []):
            span = range(sig.start_pos, sig.start_pos + sig.length)
            if sig.is_checksum:
                derived_pos.update(span)
            elif sig.is_counter:
                seq_pos.update(span)
            elif self._signal_is_suspect(info, sig):
                suspect_pos.update(span)
                info.suspect_signals.append(sig.name)
                if damp:
                    sig.confidence *= _SUSPECT_CONFIDENCE_FACTOR
            else:
                signal_pos.update(span)
        # A position claimed by both a suspect and a validated signal (mux
        # variants overlap) stays a signal: one surviving claim is enough.
        suspect_pos -= signal_pos

        def bucket_of(pos: int, const: bool) -> str:
            if const:
                return "const"
            if pos in derived_pos:
                return "derived"
            if pos in seq_pos:
                return "seq"
            if pos in signal_pos:
                return "signal"
            return "residual"

        bits: dict[str, set[int]] = {
            k: set() for k in ("const", "derived", "seq", "signal", "residual")
        }
        h_marg = {k: 0.0 for k in bits}
        h_cond = {k: 0.0 for k in bits}
        n = info.count or 1
        for part in info.partitions:
            weight = part.count / n
            for b in part.bits:
                bucket = bucket_of(b.position, b.bucket == "const")
                b.bucket = bucket
                h_marg[bucket] += b.h_marginal * weight
                h_cond[bucket] += b.h_conditional * weight
                bits[bucket].add(b.position)
        # A position live in one partition and dead in another counts once,
        # under its live bucket.
        bits["const"] -= (
            bits["derived"] | bits["seq"] | bits["signal"] | bits["residual"]
        )
        info.bucket_bits = {k: len(v) for k, v in bits.items()}
        info.bucket_h_marginal = h_marg
        info.bucket_h_conditional = h_cond
        info.net_innovation_bits_per_s = (
            h_cond["signal"] * info.frames_per_second
        )
        info.opaque_tail_bytes = self._opaque_tail(info)
        if info.suspect_signals:
            info.flags.append("suspect_noise")
        if info.opaque_tail_bytes:
            info.flags.append("opaque_tail")

    @staticmethod
    def _signal_is_suspect(
        info: MessageInformation, sig: DiscoveredSignal
    ) -> bool:
        """Does this claimed application signal look like i.i.d. noise?

        Counters, checksums and mux selectors never reach here — they are
        functionally proven. Under-sampled messages are skipped too: too
        few frames to call anything noise.
        """
        if sig.is_mux_selector or info.under_sampled:
            return False
        span = range(sig.start_pos, sig.start_pos + sig.length)
        mi_sum = cond_sum = 0.0
        live = 0
        n = info.count or 1
        for part in info.partitions:
            weight = part.count / n
            for b in part.bits:
                if b.position in span and b.bucket != "const":
                    mi_sum += b.mutual_information * weight
                    cond_sum += b.h_conditional * weight
                    live += 1
        if not live:
            return False
        return (
            cond_sum / live >= _SUSPECT_HCOND_PER_BIT
            and mi_sum / live <= _SUSPECT_MI_PER_BIT
        )

    @staticmethod
    def _opaque_tail(info: MessageInformation) -> int:
        """Trailing bytes of the primary partition whose live bits are all
        residual — the structured-head + opaque-tail shape SecOC produces
        (truncated freshness + truncated MAC appended to the PDU)."""
        if not info.partitions:
            return 0
        part = info.partitions[0]          # largest, by construction
        by_byte: dict[int, list[str]] = {}
        for b in part.bits:
            if b.bucket != "const":
                by_byte.setdefault(b.position // 8, []).append(b.bucket)
        tail = 0
        seen_live = False
        for bi in range(part.length - 1, -1, -1):
            buckets = by_byte.get(bi)
            if buckets is None:
                if seen_live:
                    break              # const gap after live tail bytes
                continue               # pure padding/const at the very end
            if all(k == "residual" for k in buckets):
                tail += 1
                seen_live = True
            else:
                break
        return tail

    @staticmethod
    def _stage2_actions(profile: TraceInformationProfile) -> None:
        if profile.suspect_signals:
            shown = ", ".join(profile.suspect_signals[:5])
            more = (
                f" and {len(profile.suspect_signals) - 5} more"
                if len(profile.suspect_signals) > 5 else ""
            )
            profile.actions.append(
                f"audit: {len(profile.suspect_signals)} claimed signals look "
                f"like i.i.d. noise ({shown}{more}) — confidence damped, "
                "bits reclassified as residual"
            )
        opaque = sorted(
            (m for m in profile.messages.values() if m.opaque_tail_bytes),
            key=lambda m: -m.opaque_tail_bytes,
        )
        if opaque:
            shown = ", ".join(
                f"0x{m.can_id:X} ({m.opaque_tail_bytes}B)" for m in opaque[:5]
            )
            more = f" and {len(opaque) - 5} more" if len(opaque) > 5 else ""
            profile.actions.append(
                f"opaque tail regions (likely SecOC/crypto): {shown}{more} — "
                "structure extraction is capped there; treat as boundary, "
                "not as failure"
            )

    # ── trace-level shape ───────────────────────────────────────────

    def _time_span(self) -> tuple[float, float]:
        firsts = [s.timestamps[0] for s in self._analysis.can_stats.values()
                  if s.timestamps]
        lasts = [s.timestamps[-1] for s in self._analysis.can_stats.values()
                 if s.timestamps]
        if not firsts:
            return 0.0, 0.0
        return min(firsts), max(lasts)

    def _segment_phases(
        self, t0: float, duration: float
    ) -> tuple[bool, float, list[TracePhase]]:
        """Split the capture into warm-up + steady when ID discovery is
        staged (startup-from-bus-sleep shape), else one steady phase.

        Segmentation is driven purely by *when each ID first appears* —
        deterministic, one sorted list, no change-point machinery. Measured
        shapes: OBD captures show 100% of IDs within the first 1% of the
        capture; a bus-sleep startup staged 344→726 IDs over ~30 s.
        """
        stats = self._analysis.can_stats
        first_seen = sorted(
            s.timestamps[0] - t0 for s in stats.values() if s.timestamps
        )
        if not first_seen or duration <= 0.0:
            return False, 0.0, [TracePhase("steady", 0.0, duration,
                                           len(first_seen))]
        window = duration * _FLAT_DISCOVERY_WINDOW
        early = bisect_left(first_seen, window + 1e-12)
        if early / len(first_seen) >= _FLAT_DISCOVERY_FRACTION:
            return False, 0.0, [TracePhase("steady", 0.0, duration,
                                           len(first_seen))]
        idx = max(int(math.ceil(_WARMUP_PERCENTILE * len(first_seen))) - 1, 0)
        warmup_end = min(first_seen[idx], duration * _WARMUP_MAX_FRACTION)
        in_warmup = bisect_left(first_seen, warmup_end + 1e-12)
        phases = [
            TracePhase("warmup", 0.0, warmup_end, in_warmup),
            TracePhase("steady", warmup_end, duration,
                       len(first_seen) - in_warmup),
        ]
        return True, warmup_end, phases

    # ── per-message scoring ─────────────────────────────────────────

    @staticmethod
    def _score_message(s: CanIdStats, duration: float) -> MessageInformation:
        by_length: dict[int, _PartitionAccumulator] = {}
        payload_counts: dict[bytes, int] = {}
        first_occ: list[int] = []          # frame index of each new payload
        capped = False
        for idx, payload in enumerate(s.payload_samples):
            acc = by_length.get(len(payload))
            if acc is None:
                acc = by_length[len(payload)] = _PartitionAccumulator(len(payload))
            acc.add(payload)
            c = payload_counts.get(payload)
            if c is not None:
                payload_counts[payload] = c + 1
            elif len(payload_counts) < _DISTINCT_CAP:
                payload_counts[payload] = 1
                first_occ.append(idx)
            else:
                capped = True

        n = s.count
        partitions = sorted(
            (acc.finish() for acc in by_length.values()),
            key=lambda p: -p.count,
        )
        width = max((p.length for p in partitions), default=0) * 8

        # Expected bits/frame: each frame belongs to one partition.
        h_marg = sum(p.h_marginal * p.count for p in partitions) / n if n else 0.0
        h_cond = sum(p.h_conditional * p.count for p in partitions) / n if n else 0.0
        live_positions: set[int] = set()
        for p in partitions:
            live_positions.update(b.position for b in p.bits
                                  if b.bucket != "const")

        naive = 0.0
        for c in payload_counts.values():
            pr = c / n
            naive -= pr * math.log2(pr)
        ceiling = min(width, math.log2(n)) if n > 1 and width else 0.0
        saturation = min(naive / ceiling, 1.0) if ceiling > 0 else 0.0
        if capped:
            saturation = 1.0

        distinct = len(payload_counts)
        last_decile_start = n - n // 10 if n >= 10 else n
        new_last = sum(1 for i in first_occ if i >= last_decile_start)
        new_last_frac = new_last / distinct if distinct else 0.0
        if n < _MIN_SAMPLES:
            verdict = "insufficient"
        elif new_last_frac >= _UNSATURATED_LAST_DECILE:
            verdict = "unsaturated"
        elif new_last_frac < _SATURATED_LAST_DECILE:
            verdict = "saturated"
        else:
            verdict = "plateauing"

        presence = (s.timestamps[-1] - s.timestamps[0]) if len(s.timestamps) > 1 else 0.0
        fps = n / presence if presence > 0 else 0.0

        flags: list[str] = []
        if n < _MIN_SAMPLES:
            flags.append("under_sampled")
        if not live_positions and n >= 2:
            flags.append("constant")
        if saturation >= _PINNED_SATURATION and live_positions:
            flags.append("payload_entropy_pinned")
        if any(p.padding_tail_bytes for p in partitions):
            flags.append("padding_tail")
        if len(partitions) > 1:
            flags.append("variable_length")

        return MessageInformation(
            can_id=s.arbitration_id,
            channel=s.channel,
            is_extended=s.is_extended,
            is_fd=s.is_fd,
            count=n,
            width_bits=width,
            lengths={p.length: p.count for p in partitions},
            partitions=partitions,
            live_bits=len(live_positions),
            h_marginal=h_marg,
            h_conditional=h_cond,
            mutual_information=h_marg - h_cond,
            distinct_payloads=distinct,
            distinct_capped=capped,
            payload_entropy=naive,
            payload_entropy_ceiling=ceiling,
            payload_saturation=saturation,
            new_distinct_last_decile=new_last_frac,
            saturation_verdict=verdict,
            under_sampled=n < _MIN_SAMPLES,
            frames_per_second=fps,
            innovation_bits_per_s=h_cond * fps,
            flags=flags,
        )

    def _add_steady_liveness(
        self, messages: dict[int, MessageInformation], warmup_end_abs: float
    ) -> None:
        """Fill ``live_bits_steady`` from frames at or after the warm-up end.

        During warm-up, fields sit at init / signal-not-available fill
        (0xFF blocks observed on a startup capture), so warm-up frames make
        genuinely dead bits look live and vice versa. Liveness needs only a
        change mask, so this restricted pass is a cheap XOR-accumulate per
        length, not a second full statistics pass.
        """
        for aid, s in self._analysis.can_stats.items():
            start = bisect_left(s.timestamps, warmup_end_abs)
            if s.count - start < 2:
                continue
            masks: dict[int, tuple[int, int]] = {}   # length -> (first, mask)
            for payload in s.payload_samples[start:]:
                v = int.from_bytes(payload, "big")
                entry = masks.get(len(payload))
                if entry is None:
                    masks[len(payload)] = (v, 0)
                else:
                    masks[len(payload)] = (entry[0], entry[1] | (entry[0] ^ v))
            messages[aid].live_bits_steady = sum(
                mask.bit_count() for _, mask in masks.values()
            )

    # ── components & verdicts ───────────────────────────────────────

    @staticmethod
    def _excitation(
        messages: dict[int, MessageInformation], staged: bool
    ) -> float:
        live = 0
        total = 0
        for m in messages.values():
            if m.count < 2:
                continue
            if staged:
                if m.live_bits_steady is None:
                    continue
                live += m.live_bits_steady
            else:
                live += m.live_bits
            total += m.width_bits
        return live / total if total else 0.0

    @staticmethod
    def _verdict(
        messages: dict[int, MessageInformation],
        scored: list[MessageInformation],
        well_sampled: list[MessageInformation],
    ) -> tuple[bool, list[str]]:
        if not scored:
            return False, ["no message was observed more than once — "
                           "nothing to infer structure from"]
        total_live = sum(m.live_bits for m in scored)
        if total_live == 0:
            return False, ["no payload bit ever changed — the bus is idle; "
                           "every layout hypothesis survives, so the "
                           "reverse-engineering pass has nothing to work with"]
        if not well_sampled:
            return False, [
                f"every message is under-sampled (<{_MIN_SAMPLES} frames) — "
                "estimates would be pinned to the sampling ceiling; "
                "capture longer before reverse engineering"
            ]
        return True, [
            f"{total_live} live bits across "
            f"{sum(1 for m in scored if m.live_bits)} of {len(messages)} messages"
        ]

    @staticmethod
    def _actions(
        messages: dict[int, MessageInformation],
        well_sampled: list[MessageInformation],
        staged: bool,
        warmup_end: float,
        worth: bool,
        reasons: list[str],
    ) -> list[str]:
        actions: list[str] = []
        if not worth:
            actions.extend(reasons)

        if well_sampled:
            unsaturated = [m for m in well_sampled
                           if m.saturation_verdict == "unsaturated"]
            saturated = [m for m in well_sampled
                         if m.saturation_verdict == "saturated"]
            if len(unsaturated) / len(well_sampled) > 0.10:
                actions.append(
                    f"keep recording: {len(unsaturated)} of "
                    f"{len(well_sampled)} well-sampled messages were still "
                    "producing new payloads in the last tenth of the capture"
                )
            elif len(saturated) / len(well_sampled) >= 0.90:
                actions.append(
                    "capture is saturated: more time buys nothing — change "
                    "the drive scenario (gear, HVAC, doors, faults) instead "
                    "of recording longer"
                )

        if staged:
            actions.append(
                f"warm-up phase (first {warmup_end:.1f} s, staged ID "
                "discovery): excitation is scored on the steady phase only; "
                "encoding inference should skip warm-up frames (init/SNA fill)"
            )

        under = sorted((m for m in messages.values() if m.under_sampled),
                       key=lambda m: m.count)
        if under:
            shown = ", ".join(
                f"0x{m.can_id:X} ({m.count})" for m in under[:5]
            )
            more = f" and {len(under) - 5} more" if len(under) > 5 else ""
            actions.append(
                f"under-sampled (<{_MIN_SAMPLES} frames): {shown}{more} — "
                "no verdict on these without a longer capture"
            )

        pinned = sorted(
            (m for m in well_sampled if "payload_entropy_pinned" in m.flags),
            key=lambda m: -m.width_bits,
        )
        if pinned:
            shown = ", ".join(f"0x{m.can_id:X}" for m in pinned[:5])
            more = f" and {len(pinned) - 5} more" if len(pinned) > 5 else ""
            actions.append(
                f"payload entropy pinned at the sampling ceiling: {shown}"
                f"{more} — whole-payload measures say nothing here; only "
                "per-bit and per-field statistics are meaningful"
            )
        return actions


def triage_trace(path: str | Path) -> TraceInformationProfile:
    """Analyze a trace file and return its stage-1 information triage."""
    analyzer = TraceAnalyzer(path)
    analyzer.analyze()
    return TraceInformationScorer(analyzer).triage()
