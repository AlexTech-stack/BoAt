# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

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
    # Set only when this checksum was identified by the dedicated AUTOSAR CRC
    # scan (find_crcs()) rather than the older, weaker XOR-based heuristic in
    # _detect_checksum() -- crc_algorithm names exactly which of the six
    # AUTOSAR_SWS_CRCLibrary-defined algorithms matched every observed frame.
    # Set when this field, besides cycling, also decides what the rest of
    # the frame *means* -- a multiplexor selector. `multiplexed_bytes` are
    # the byte positions it selects between. See
    # TraceReverseEngineer.find_multiplexors().
    is_mux_selector: bool = False
    multiplexed_bytes: list[int] | None = None
    # Which variant this signal belongs to: the selector value under which
    # it was found. None means the signal sits outside the multiplexed
    # region and is present in every frame.
    mux_value: int | None = None
    # For a counter only: how far it advances between consecutive frames,
    # modulo its own width. Normally 1. Anything else means the capture is
    # not seeing every transmission of this message -- see
    # TraceReverseEngineer._counter_stride().
    counter_stride: int | None = None
    crc_algorithm: str | None = None
    crc_data_id: int | None = None
    # AUTOSAR E2E Profile 2's Data ID *list*, indexed by the counter value
    # (see TraceReverseEngineer._match_crc_data_id_list). Entry i is the
    # Data ID used on frames whose counter reads i, or None where the
    # capture never showed that counter value. Mutually exclusive with
    # crc_data_id, which holds the single constant Data ID of the schemes
    # that use one.
    crc_data_id_list: list[int | None] | None = None
    # For the simple whole-frame checksum family (SUM8/XOR8) only: the
    # constant K the aggregate over the *whole* frame is held at, which is
    # what makes the scheme checkable. See _find_simple_checksum().
    checksum_target: int | None = None


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
    # Best-effort AUTOSAR E2E Profile label (e.g. "E2E_Profile_2"), set only
    # when both a counter and a CRC were found for this message and their
    # combination matches a well-known standard profile definition -- see
    # _E2E_PROFILE_HINTS. A hint, not a verified classification: it isn't
    # checked against the full AUTOSAR E2E protocol spec, only the CRC
    # algorithm parameters themselves are spec-verified.
    e2e_profile: str | None = None


@dataclass
class ReverseEngineeringResult:
    """Full reverse engineering result."""
    messages: list[ReverseEngineeredMessage] = field(default_factory=list)
    total_can_ids: int = 0
    total_signals_discovered: int = 0
    numpy_available: bool = False


# ── AUTOSAR CRC engine ──────────────────────────────────────────────────
#
# Bit-by-bit (MSB-first, no lookup table -- candidate counts here are small
# enough that table-driven speed doesn't matter) implementation of the six
# byte-oriented CRC algorithms defined in AUTOSAR_SWS_CRCLibrary (R22-11).
# Every parameter below is verified against *every* "Check" value (CRC of
# ASCII "123456789") and every full worked test-vector table in the spec --
# 100% match. CRC64 (ECMA) is defined there too but deliberately out of
# scope: a 64-bit field is vanishingly rare in a CAN payload and would
# balloon the brute-force search space for little practical benefit.
_AUTOSAR_CRC_ALGORITHMS: dict[str, dict[str, int | bool]] = {
    "CRC8":     dict(width=8,  poly=0x1D,       init=0xFF,       refin=False, refout=False, xorout=0xFF),
    "CRC8H2F":  dict(width=8,  poly=0x2F,       init=0xFF,       refin=False, refout=False, xorout=0xFF),
    "CRC16":    dict(width=16, poly=0x1021,     init=0xFFFF,     refin=False, refout=False, xorout=0x0000),
    "CRC16ARC": dict(width=16, poly=0x8005,     init=0x0000,     refin=True,  refout=True,  xorout=0x0000),
    "CRC32":    dict(width=32, poly=0x04C11DB7, init=0xFFFFFFFF, refin=True,  refout=True,  xorout=0xFFFFFFFF),
    "CRC32P4":  dict(width=32, poly=0xF4ACFB13, init=0xFFFFFFFF, refin=True,  refout=True,  xorout=0xFFFFFFFF),
    # Not a CRC library function -- this is the CRC that AUTOSAR E2E
    # Profile 1 actually uses (PRS_E2EProtocol table 6.1): the same
    # SAE-J1850 polynomial as CRC8, but with a start value and XOR value of
    # 0x00 rather than the library's 0xFF. The spec is explicit that E2E
    # applies additional XOR-0xFF operations precisely to undo the
    # library's values, so a genuine Profile 1 message does not match the
    # "CRC8" entry above and needs this one.
    "CRC8P01":  dict(width=8,  poly=0x1D,       init=0x00,       refin=False, refout=False, xorout=0x00),
}

# Sample size for the cheap first pass (variability prefilter, "no Data ID"
# check, and the bounded Data-ID brute force) before a promising candidate
# is re-verified against the *entire* trace. Both this and the match
# threshold bound the otherwise-expensive Data-ID search to a manageable
# cost per candidate position.
_CRC_SAMPLE_SIZE = 20
_CRC_MATCH_THRESHOLD = 0.95

# AUTOSAR E2E's Data ID is only folded in as "one extra input byte" this
# simply for the 1-byte-wide CRC profiles (Profile 1/2's Crc_CalculateCRC8
# is called a second time with the first result as its start value, over
# the Data ID's low byte -- mathematically identical to appending it to the
# input). Wider CRCs' Data ID handling varies by profile and isn't folded
# in this uniformly, so brute-forcing it here would be both more expensive
# and less trustworthy -- only these two get the brute force.
_CRC_DATA_ID_ALGOS = ("CRC8", "CRC8H2F", "CRC8P01")

# AUTOSAR E2E Profile 2 does not protect a message with one Data ID: it
# holds a *list* of them, and picks entry [counter] for each frame. So the
# CRC over byte-identical data differs from frame to frame, and no single
# constant Data ID matches anywhere -- the search in _match_crc_algorithm
# cannot find a Profile 2 message no matter how long it runs, and such a
# message can only ever fall through to the unnamed behavioural guess.
# Observed on a modern vehicle capture: of 331 checksums found, 299 landed
# at the canonical Profile 1/2 layout (CRC in byte 0, counter in byte 1's
# low nibble) yet went unnamed for exactly this reason.
#
# The list is recoverable, because the counter that indexes it is already
# known by the time the CRC search runs: partition the frames by counter
# value and each partition has a single constant Data ID again, solvable
# on its own. Bounded by only ever indexing a counter of at most this many
# bits -- Profile 1/2 counters are 4-bit, and a list indexed by a wider
# counter is not a scheme that exists.
_CRC_DATA_ID_LIST_MAX_COUNTER_BITS = 4

# Frames a counter partition needs before its Data ID is worth solving, and
# how many partitions must be present. Both guard against "solving" a list
# from partitions so thin that a byte matches them by chance.
_CRC_DATA_ID_LIST_MIN_PER_GROUP = 4
_CRC_DATA_ID_LIST_MIN_GROUPS = 4

# Frames alone are not evidence -- the *protected data* has to vary.
#
# For any fixed data block D, the map from Data ID to CRC(D + [id]) is a
# bijection over GF(2)^8: appending one byte and shifting is invertible.
# So if every frame in a counter partition carries identical data, a Data
# ID exists for *every* algorithm and every observed CRC value, and
# "solving" the partition proves nothing whatsoever. Measured on a real
# capture: messages whose partitions held one distinct payload resolved
# under CRC8 and CRC8H2F alike, and the reported algorithm was decided
# purely by which was tried first, while messages whose partitions varied
# resolved under exactly one.
#
# A partition is therefore only *informative* once it holds two or more
# distinct protected payloads, each additional one being an independent
# 8-bit constraint. Requiring several informative partitions puts the odds
# of a wrong algorithm satisfying all of them at roughly 256^-4, while
# still allowing the quiet partitions of a mostly-idle message to have
# their Data IDs read off once the algorithm is established.
_CRC_DATA_ID_LIST_MIN_DISTINCT = 2
_CRC_DATA_ID_LIST_MIN_INFORMATIVE = 4


def _reflect(value: int, width: int) -> int:
    """Bit-reverse `value` within `width` bits (CRC refin/refout)."""
    r = 0
    for _ in range(width):
        r = (r << 1) | (value & 1)
        value >>= 1
    return r


def _crc_autosar(data: bytes, algo: str, extra_byte: int | None = None) -> int:
    """Compute the named AUTOSAR CRC algorithm over `data`. `extra_byte`,
    when given, models a 1-byte Data ID folded in after `data` -- see
    _CRC_DATA_ID_ALGOS.
    """
    params = _AUTOSAR_CRC_ALGORITHMS[algo]
    width = int(params["width"])
    poly = int(params["poly"])
    mask = (1 << width) - 1
    top_bit = 1 << (width - 1)
    crc = int(params["init"]) & mask
    payload = data if extra_byte is None else bytes(data) + bytes([extra_byte])
    for byte in payload:
        b = _reflect(byte, 8) if params["refin"] else byte
        crc ^= (b << (width - 8)) & mask
        for _ in range(8):
            if crc & top_bit:
                crc = ((crc << 1) ^ poly) & mask
            else:
                crc = (crc << 1) & mask
    if params["refout"]:
        crc = _reflect(crc, width)
    return (crc ^ int(params["xorout"])) & mask


# ── Simple whole-frame checksum family ──────────────────────────────
#
# What pre-AUTOSAR ECUs overwhelmingly use instead of a CRC: one byte
# chosen so that a trivial aggregate over the *entire* frame comes out at a
# fixed constant. Expressed as an invariant over the whole payload rather
# than as "byte X = f(the others)", because that form is symmetric and so
# checkable without first knowing which byte is the checksum:
#
#   SUM8:  (b0 + b1 + ... + bn) & 0xFF == K   =>  check = (K - SUM(others)) & 0xFF
#   XOR8:   b0 ^ b1 ^ ... ^ bn      == K      =>  check = K ^ XOR(others)
#
# Verified the same way the CRC family is -- against every frame of the
# capture -- which is what separates this from the behavioural fallback in
# _looks_like_checksum(): the formula is named and exact, not guessed.
_SIMPLE_CHECKSUM_ALGORITHMS = ("SUM8", "XOR8")

# How AUTOSAR E2E folds a *16-bit* Data ID into a wide CRC, recorded here
# because it is not currently searched and the ordering is easy to get
# wrong. Profiles 5 and 6 append it after the data as two separate
# one-byte CRC calls, HIGH byte first then low (PRS_E2E_00421 and the
# E2E_P05Protect/E2E_P06Protect diagrams). Profile 1's DATAID_BOTH mode
# appends both bytes too but in the opposite order, LOW then high
# (PRS_E2EProtocol 6.3.11). Searching for one would mean brute-forcing
# 65536 values rather than 256; doing that affordably needs a
# continue-from-state CRC primitive, since recomputing the whole payload
# per guess is far too slow. Deferred deliberately: no capture available
# has produced a single CRC16 match, so there is nothing to test such a
# search against.
_WIDE_DATA_ID_NOTE = "P05/P06 append DataID high byte then low; P01 BOTH appends low then high"

# A payload that barely changes satisfies *every* invariant trivially: a
# frame whose bytes are identical in all frames trivially has a constant
# sum and a constant XOR, and reporting a checksum there would be
# meaningless. Observed on real captures -- of 33 Opel Astra messages whose
# aggregate is constant, 31 have exactly one distinct payload across 2.7M
# frames. Require real variety before believing the invariant means
# anything.
_SIMPLE_CHECKSUM_MIN_DISTINCT = 20

# Reject a checksum candidate whose own frame-to-frame step is this
# dominated by a single constant value -- see _looks_like_checksum() check
# 4. The observed margin is wide enough that the exact figure barely
# matters: genuine checksums measured 18.5% and 27.0%, ramps exactly 100%.
_CHECKSUM_MAX_CONSTANT_STEP = 0.9


def _simple_checksum_aggregate(payload: bytes, algo: str) -> int:
    """SUM8 / XOR8 aggregate over a whole frame -- see
    :data:`_SIMPLE_CHECKSUM_ALGORITHMS`."""
    if algo == "SUM8":
        return sum(payload) & 0xFF
    acc = 0
    for b in payload:
        acc ^= b
    return acc


# Best-effort AUTOSAR E2E Profile hint from (counter_length_bits,
# crc_algorithm), checked against AUTOSAR_PRS_E2EProtocol (FO R19-11),
# section 6 "Specification of E2E Profile N" and the per-profile mechanism
# tables 6.1 / 6.14 / 6.20 / 6.27 / 6.34 / 6.41 / 6.48 / 6.58. What the
# spec says each profile actually uses:
#
#   P01   counter 4-bit (mod 15, so 0..14)   CRC poly 0x1D, init/xor 0x00
#   P02   counter 4-bit (0..15)              CRC poly 0x2F + 16-entry Data ID list
#   P04   counter 16-bit                     CRC-32 poly 0xF4ACFB13
#   P05   counter 8-bit                      CRC-16 poly 0x1021
#   P06   counter 8-bit                      CRC-16 poly 0x1021  (+ Length field)
#   P07   counter 32-bit                     CRC-64 poly 0x42F0E1EBA9EA3693
#   P11   counter 4-bit                      CRC poly 0x1D
#   P22   counter 4-bit                      CRC poly 0x2F + 16-entry Data ID list
#
# Three earlier entries were wrong against that and have been removed or
# corrected:
#
#   (8, CRC16ARC) -> P06.  P06 uses poly 0x1021, the same CRC as P05 --
#       CRC16ARC (poly 0x8005) is used by no profile at all. P05 and P06
#       are separated by a Length field, not by their CRC, so the two are
#       not distinguishable on (counter width, algorithm) and now share a
#       single combined label.
#   (8, CRC32P4) -> P07.  P07 is a 32-bit counter with a 64-bit CRC, so
#       this combination describes no profile. CRC64 is deliberately out of
#       scope for _AUTOSAR_CRC_ALGORITHMS, which makes P07 undetectable
#       here by construction; the entry is dropped rather than left to fire
#       on something else.
#   (4, CRC8) -> P01.  The parameters do not match: P01's CRC uses start
#       and XOR values of 0x00, while "CRC8" here is the CRC library's
#       Crc_CalculateCRC8 with 0xFF for both. The spec is explicit that E2E
#       applies extra XOR-0xFF operations precisely to undo the library's
#       values, so a genuine P01 message cannot match this entry's
#       algorithm. What it does match is P11, which uses the same poly 0x1D
#       and the same 4-bit counter.
#
# Every remaining entry names every profile that fits, because a passive
# capture cannot separate profiles that differ only in framing or Data ID
# convention. Still a hint, not a classification: only the CRC parameters
# themselves are verified (_AUTOSAR_CRC_ALGORITHMS), never the full E2E
# protocol behaviour.
_E2E_PROFILE_HINTS: dict[tuple[int, str], str] = {
    # Poly 0x1D with the library's 0xFF/0xFF is P11's CRC, not P01's.
    (4, "CRC8"): "E2E_Profile_11",
    # ...and poly 0x1D with 0x00/0x00 is P01's, which is what CRC8P01 is.
    (4, "CRC8P01"): "E2E_Profile_1",
    # P02 and P22 are mechanically identical here -- 4-bit counter, poly
    # 0x2F, 16 Data IDs indexed by the counter. Nothing in a passive
    # capture separates them.
    (4, "CRC8H2F"): "E2E_Profile_2_or_22",
    (16, "CRC32P4"): "E2E_Profile_4",
    # P05 and P06 share poly 0x1021 and an 8-bit counter; only P06's
    # Length field distinguishes them, and that is not read here.
    (8, "CRC16"): "E2E_Profile_5_or_6",
}

# A message pairing a counter with *some* checksum right next to it is
# itself strong evidence of E2E protection, independent of whether the
# exact profile number could be pinned down -- either the checksum's
# algorithm was identified but the (counter width, algorithm) combination
# isn't one of the standard profiles above, or it was only recognized by
# _looks_like_checksum()'s behavioral fallback (formula unknown entirely).
# Kept distinct from a real "E2E_Profile_N" string so callers (e.g. the
# PDU DB export's `isE2E`, which is a real profile number or 0) can tell
# "protected, profile unknown" apart from an actual identified profile.
E2E_UNKNOWN_PROFILE = "E2E_Unknown"


def guess_e2e_profile(counter: DiscoveredSignal, crc: DiscoveredSignal) -> str:
    """Best-effort AUTOSAR E2E Profile hint for a (counter, CRC/checksum)
    pair found on the same message -- see _E2E_PROFILE_HINTS, which records
    what the spec says each profile uses and which combinations are
    genuinely ambiguous. Returns the matching profile name (which may name
    two profiles, where a passive capture cannot separate them),
    otherwise E2E_UNKNOWN_PROFILE -- callers already only call this once
    both a counter and a checksum have been found on the same message, so
    there's always *something* to report here.
    """
    if crc.crc_algorithm is not None:
        hint = _E2E_PROFILE_HINTS.get((counter.length, crc.crc_algorithm))
        if hint:
            return hint
    return E2E_UNKNOWN_PROFILE


def _e2e_profile_number(e2e_profile: str | None) -> int:
    """Extract the bare profile number from an "E2E_Profile_N" hint string
    for the PDU DB schema's `isE2E` field (an AUTOSAR E2E profile number,
    or 0 if none) -- 0 if no hint was set or it doesn't parse.

    Reads the number *directly after* the prefix, not the last underscored
    token, because some hints name more than one profile: a passive capture
    cannot separate P02 from P22, or P05 from P06, so those labels read
    "E2E_Profile_2_or_22" and "E2E_Profile_5_or_6". Taking the last token
    would export 22 and 6 -- picking the higher number purely by accident
    of how the label is spelled. The schema field holds a single number, so
    the first (and lower) is reported, and the full ambiguity stays visible
    on the signal's own `e2e_profile` string.
    """
    prefix = "E2E_Profile_"
    if not e2e_profile or not e2e_profile.startswith(prefix):
        return 0
    head = e2e_profile[len(prefix):].split("_", 1)[0]
    return int(head) if head.isdigit() else 0


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
        """Run the full reverse engineering pipeline (all stages together,
        in order). For the staged web UI -- where each stage is run and
        timed independently -- call :meth:`find_counters`,
        :meth:`find_crcs`, and :meth:`find_application_signals` directly
        instead; this method is the right entry point for
        non-interactive/CLI use (:meth:`to_pdu_db`, :meth:`save_pdu_db`).
        """
        if self._analysis is None:
            raise RuntimeError("Call analyzer.analyze() before reverse_engineer()")

        counters_by_id = self.find_counters()
        crcs_by_id = self.find_crcs(counters_by_id)
        # Annotates the selector signals in place, so the flags travel with
        # the same objects into combine_results() and out to the export.
        self.find_multiplexors(counters_by_id, crcs_by_id)
        app_signals_by_id = self.find_application_signals(counters_by_id, crcs_by_id)
        return self.combine_results(counters_by_id, app_signals_by_id, crcs_by_id)

    def combine_results(
        self,
        counters_by_id: dict[int, list[DiscoveredSignal]] | None = None,
        app_signals_by_id: dict[int, list[DiscoveredSignal]] | None = None,
        crcs_by_id: dict[int, list[DiscoveredSignal]] | None = None,
    ) -> ReverseEngineeringResult:
        """Merge staged results into a :class:`ReverseEngineeringResult`
        covering every CAN ID -- the same shape :meth:`reverse_engineer`
        returns, but usable directly with whatever's actually been computed
        so far. A caller that ran :meth:`find_counters`/:meth:`find_crcs`/
        :meth:`find_application_signals` independently (e.g. the staged web
        UI, caching each stage's result as it completes) calls this to
        assemble an exportable result without recomputing anything; any
        argument may be omitted if that stage hasn't run yet. When both a
        counter and a CRC are present for a message, also sets
        :attr:`ReverseEngineeredMessage.e2e_profile` via
        :func:`guess_e2e_profile`.
        """
        if self._analysis is None:
            raise RuntimeError("Call analyzer.analyze() before combine_results()")

        counters_by_id = self._corroborated_counters(counters_by_id or {}, crcs_by_id)
        app_signals_by_id = app_signals_by_id or {}
        crcs_by_id = crcs_by_id or {}

        result = ReverseEngineeringResult(numpy_available=_HAS_NUMPY)

        for aid in sorted(self._analysis.can_stats.keys()):
            s = self._analysis.can_stats[aid]
            counters = counters_by_id.get(aid, [])
            crcs = crcs_by_id.get(aid, [])
            # Each stage already post-processed (named/ordered) its own
            # signals independently, so e.g. a genuinely counter-shaped
            # value _merge_adjacent_smooth_signals() assembled in stage 3
            # (find_application_signals) and the real counter stage 2
            # (find_counters) found would both be independently named
            # "Counter_1" -- re-running _post_process_signals on the fully
            # merged, cross-stage list renumbers everything from scratch so
            # names stay unique and sequential in the combined result.
            discovered_signals = self._post_process_signals(
                counters + crcs + app_signals_by_id.get(aid, []), s
            )

            length = max(s.dlc_values) if s.dlc_values else 8
            cycle_ms = self._analysis.cycle_times_ms.get(aid, 0)
            # Cyclic when it keeps a period. Otherwise OnChange if nearly
            # every frame carries different content -- the sender is
            # reacting to something rather than following a schedule --
            # and Spontaneous when neither holds.
            if cycle_ms:
                send_type = "Cyclic"
            elif (
                s.timing is not None
                and s.timing.change_fraction >= TraceAnalyzer._ONCHANGE_MIN_CHANGE_FRACTION
            ):
                send_type = "OnChange"
            else:
                send_type = "Spontaneous"

            e2e_profile = None
            if counters and crcs:
                e2e_profile = guess_e2e_profile(counters[0], crcs[0])

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
                send_type=send_type,
                signals=discovered_signals,
                e2e_profile=e2e_profile,
            )
            result.messages.append(msg)
            result.total_signals_discovered += len(discovered_signals)

        result.total_can_ids = len(result.messages)
        return result

    # ── Staged analysis: independently runnable, independently cacheable ──
    #
    # Split so a caller (the web UI in particular) can run and time each
    # stage on its own instead of one long blocking call: stage 2 (counters,
    # then CRCs) has an exact, directly-checkable signature and is
    # deliberately run before stage 3 (generic clustering) so those bits
    # never get re-absorbed or re-split by the statistical clustering pass.
    # find_crcs() runs after find_counters() and takes its result as input --
    # CRC fields are searched *relative to* counter positions, not
    # independently (see find_crcs()'s docstring).

    def find_counters(self) -> dict[int, list[DiscoveredSignal]]:
        """Stage 2: dedicated counter scan across every CAN ID.

        Returns discovered counters keyed by CAN ID (IDs with none found are
        omitted). Independent of :meth:`find_application_signals` -- can run
        before it, after it, or not at all.
        """
        if self._analysis is None:
            raise RuntimeError("Call analyzer.analyze() before find_counters()")

        result: dict[int, list[DiscoveredSignal]] = {}
        for aid, s in self._analysis.can_stats.items():
            if not s.payload_samples or s.count < 2:
                continue
            raw_values = self._compute_raw_values(s)
            max_len = max(len(p) for p in s.payload_samples)
            total_bits = max_len * 8

            counter_signals, _claimed = self._scan_for_counters(s, raw_values, total_bits, 1)
            counter_signals = [
                sig for sig in counter_signals if sig.confidence >= self._min_confidence
            ]
            if counter_signals:
                result[aid] = self._post_process_signals(counter_signals, s)
        return result

    @staticmethod
    def _is_ordinary_stride(counter: DiscoveredSignal) -> bool:
        """Does this counter simply count, up or down?

        A stride of 1 or of 2^width - 1 (which is -1 modulo the width) is an
        ordinary counter. Across both pre-AUTOSAR captures every genuine
        counter found is one of those two -- 22 counting up and 9 counting
        down on the Opel Astra, and the Renault Clio's single real one.
        """
        if counter.counter_stride is None:
            return True
        return counter.counter_stride in (1, (1 << counter.length) - 1)

    # Largest stride the cycle-time corroboration will entertain. A stride
    # is a decimation factor -- one frame in k reaching this bus -- and
    # gateways drop frames at small ratios, not at 1-in-107. Without this
    # bound the test degenerates: divide a 100 ms period by a large enough
    # stride and the result lands near *some* raster value by arithmetic
    # alone. Renault Clio 0x511's ramps (strides 13 to 227 on a 100 ms
    # message) are the case that showed it -- 100/107 = 0.93 ms sits inside
    # 10% of the 1 ms raster entry and would have been "corroborated".
    _MAX_DECIMATION_STRIDE = 16

    def _stride_matches_cycle_time(self, can_id: int, counter: DiscoveredSignal) -> bool:
        """Does this counter's stride agree with how often the message was
        actually seen?

        A counter advances once per *transmission*. So a message observed
        every T ms whose counter advances by k was really sent every T/k ms,
        and that implied native period should be a value a scheduler would
        actually use -- one of the raster steps stage 1 already snaps cycle
        times to. When it is, the stride and the timing tell the same story
        from two independent directions.

        Message 0x112 on a modern vehicle is the worked example: stride 5,
        observed every 50.00 ms, implying 10 ms natively -- which is a
        raster value, and the rate its neighbours on that bus actually run
        at. Nothing about the payload was consulted to reach that.

        Only asked about unusual strides; +/-1 needs no corroboration.
        """
        stride = counter.counter_stride
        if stride is None or self._is_ordinary_stride(counter):
            return True
        if stride > self._MAX_DECIMATION_STRIDE:
            return False
        if self._analysis is None:
            return False
        observed = self._analysis.cycle_times_ms.get(can_id, 0)
        if not observed:
            return False   # not periodic, so there is no period to divide
        native = observed / stride
        return any(
            abs(native - raster) <= raster * 0.1
            for raster in TraceAnalyzer._CANONICAL_CYCLE_TIMES_MS
        )

    def _corroborated_counters(
        self,
        counters_by_id: dict[int, list[DiscoveredSignal]],
        crcs_by_id: dict[int, list[DiscoveredSignal]] | None,
    ) -> dict[int, list[DiscoveredSignal]]:
        """Drop counters whose stride is neither +1 nor -1 unless something
        else on the message depends on them being a counter.

        Allowing any odd stride (see :meth:`_counter_stride`) is what makes
        a counter visible through a capture that misses transmissions, and
        it recovered a real message -- but it also lets a block of unrelated
        constant-stride ramps read as a row of counters. Renault Clio 0x511
        is exactly that: six bytes advancing by 13, 63, 107, 185, 201 and
        227 on every single frame, each a flawless constant stride and none
        of them a counter.

        What separates the two is not how regular they look, because both
        are perfectly regular. It is whether anything *uses* the value. On
        the modern-vehicle message 0x112 the stride-5 field indexes a CRC
        Data ID list that then verifies exactly across the whole capture --
        the CRC only resolves because that field is the counter. Nothing
        whatsoever depends on 0x511's six fields. So an unusual stride is
        accepted only where stage 2.5 has named a checksum on the same
        message, which is precisely that dependency.

        The bits of a dropped counter are *not* claimed, so they return to
        stage 3 to be clustered as ordinary signals, which is what a ramp
        should be treated as.

        There are two independent ways to corroborate, and either suffices:
        a checksum named on the same message (that checksum only resolved
        because the field is the counter), or agreement between the stride
        and the message's observed period (see
        :meth:`_stride_matches_cycle_time`). The second works on messages
        carrying no checksum at all, which the first cannot reach.

        `crcs_by_id` of None means the checksum stage has not run, so there
        is nothing to corroborate against and every counter is kept -- a
        caller running stage 2 alone still sees its full result.
        """
        if crcs_by_id is None:
            return counters_by_id
        kept: dict[int, list[DiscoveredSignal]] = {}
        for aid, counters in counters_by_id.items():
            named = any(sig.crc_algorithm for sig in crcs_by_id.get(aid, []))
            surviving = [
                c for c in counters
                if named
                or self._is_ordinary_stride(c)
                or self._stride_matches_cycle_time(aid, c)
            ]
            if surviving:
                kept[aid] = surviving
        return kept

    def find_application_signals(
        self,
        counters_by_id: dict[int, list[DiscoveredSignal]] | None = None,
        crcs_by_id: dict[int, list[DiscoveredSignal]] | None = None,
    ) -> dict[int, list[DiscoveredSignal]]:
        """Stage 3: generic bit-correlation clustering for application
        signals across every CAN ID.

        `counters_by_id`/`crcs_by_id` (typically :meth:`find_counters`'s
        and :meth:`find_crcs`'s own return values) exclude each ID's
        already-claimed counter/CRC bits from clustering; omit either (or
        both) to cluster those bits too, same as if that stage hadn't run.

        When both are given, :meth:`_corroborated_counters` first discards
        counters with an unusual stride that nothing corroborates, so their
        bits are clustered here as the ordinary signals they are.
        """
        if self._analysis is None:
            raise RuntimeError("Call analyzer.analyze() before find_application_signals()")

        counters_by_id = self._corroborated_counters(counters_by_id or {}, crcs_by_id)
        crcs_by_id = crcs_by_id or {}
        result: dict[int, list[DiscoveredSignal]] = {}
        for aid, s in self._analysis.can_stats.items():
            if not s.payload_samples or s.count < 2:
                continue

            claimed: set[int] = set()
            next_sig_id = 1
            for sig in counters_by_id.get(aid, []) + crcs_by_id.get(aid, []):
                claimed.update(range(sig.start_pos, sig.start_pos + sig.length))
                next_sig_id += 1

            raw_values = self._compute_raw_values(s)
            selector = next(
                (c for c in counters_by_id.get(aid, [])
                 if c.is_mux_selector and c.multiplexed_bytes),
                None,
            )
            if selector is not None:
                app_signals = self._cluster_per_variant(s, claimed, selector, next_sig_id)
            else:
                app_signals = self._cluster_application_signals(
                    s, raw_values, claimed, next_sig_id
                )
            app_signals = [
                sig for sig in app_signals if sig.confidence >= self._min_confidence
            ]
            if app_signals:
                result[aid] = self._post_process_signals(app_signals, s)
        return result

    # A message needs this many frames, and each selector value this many
    # of them, before its partitioning means anything.
    _MUX_MIN_FRAMES = 100
    _MUX_MIN_PER_GROUP = 10

    def find_multiplexors(
        self,
        counters_by_id: dict[int, list[DiscoveredSignal]],
        crcs_by_id: dict[int, list[DiscoveredSignal]] | None = None,
    ) -> dict[int, DiscoveredSignal]:
        """Stage 2.7: decide which counters are really multiplexor
        selectors, and which bytes they select between.

        A selector and a rolling counter look identical in isolation --
        both are a small field cycling through its values. What separates
        them is what the *rest* of the frame does: partition the frames by
        the field's value, and a selector's other bytes fall into disjoint
        value sets, because the same bits carry a different quantity in
        each variant. A plain counter leaves them alone.

        Deliberately not requiring the multiplexed bytes to *vary* within a
        partition. A mux over four doors, four windows or four seat sensors
        reports the same constant for each variant all run long if nothing
        changes state, and demanding variation would miss exactly the
        common case. Disjointness is the test; motion is not.

        Bits already claimed by a counter or a checksum are masked out of
        the comparison -- at bit level, so a byte is still examined through
        whatever remains free. Both exclusions matter and for different
        reasons: on an otherwise static message the CRC is a pure function
        of the counter and would partition perfectly, and a second counter
        running in lockstep with the selector makes its entire byte look
        disjoint while carrying no variant-specific content whatsoever.
        Excluding whole *bytes* instead would go too far the other way,
        discarding bytes that hold a small counter alongside genuine
        multiplexed content.

        This matters beyond naming. Stage 3 clusters bit correlations
        across *all* frames at once, so on a multiplexed message it merges
        bits belonging to different variants into signals that do not
        exist. Knowing the selector first is what makes it possible to
        cluster each variant separately.
        """
        if self._analysis is None:
            raise RuntimeError("Call analyzer.analyze() before find_multiplexors()")

        counters_by_id = self._corroborated_counters(counters_by_id or {}, crcs_by_id)
        crcs_by_id = crcs_by_id or {}
        found: dict[int, DiscoveredSignal] = {}

        for aid in sorted(counters_by_id):
            counters = counters_by_id[aid]
            stats = self._analysis.can_stats.get(aid)
            if stats is None or not stats.payload_samples:
                continue
            width = max(stats.dlc_values)
            frames = [p for p in stats.payload_samples if len(p) == width]
            if len(frames) < self._MUX_MIN_FRAMES:
                continue

            # Masked at bit level, not byte level. A byte is only evidence
            # of multiplexing through the bits that are actually free: a
            # checksum is a pure function of the counter on a static
            # message and would partition perfectly, and a *second* counter
            # running in lockstep with the selector makes its whole byte
            # look disjoint while carrying no variant content at all.
            # Message 0x1E1 is the latter -- its selected bytes read
            # 0x1C/0x10/0x14/0x18, differing only in the two bits holding
            # another counter. Masking those leaves nothing, which is the
            # correct answer.
            claimed_bits: set[int] = set()
            for sig in counters + crcs_by_id.get(aid, []):
                claimed_bits.update(range(sig.start_pos, sig.start_pos + sig.length))

            byte_mask = {}
            for b in range(width):
                m = 0
                for pos in range(b * 8, b * 8 + 8):
                    if pos not in claimed_bits:
                        m |= 1 << (7 - (pos % 8))
                byte_mask[b] = m

            for counter in counters:
                bits = list(range(counter.start_pos, counter.start_pos + counter.length))
                values = self._extract_raw_numbers(bits, counter.byte_order, stats)
                groups: dict[int, list] = defaultdict(list)
                for payload, value in zip(stats.payload_samples, values):
                    if len(payload) == width:
                        groups[value].append(payload)
                usable = [
                    v for v in sorted(groups)
                    if len(groups[v]) >= self._MUX_MIN_PER_GROUP
                ]
                if len(usable) < 2:
                    continue

                partitioned: list[int] = []
                for b in range(width):
                    mask = byte_mask[b]
                    if not mask:
                        continue
                    sets = [{p[b] & mask for p in groups[v]} for v in usable]
                    if len({frozenset(x) for x in sets}) == 1:
                        continue          # every variant sees the same values
                    if all(
                        not (sets[i] & sets[j])
                        for i in range(len(sets))
                        for j in range(i + 1, len(sets))
                    ):
                        partitioned.append(b)

                if partitioned:
                    counter.is_mux_selector = True
                    counter.multiplexed_bytes = partitioned
                    found[aid] = counter
                    break
        return found

    def find_crcs(
        self, counters_by_id: dict[int, list[DiscoveredSignal]]
    ) -> dict[int, list[DiscoveredSignal]]:
        """Stage 2.5: AUTOSAR CRC scan, run after and informed by
        :meth:`find_counters`.

        Two passes. The first is *anchored*: CRC fields are searched
        byte-aligned near each counter's own byte position (AUTOSAR
        convention keeps them adjacent within a message), which is both
        where they nearly always are and cheap enough to search
        exhaustively across every algorithm and Data ID.

        The second is the *unanchored* fallback, for every message the
        first pass left without an exactly-named checksum -- including
        every message with no counter at all, which the anchored pass
        cannot look at, having nowhere to anchor. Those messages previously
        got no CRC detection whatsoever, and their CRC byte, being
        high-entropy, was then shredded by stage 3's clustering into a row
        of meaningless one-bit "State" flags. See
        :meth:`_find_crc_unanchored` for how the search is kept affordable
        now that it is unconstrained, and :meth:`_find_simple_checksum` for
        the SUM8/XOR8 family it falls through to -- the scheme pre-AUTOSAR
        ECUs use, and the only one that matches on older vehicles where
        every CRC algorithm draws a blank.
        """
        if self._analysis is None:
            raise RuntimeError("Call analyzer.analyze() before find_crcs()")

        result: dict[int, list[DiscoveredSignal]] = {}
        for aid, counters in counters_by_id.items():
            if not counters:
                continue
            stats = self._analysis.can_stats.get(aid)
            if not stats or not stats.payload_samples:
                continue
            lengths = self._protected_length_candidates(stats)
            active_len = self._active_payload_length(stats)
            claimed = {
                b for c in counters for b in range(c.start_pos, c.start_pos + c.length)
            }

            crc_signals: list[DiscoveredSignal] = []
            sig_id = 1
            for counter in counters:
                sig = None
                for length in lengths:
                    sig = self._find_crc_for_counter(stats, counter, claimed, length, sig_id)
                    if sig is not None:
                        break
                if sig is None:
                    # Every candidate length failed to produce an exact
                    # match, so the formula stays unknown -- but the field
                    # may still be recognisable by behaviour alone.
                    sig = self._find_checksum_by_behavior(
                        stats,
                        counter.start_pos // 8,
                        (counter.start_pos + counter.length + 7) // 8,
                        claimed,
                        active_len,
                        sig_id,
                    )
                if sig is not None:
                    crc_signals.append(sig)
                    claimed.update(range(sig.start_pos, sig.start_pos + sig.length))
                    sig_id += 1

            crc_signals = [
                sig for sig in crc_signals if sig.confidence >= self._min_confidence
            ]
            if crc_signals:
                result[aid] = self._post_process_signals(crc_signals, stats)

        for aid, stats in self._analysis.can_stats.items():
            # An ID the anchored pass named an exact algorithm for is
            # settled. One it only reached via _find_checksum_by_behavior
            # (crc_algorithm unset -- "checksum-shaped, formula unknown")
            # is not: an exact match verified across the whole trace is far
            # stronger evidence than that behavioral guess, and it can name
            # the algorithm, so let the unanchored scan try to supersede it.
            existing = result.get(aid)
            if existing and any(sig.crc_algorithm for sig in existing):
                continue
            if len(stats.payload_samples) < self._UNANCHORED_CRC_MIN_FRAMES:
                continue
            claimed = {
                b
                for c in counters_by_id.get(aid, [])
                for b in range(c.start_pos, c.start_pos + c.length)
            }
            sig = None
            for length in self._protected_length_candidates(stats):
                sig = self._find_crc_unanchored(stats, length, 1, claimed)
                if sig is not None:
                    break
            if sig is None:
                for length in self._protected_length_candidates(stats):
                    sig = self._find_simple_checksum(stats, length, 1, claimed)
                    if sig is not None:
                        break
            if sig is not None and sig.confidence >= self._min_confidence:
                result[aid] = self._post_process_signals([sig], stats)

        return result

    # Frames a variant needs before it is worth clustering on its own.
    _MUX_VARIANT_MIN_FRAMES = 30

    def _cluster_per_variant(
        self,
        stats: CanIdStats,
        claimed: set[int],
        selector: DiscoveredSignal,
        start_sig_id: int,
    ) -> list[DiscoveredSignal]:
        """Cluster a multiplexed message one variant at a time.

        Clustering correlates bit transitions across the whole capture at
        once. On a multiplexed message that is simply the wrong population:
        the same bits carry a different quantity depending on the selector,
        so correlating them across all frames mixes several signals
        together and yields fields that exist in no variant. Splitting the
        frames by selector value first is what makes the correlation
        meaningful.

        The frame divides in two. Bytes the selector does not select are
        present in every variant, so they are clustered once over the whole
        capture and carry `mux_value=None`. The multiplexed bytes are
        clustered separately within each selector value, and those signals
        carry that value -- which is what fills the PDU database's
        `MuxValue`.
        """
        width = max(stats.dlc_values) if stats.dlc_values else 0
        mux_bits = {
            b for byte in (selector.multiplexed_bytes or [])
            for b in range(byte * 8, byte * 8 + 8)
        }
        if not mux_bits:
            return []
        all_bits = set(range(width * 8))

        signals: list[DiscoveredSignal] = []
        sig_id = start_sig_id

        # Everything outside the multiplexed region: one pass, all frames.
        static = self._cluster_application_signals(
            stats, self._compute_raw_values(stats), claimed | mux_bits, sig_id
        )
        signals.extend(static)
        sig_id += len(static)

        selector_bits = list(
            range(selector.start_pos, selector.start_pos + selector.length)
        )
        values = self._extract_raw_numbers(selector_bits, selector.byte_order, stats)
        by_variant: dict[int, list] = defaultdict(list)
        for payload, value in zip(stats.payload_samples, values):
            if len(payload) == width:
                by_variant[value].append(payload)
        variants = {
            v: self._variant_stats(stats, frames, width)
            for v, frames in sorted(by_variant.items())
            if len(frames) >= self._MUX_VARIANT_MIN_FRAMES
        }
        if not variants:
            return signals

        covered: set[int] = set()
        per_variant: dict[int, list[DiscoveredSignal]] = {}
        for value, variant in variants.items():
            found = self._cluster_application_signals(
                variant, self._compute_raw_values(variant),
                claimed | (all_bits - mux_bits), sig_id,
            )
            sig_id += len(found)
            for sig in found:
                sig.mux_value = value
                covered.update(range(sig.start_pos, sig.start_pos + sig.length))
            per_variant[value] = found
        for found in per_variant.values():
            signals.extend(found)

        # Clustering only reports bits that *change*, and in a status
        # multiplexor they do not: each variant carries one fixed value --
        # four doors that stay shut report the same constant all run long.
        # The constant is the finding, so whatever the clustering left
        # uncovered is read out directly. On real messages 0x1E1, 0x0D1 and
        # 0x1C8 every selected byte is constant within its variant, so
        # without this the commonest kind of mux yields nothing at all.
        for byte in sorted(selector.multiplexed_bytes or []):
            # Take the parts of the byte nothing else has taken, rather
            # than skipping the byte whenever any bit is spoken for: a
            # selected byte often carries a small counter too (0x1E1 keeps
            # one inside each of its two), and dropping the whole byte on
            # that basis discards the values the selector switches.
            free = [
                b for b in range(byte * 8, byte * 8 + 8)
                if b not in covered and b not in claimed
            ]
            for run in self._find_contiguous_groups(free):
                built = {
                    value: self._build_signal(
                        sig_id, run, self._compute_raw_values(variant), variant
                    )
                    for value, variant in variants.items()
                }
                built = {v: sig for v, sig in built.items() if sig is not None}
                if not built:
                    continue
                # A field that reads the same under every selector value is
                # not multiplexed -- it is an ordinary field that happens to
                # sit inside a selected byte. Reporting it once per variant
                # would multiply one signal into four identical rows.
                readings = {
                    tuple(sig.raw_values) for sig in built.values()
                }
                if len(readings) == 1:
                    only = next(iter(built.values()))
                    only.id = sig_id
                    signals.append(only)
                    sig_id += 1
                else:
                    for value, sig in built.items():
                        sig.id = sig_id
                        sig.mux_value = value
                        signals.append(sig)
                        sig_id += 1

        return signals

    @staticmethod
    def _variant_stats(stats: CanIdStats, frames: list, width: int) -> CanIdStats:
        """A CanIdStats holding just one variant's frames, so the ordinary
        clustering and signal-building code can run against it unchanged."""
        variant = CanIdStats(
            channel=stats.channel,
            arbitration_id=stats.arbitration_id,
            is_extended=stats.is_extended,
            is_fd=stats.is_fd,
        )
        variant.payload_samples = frames
        variant.dlc_values = [width] * len(frames)
        variant.timestamps = [0.0] * len(frames)
        variant.count = len(frames)
        return variant

    def _cluster_application_signals(
        self,
        stats: CanIdStats,
        raw_values: list[dict[str, Any]],
        exclude: set[int],
        start_sig_id: int,
    ) -> list[DiscoveredSignal]:
        """The generic correlation-clustering pass, over whatever bits
        `exclude` (a counter scan's claimed bits, typically) doesn't cover.
        """
        signals: list[DiscoveredSignal] = []
        sig_id = start_sig_id

        bit_matrix = self._build_bit_matrix(stats)
        if bit_matrix is None or len(bit_matrix) < 2:
            return signals

        clusters = self._cluster_correlated_bits(bit_matrix, exclude=exclude)

        grouped: dict[int, list[int]] = {}
        for bit_idx, cluster_id in clusters.items():
            grouped.setdefault(cluster_id, []).append(bit_idx)

        for cluster_id in sorted(grouped.keys()):
            if cluster_id == -1:
                continue  # "not active enough to cluster" bucket -- not a real signal group
            bits = sorted(grouped[cluster_id])
            if not bits:
                continue

            contiguous = self._find_contiguous_groups(bits)
            if contiguous and len(contiguous) > 1:
                for group in contiguous:
                    sig = self._build_signal(sig_id, group, raw_values, stats)
                    if sig:
                        signals.append(sig)
                        sig_id += 1
            else:
                sig = self._build_signal(sig_id, bits, raw_values, stats)
                if sig:
                    signals.append(sig)
                    sig_id += 1

        return self._merge_adjacent_smooth_signals(signals, raw_values, stats, start_sig_id)

    @staticmethod
    def _is_smoothly_varying(raw_values: list[int], length: int) -> bool:
        """True if a (combined) multi-bit value's frame-to-frame steps are
        small relative to its full range -- the signature of one coherent
        changing quantity (a counter, sequence number, or an ordinary
        slowly-ramping physical signal), as opposed to unrelated bits that
        just happen to sit next to each other, or a high-entropy field
        (random-looking flags, a checksum) that jumps around.

        Uses *circular* distance (the shorter way around the value's own
        modulus), not a literal absolute difference -- a rolling value's
        wrap (e.g. 15 -> 0 for a 4-bit field) is a smooth +1 step, not a
        huge jump, exactly like :meth:`_detect_counter`'s masked diff.
        """
        if len(raw_values) < 5:
            return False
        full_range = (1 << length) - 1
        modulus = 1 << length
        if full_range <= 0:
            return False
        diffs = []
        for i in range(1, len(raw_values)):
            d = (raw_values[i] - raw_values[i - 1]) % modulus
            diffs.append(min(d, modulus - d))
        mean_delta = sum(diffs) / len(diffs) if diffs else 0.0
        return (mean_delta / full_range) < 0.1

    def _merge_adjacent_smooth_signals(
        self,
        signals: list[DiscoveredSignal],
        raw_values: list[dict[str, Any]],
        stats: CanIdStats,
        start_sig_id: int,
    ) -> list[DiscoveredSignal]:
        """Merge bit-adjacent clustered signals when their COMBINED value
        changes smoothly -- catches the case where correlation-based
        clustering (:meth:`_cluster_correlated_bits`) splits the
        constituent bits of one coherent multi-bit quantity into several
        spurious single-bit "flags". A binary counter's own bits don't
        pairwise correlate the way clustering looks for: each bit toggles
        at a *different* rate (related by carry, not by co-occurring
        transitions -- the LSB flips every step, the next bit every other
        step, ...), so two bits of the very same counter can easily fail
        both the numpy path's Pearson-correlation threshold and the
        pure-Python path's Jaccard-similarity-of-transitions threshold,
        landing in separate clusters (or singleton bit-adjacent clusters)
        despite obviously belonging together once you read them as one
        number. Greedily tries widening each signal into its immediate
        right-hand neighbor (both must already be plain, non-counter,
        non-checksum clustered signals) and keeps the merge only if the
        wider value is itself smoothly-varying -- a merge across two truly
        unrelated adjacent fields would show large jumps whenever either
        one moves independently, so this is self-correcting rather than
        indiscriminately merging every neighboring pair.
        """
        if len(signals) < 2:
            return signals

        ordered = sorted(signals, key=lambda s: s.start_pos)
        merged: list[DiscoveredSignal] = []
        i = 0
        while i < len(ordered):
            current = ordered[i]
            j = i + 1
            while (
                j < len(ordered)
                and not current.is_counter
                and not current.is_checksum
                and not ordered[j].is_counter
                and not ordered[j].is_checksum
                and ordered[j].start_pos == current.start_pos + current.length
                and current.length + ordered[j].length <= 32
            ):
                combined_bits = list(range(current.start_pos, ordered[j].start_pos + ordered[j].length))
                # Judge smoothness on the *full* extraction, not the 100-sample
                # preview DiscoveredSignal.raw_values is truncated to -- a real
                # capture easily runs to thousands of frames, and a jump past
                # frame 100 would otherwise go unnoticed.
                byte_order = self._detect_byte_order(combined_bits, stats)
                full_raw = self._extract_raw_numbers(combined_bits, byte_order, stats)
                if not full_raw or not self._is_smoothly_varying(full_raw, len(combined_bits)):
                    break
                candidate = self._build_signal(current.id, combined_bits, raw_values, stats)
                if candidate is None:
                    break
                current = candidate
                j += 1
            merged.append(current)
            i = j if j > i + 1 else i + 1

        for offset, sig in enumerate(merged):
            sig.id = start_sig_id + offset
        return merged

    # ── Dedicated counter scan (stage 2) ───────────────────────────────

    _COUNTER_WIDTHS = (32, 16, 8, 4, 2)  # widest first: an 8-bit counter's low
    # nibble also independently looks like a valid 4-bit counter, so a
    # narrower width must not get the chance to claim it out from under a
    # genuinely wider counter. 16 is here for AUTOSAR E2E Profile 4, whose
    # counter is 16 bits wide -- without it the (16, "CRC32P4") entry in
    # _E2E_PROFILE_HINTS could never fire, and a Profile 4 message was
    # actively *mis*-reported as Profile 7: the 16-bit counter's low byte
    # is itself a valid 8-bit counter, so the 8-bit scan claimed it and the
    # (8, "CRC32P4") hint matched instead.
    #
    # 2 is here because real pre-AUTOSAR ECUs use 2-bit rolling fields: all
    # five such fields found on an Opel Astra capture cycle 0,1,2,3, and
    # read as 4-bit they score exactly 75% (+1,+1,+1,-3), just over the 70%
    # threshold -- so they were accepted at double their real width, and the
    # two extra bits, which belong to other signals, were claimed away from
    # stage 3. Unlike 1 bit, 2 bits are not degenerate: a plain 0,1 toggle
    # scores 100% as a 1-bit counter, whereas a non-counter 2-bit field
    # scores about 25%, nowhere near threshold.

    # Candidates are scanned byte-aligned (or on the field's own width for
    # the sub-byte widths) rather than width-aligned: a 32-bit counter starting at byte 1,
    # or a 16-bit one at an odd byte, is perfectly ordinary and a stride of
    # `length` would step straight over it -- in that case the scan fell
    # through to claiming the counter's own least-significant byte as an
    # unrelated 8-bit counter, reporting the right message with the wrong
    # field. The extra positions are cheap because _quick_counter_check
    # prefilters them on a prefix of the capture.
    _COUNTER_SCAN_ALIGNMENT = 8

    # Frames of the capture _quick_counter_check judges a candidate on
    # before the authoritative full-trace check in _build_signal. Only
    # applied when the capture is comfortably longer than this, so short
    # traces are still judged in full.
    _COUNTER_PREFILTER_FRAMES = 64

    # Fraction of frame-to-frame steps that must take the field's dominant
    # stride for it to read as a counter. The slack in the +1 case is there
    # to absorb frames the capture missed.
    _COUNTER_STEP_THRESHOLD = 0.7

    # A *non-unit* stride gets no such slack, because the two claims are not
    # the same. "+1, most of the time" tolerates gaps in the capture; "+k"
    # is itself an assertion that the step is constant, and a capture with
    # gaps would not show a constant k. Loosening it to 0.7 turned out to
    # admit a specific artefact: a narrow cycle sitting inside a wider
    # field, where every step but the wrap takes one value. Real examples
    # on an Opel Astra capture -- bits[28:32] of 0x0C9 run A D 0 7 A D 0 7,
    # a stride of 3 on exactly 75% of steps while visiting 4 of the 16
    # values; 0x287 bits[0:8] the same at 4 of 256. Both are the low bits
    # of a 2-bit counter dressed up as a wider one. A genuine non-unit
    # counter does not look like that: 0x112's stride of 5 holds on 100% of
    # steps and reaches all 16 values.
    _COUNTER_NONUNIT_STRIDE_THRESHOLD = 0.95

    @staticmethod
    def _stride_threshold(stride: int) -> float:
        """Fraction of steps a candidate must show at `stride` to count --
        see the two constants above."""
        return (
            TraceReverseEngineer._COUNTER_STEP_THRESHOLD
            if stride == 1
            else TraceReverseEngineer._COUNTER_NONUNIT_STRIDE_THRESHOLD
        )

    # How consistently a sub-byte candidate must show carry -- in either
    # direction -- before it is dismissed as a slice of something wider;
    # see _is_slice_of_wider_value().
    #
    # Deliberately near 1.0, because for a genuine slice both ratios are
    # *structurally* 100%: a slice rolls over exactly when the wider value
    # carries past it, and advances exactly when the bits under it wrap.
    # Anything materially below 100% means the field moves independently
    # of its neighbours, which a slice cannot do. Measured on real
    # captures the two populations do not overlap at all -- genuine 2-bit
    # counters on the Opel Astra sit at exactly 75.00% (their neighbours
    # are unrelated fields that merely happen to cycle), while the Clio
    # 0x511 ramp slices sit at exactly 100.00%. An earlier 0.5 limit fell
    # between them and discarded six real counters.
    _COUNTER_SLICE_CARRY_LIMIT = 0.9

    @staticmethod
    def _is_slice_of_wider_value(start_pos: int, length: int, stats: CanIdStats) -> bool:
        """Is this narrow candidate not a counter at all, but a slice cut
        out of a wider, steadily-climbing quantity?

        Any value that climbs steadily contains a field that increments by
        exactly +1 at *some* bit scale -- that is just what "divide by
        2^k" does to a ramp. A physical signal rising by about 3.75 per
        frame, for instance, has a 2-bit field scoring 93.5% at one offset
        while every other offset scores 47% or less. Nothing about the
        field read on its own distinguishes that from a genuine 2-bit
        rolling counter, so :meth:`_detect_counter` cannot reject it.

        What separates them is what happens *above* the field when it rolls
        over. A real counter's neighbours are unrelated signals and simply
        stay put; a slice of a wider value carries into them on every
        rollover, because the wider value is still climbing. On the case
        above that is a clean 100% against 0%.

        Two directions, because a slice can sit at either end of its byte:

        - **Carry upward.** When the field rolls over, do the
          more-significant bits of its byte change? They do for a slice,
          never for a counter whose neighbours are unrelated.
        - **Carry from below.** Does the field only ever advance in the
          same step where the bits *beneath* it wrapped? That is what
          being the high end of a ramping byte looks like, and it is the
          only test available to a field sitting at the top of its byte,
          which has no more-significant bits to watch. Clio 0x511's byte1
          climbs by 0x3F every frame, making its top two bits a flawless
          +1 counter; its bottom six wrap on every one of those
          increments, which is what gives it away.

        Both are confined to the field's own byte. A slice spanning a byte
        boundary is not tested: whether the neighbouring byte is even part
        of the same quantity depends on a byte order this candidate does
        not have yet.
        """
        offset = start_pos % 8
        if offset + length > 8:
            return False
        byte_i = start_pos // 8
        low_len = 8 - (offset + length)
        mask = (1 << length) - 1
        low_mask = (1 << low_len) - 1

        wraps = carries = 0          # does the field carry upward when it rolls over?
        increments = low_wraps = 0   # does the field only advance when the bits below wrap?
        prev: tuple[int, int, int] | None = None
        for payload in stats.payload_samples:
            if byte_i >= len(payload):
                continue
            b = payload[byte_i]
            cur = ((b >> low_len) & mask, b >> (low_len + length), b & low_mask)
            if prev is not None:
                if cur[0] < prev[0]:
                    wraps += 1
                    if cur[1] != prev[1]:
                        carries += 1
                if (cur[0] - prev[0]) & mask == 1:
                    increments += 1
                    if cur[2] < prev[2]:
                        low_wraps += 1
            prev = cur

        limit = TraceReverseEngineer._COUNTER_SLICE_CARRY_LIMIT
        if offset > 0 and wraps and carries / wraps > limit:
            return True
        if low_len > 0 and increments and low_wraps / increments > limit:
            return True
        return False

    def _quick_counter_check(self, bits: list[int], stats: CanIdStats, length: int) -> bool:
        """Cheap pre-filter for :meth:`_scan_for_counters`, skipping the
        expensive parts of :meth:`_build_signal` (byte-order smoothness
        heuristic, checksum detection, confidence scoring) that only matter
        once a candidate is already known to be a counter. Byte order is
        irrelevant for any width that fits inside one byte (there is no
        ordering ambiguity within a byte); for 16/32-bit, try both.

        Deliberately checks only :meth:`_counter_stride`, and only
        over a prefix of the capture -- not the whole of
        :meth:`_detect_counter`. Both restrictions are about *cost*, and
        neither changes the detection result, because :meth:`_build_signal`
        still runs the authoritative full-trace :meth:`_detect_counter`
        before anything is accepted:

        - The prefix keeps the widened, byte-aligned candidate grid
          affordable on long captures: a non-counter position is rejected
          after extracting 64 frames instead of all of them.
        - :meth:`_detect_counter`'s narrower-width guard is deliberately
          *not* applied here, because it is not prefix-safe: it asks whether
          the wider width's high-order bits ever take a nonzero value
          anywhere in the capture, and a 16-bit counter that only climbs
          past 255 late in the trace would be wrongly discarded if that
          question were answered from the first 64 frames alone.
        """
        orders = (0,) if length in (2, 4, 8) else (0, 1)
        limit = (
            self._COUNTER_PREFILTER_FRAMES
            if len(stats.payload_samples) > 2 * self._COUNTER_PREFILTER_FRAMES
            else None
        )
        for byte_order in orders:
            raw_nums = self._extract_raw_numbers(bits, byte_order, stats, limit=limit)
            if raw_nums:
                stride, fraction = self._counter_stride(raw_nums, length)
                if fraction > self._stride_threshold(stride):
                    return True
        return False

    def _scan_for_counters(
        self,
        stats: CanIdStats,
        raw_values: list[dict[str, Any]],
        total_bits: int,
        start_sig_id: int,
    ) -> tuple[list[DiscoveredSignal], set[int]]:
        """Scan every aligned candidate position for a 2/4/8/16/32-bit
        counter. Returns the signals found and the set of bit positions
        they claim (for the clustering pass to exclude).

        :meth:`_quick_counter_check` pre-filters candidates cheaply; the full
        :meth:`_build_signal` (and its authoritative `is_counter` check) still
        runs before anything is accepted, so this changes *when* work happens,
        not the detection result.
        """
        found: list[DiscoveredSignal] = []
        claimed: set[int] = set()
        sig_id = start_sig_id

        for length in self._COUNTER_WIDTHS:
            stride = min(length, self._COUNTER_SCAN_ALIGNMENT)
            for start in range(0, total_bits - length + 1, stride):
                bits = list(range(start, start + length))
                if any(b in claimed for b in bits):
                    continue
                if not self._quick_counter_check(bits, stats, length):
                    continue
                sig = self._build_signal(sig_id, bits, raw_values, stats)
                if sig is not None and sig.is_counter:
                    if length < 8 and self._is_slice_of_wider_value(start, length, stats):
                        continue
                    found.append(sig)
                    claimed.update(bits)
                    sig_id += 1

        return found, claimed

    # ── Dedicated CRC scan (stage 2.5) ─────────────────────────────────

    @staticmethod
    def _crc_candidate_positions(
        counter_byte_start: int, counter_byte_end: int, width_bytes: int, max_len: int
    ) -> list[int]:
        """Byte-aligned start positions to try for a CRC field: a 2-byte
        window immediately before/after the counter's own bytes (per the
        user's domain observation that AUTOSAR keeps the two adjacent),
        excluding any overlap with the counter itself.
        """
        candidates = []
        lo = max(0, counter_byte_start - 2)
        hi = min(max_len - width_bytes, counter_byte_end + 2)
        for start in range(lo, hi + 1):
            end = start + width_bytes
            if end > max_len:
                continue
            if start < counter_byte_end and end > counter_byte_start:
                continue  # overlaps the counter's own bytes
            candidates.append(start)
        return candidates

    @staticmethod
    def _active_payload_length(stats: CanIdStats) -> int:
        """Highest byte index (+1) that ever varies across the *entire*
        capture, used to exclude CAN FD DLC padding from the CRC's "other
        bytes" input (the transmitting ECU's own CRC calculation never
        saw padding it never sent).

        Only trims when `max_len > 8`: every DLC from 0-8 is directly
        achievable in both classic CAN and CAN FD, so there's no such
        thing as "padding" there -- a trailing constant byte in an
        8-byte-or-smaller frame is just as likely a genuinely reserved
        (if currently-unexercised) field as it is padding, and trimming
        it broke real matches (found live on message 0x40: an always-0
        trailing byte that WAS part of its real CRC input). Padding as a
        structural phenomenon only exists once DLC jumps into CAN FD's
        non-linear bucket sizes (12, 16, 20, 24, 32, 48, 64) -- there,
        a message using only a couple of bytes in a 32-byte frame (as
        seen on 0xB6) is unambiguous.
        """
        if not stats.payload_samples:
            return 0
        max_len = max(len(p) for p in stats.payload_samples)
        if max_len <= 8:
            return max_len
        first = stats.payload_samples[0]
        for idx in range(max_len - 1, -1, -1):
            first_val = first[idx] if idx < len(first) else 0
            if any((p[idx] if idx < len(p) else 0) != first_val for p in stats.payload_samples):
                return idx + 1
        return max_len

    # How far below the full payload the search will look for the end of
    # the protected region, once the full length and the padding-trimmed
    # length have both failed. Every partially-covered message found on a
    # real modern capture ended within four bytes of the frame end
    # (48->44, 20->16, 20->16, 8->4), so a short sweep finds them while
    # keeping the cost of the common no-match case bounded.
    _PROTECTED_LENGTH_SWEEP = 8

    @staticmethod
    def _protected_length_candidates(stats: CanIdStats) -> list[int]:
        """Candidate lengths, in priority order, for the region a checksum
        actually covers.

        There is no single right answer to feed a CRC, which is why this is
        searched rather than computed:

        - The **full payload** comes first. It is what most messages
          protect, and assuming otherwise was an outright bug:
          :meth:`_active_payload_length` drops trailing bytes that are
          constant across the capture on the theory that they are CAN FD
          padding, but a protected byte that simply never changed looks
          exactly the same. On a real capture that cost five of nine
          sampled messages their match -- one of them, a 32-byte frame
          whose CRC covers all 32, was being fed 14.
        - The **padding-trimmed** length second, since genuine CAN FD
          padding does exist and was trimmed for a reason.
        - Then a short **downward sweep**, for messages whose CRC genuinely
          covers less than the frame: container I-PDUs, where each
          sub-PDU protects only its own region. Confirmed on message
          0x0B7, whose CRC stops at byte 16 -- exactly where the second
          counter/CRC pair the scan independently found begins.

        Ordering matters more than the list's length: every caller stops at
        the first candidate that verifies exactly against the whole trace,
        so a message protected in the ordinary way costs one attempt.
        """
        if not stats.payload_samples:
            return []
        max_len = max(len(p) for p in stats.payload_samples)
        if max_len < 2:
            return []
        candidates = [max_len]
        active = TraceReverseEngineer._active_payload_length(stats)
        if 2 <= active < max_len:
            candidates.append(active)
        for length in range(max_len - 1,
                            max(1, max_len - TraceReverseEngineer._PROTECTED_LENGTH_SWEEP), -1):
            if length not in candidates:
                candidates.append(length)
        return candidates

    @staticmethod
    def _crc_candidate_looks_variable(
        stats: CanIdStats, start_byte: int, width_bytes: int
    ) -> bool:
        """Cheap prefilter: a real CRC changes on nearly every frame (it's
        a function of the counter, which itself changes every frame), so a
        candidate position with few distinct values across the sample
        can't be one -- skip it before paying for any CRC computation.
        """
        sample = stats.payload_samples[:_CRC_SAMPLE_SIZE]
        seen = set()
        for p in sample:
            if len(p) >= start_byte + width_bytes:
                seen.add(bytes(p[start_byte:start_byte + width_bytes]))
        return len(seen) >= max(3, int(0.5 * len(sample)))

    # Tiny all-or-nothing probe checked before scoring each Data-ID guess
    # during the brute force: a wrong guess has only a 1/256 chance of
    # matching any single frame, so it almost always fails on the very
    # first one -- exiting immediately there (instead of always scoring a
    # full _CRC_SAMPLE_SIZE-frame fraction) is what keeps the 256-guess
    # brute force cheap in the common case where no CRC is actually present.
    _CRC_PROBE_SIZE = 6

    @staticmethod
    def _crc_probe_matches(
        frames: list,
        start_byte: int,
        width_bytes: int,
        algo: str,
        data_id: int | None,
        big_endian: bool,
        active_len: int,
    ) -> bool:
        for payload in frames:
            if len(payload) < start_byte + width_bytes:
                continue
            observed = int.from_bytes(
                bytes(payload[start_byte:start_byte + width_bytes]),
                "big" if big_endian else "little",
            )
            other = bytes(payload[:start_byte]) + bytes(payload[start_byte + width_bytes:active_len])
            if _crc_autosar(other, algo, extra_byte=data_id) != observed:
                return False
        return True

    @staticmethod
    def _crc_match_fraction(
        frames: list,
        start_byte: int,
        width_bytes: int,
        algo: str,
        data_id: int | None,
        big_endian: bool,
        active_len: int,
    ) -> float:
        """Fraction of `frames` where computed CRC(algo, other bytes,
        data_id) matches the observed bytes at [start_byte, start_byte +
        width_bytes) interpreted as `big_endian`/little. "Other bytes" is
        bounded to `active_len` (see :meth:`_active_payload_length`) so
        CAN FD padding never gets fed into the CRC computation. Shared by
        the sample-level search and the full-trace verification pass.
        """
        matches = 0
        total = 0
        for payload in frames:
            if len(payload) < start_byte + width_bytes:
                continue
            observed = int.from_bytes(
                bytes(payload[start_byte:start_byte + width_bytes]),
                "big" if big_endian else "little",
            )
            other = bytes(payload[:start_byte]) + bytes(payload[start_byte + width_bytes:active_len])
            computed = _crc_autosar(other, algo, extra_byte=data_id)
            total += 1
            if computed == observed:
                matches += 1
        return matches / total if total else 0.0

    def _match_crc_algorithm(
        self,
        stats: CanIdStats,
        start_byte: int,
        width_bytes: int,
        algo: str,
        active_len: int,
        allow_data_id: bool = True,
    ) -> tuple[float, int | None, bool] | None:
        """Find the best-scoring (data_id, byte_order) for `algo` at this
        position, checked against a small sample first. Tries "no Data ID"
        (cheap: one CRC per frame per byte order) before brute-forcing a
        1-byte Data ID, which is bounded to :data:`_CRC_DATA_ID_ALGOS`;
        each guess is first rejected cheaply via :meth:`_crc_probe_matches`
        (see its docstring) before paying for a full-sample score, and the
        loop breaks as soon as the threshold is cleared. Returns None if
        nothing clears :data:`_CRC_MATCH_THRESHOLD` on the sample; the
        caller must still re-verify against the full trace before
        accepting.

        `allow_data_id=False` skips the brute force entirely, leaving only
        the cheap no-Data-ID pass. The unanchored scan uses it to sweep
        every candidate position cheaply before deciding whether any
        position is worth the 256-guess search at all -- see
        :meth:`_find_crc_unanchored`.
        """
        sample = stats.payload_samples[:_CRC_SAMPLE_SIZE]

        best: tuple[float, int | None, bool] = (0.0, None, True)
        for big_endian in (True, False):
            frac = self._crc_match_fraction(
                sample, start_byte, width_bytes, algo, None, big_endian, active_len
            )
            if frac > best[0]:
                best = (frac, None, big_endian)

        if (
            best[0] < _CRC_MATCH_THRESHOLD
            and allow_data_id
            and algo in _CRC_DATA_ID_ALGOS
        ):
            probe = sample[: self._CRC_PROBE_SIZE]
            for data_id in range(256):
                for big_endian in (True, False):
                    if not self._crc_probe_matches(
                        probe, start_byte, width_bytes, algo, data_id, big_endian, active_len
                    ):
                        continue
                    frac = self._crc_match_fraction(
                        sample, start_byte, width_bytes, algo, data_id, big_endian, active_len
                    )
                    if frac > best[0]:
                        best = (frac, data_id, big_endian)
                if best[0] >= _CRC_MATCH_THRESHOLD:
                    break

        return best if best[0] >= _CRC_MATCH_THRESHOLD else None

    def _match_crc_data_id_list(
        self,
        stats: CanIdStats,
        counter: DiscoveredSignal,
        start_byte: int,
        algo: str,
        active_len: int,
    ) -> dict[int, int] | None:
        """Solve an AUTOSAR E2E Profile 2 Data ID *list* at this position,
        or None.

        Partitions the capture by counter value. Within one partition every
        frame used the same list entry, so the constant-Data-ID problem is
        back and can be brute-forced exactly as
        :meth:`_match_crc_algorithm` does -- 256 guesses, each rejected on
        its first frame by :meth:`_crc_probe_matches`, then confirmed
        against the whole partition. Every partition must resolve: a list
        with a hole in it is not a solution, it is a coincidence somewhere
        else.

        Partitions are visited in counter order and the search abandons the
        position at the first one that fails to resolve. That matters for
        cost -- a position that is not a Profile 2 CRC pays for one
        partition's 256 guesses, not sixteen -- and it keeps the result
        deterministic regardless of dict ordering.

        Scoring is over *distinct* protected payloads rather than frames,
        which is the only counting that reflects how much the capture
        actually constrains the answer.

        Crucially, enough partitions must carry *varying* protected data
        before any of this counts as evidence at all; see
        :data:`_CRC_DATA_ID_LIST_MIN_DISTINCT`. Without that check a
        message whose payload is static within each counter value resolves
        under every algorithm equally, and the one reported is decided by
        loop order rather than by the data.
        """
        if counter.length > _CRC_DATA_ID_LIST_MAX_COUNTER_BITS:
            return None

        counter_bits = list(range(counter.start_pos, counter.start_pos + counter.length))
        counter_values = self._extract_raw_numbers(counter_bits, counter.byte_order, stats)

        # Per counter value, the distinct (protected data -> CRC byte)
        # pairs. Deduplicating here is not an optimisation, it is the
        # unit of evidence: a payload repeated ten thousand times is one
        # constraint on the Data ID, not ten thousand, and scoring by
        # frame lets a Data ID that satisfies only the dominant payload
        # clear a 95% threshold while the rarer payloads contradict it.
        groups: dict[int, dict[bytes, int]] = defaultdict(dict)
        frame_counts: Counter = Counter()
        for payload, value in zip(stats.payload_samples, counter_values):
            if len(payload) <= start_byte:
                continue
            other = bytes(payload[:start_byte]) + bytes(payload[start_byte + 1:active_len])
            observed = payload[start_byte]
            seen = groups[value].get(other)
            if seen is not None and seen != observed:
                # Identical protected data under the same counter yielding
                # two different values here: whatever this byte is, it is
                # not a function of the data, so it is not a CRC.
                return None
            groups[value][other] = observed
            frame_counts[value] += 1

        usable = [
            v for v in sorted(groups)
            if frame_counts[v] >= _CRC_DATA_ID_LIST_MIN_PER_GROUP
        ]
        if len(usable) < _CRC_DATA_ID_LIST_MIN_GROUPS:
            return None

        informative = sum(
            1 for v in usable if len(groups[v]) >= _CRC_DATA_ID_LIST_MIN_DISTINCT
        )
        if informative < _CRC_DATA_ID_LIST_MIN_INFORMATIVE:
            return None

        table: dict[int, int] = {}
        for value in usable:
            classes = sorted(groups[value].items())
            resolved = None
            probe = classes[: self._CRC_PROBE_SIZE]
            for data_id in range(256):
                if any(
                    _crc_autosar(other, algo, extra_byte=data_id) != observed
                    for other, observed in probe
                ):
                    continue
                hits = sum(
                    1 for other, observed in classes
                    if _crc_autosar(other, algo, extra_byte=data_id) == observed
                )
                if hits / len(classes) >= _CRC_MATCH_THRESHOLD:
                    resolved = data_id
                break
            if resolved is None:
                return None
            table[value] = resolved
        return table

    def _find_crc_data_id_list(
        self,
        stats: CanIdStats,
        counter: DiscoveredSignal,
        claimed: set[int],
        active_len: int,
        sig_id: int,
    ) -> DiscoveredSignal | None:
        """Look for a Profile 2 style one-byte CRC near `counter`, using a
        counter-indexed Data ID list rather than a single constant one.

        Runs only after :meth:`_match_crc_algorithm` has failed at every
        position for every algorithm, and searches the same candidate
        window and the same two one-byte algorithms -- the Data ID is only
        foldable as a single extra input byte for those (see
        :data:`_CRC_DATA_ID_ALGOS`), which is also exactly the set Profile
        1 and 2 use.
        """
        counter_byte_start = counter.start_pos // 8
        counter_byte_end = (counter.start_pos + counter.length + 7) // 8

        for algo in _CRC_DATA_ID_ALGOS:
            for start_byte in self._crc_candidate_positions(
                counter_byte_start, counter_byte_end, 1, active_len
            ):
                if set(range(start_byte * 8, start_byte * 8 + 8)) & claimed:
                    continue
                if not self._crc_candidate_looks_variable(stats, start_byte, 1):
                    continue
                table = self._match_crc_data_id_list(
                    stats, counter, start_byte, algo, active_len
                )
                if table is None:
                    continue

                raw_nums = [p[start_byte] for p in stats.payload_samples
                            if len(p) > start_byte]
                id_list: list[int | None] = [
                    table.get(i) for i in range(1 << counter.length)
                ]
                return DiscoveredSignal(
                    id=sig_id,
                    name=algo,
                    start_pos=start_byte * 8,
                    length=8,
                    byte_order=0,
                    value_type="Unsigned",
                    factor=1.0,
                    offset=0.0,
                    min_val=0.0,
                    max_val=255.0,
                    unit="",
                    enum_values=None,
                    is_counter=False,
                    is_checksum=True,
                    # Every frame of every partition matched exactly, which
                    # is the same standard of evidence a constant-Data-ID
                    # match is held to, so it earns the same confidence.
                    confidence=1.0,
                    raw_values=raw_nums[:100],
                    physical_values=[float(v) for v in raw_nums[:100]],
                    crc_algorithm=algo,
                    crc_data_id=None,
                    crc_data_id_list=id_list,
                )
        return None

    @staticmethod
    def _looks_like_checksum(
        raw_values: list[int],
        length: int,
        stats: CanIdStats,
        field_bytes: range,
    ) -> bool:
        """Behavioral fallback for a candidate that doesn't match any known
        AUTOSAR CRC algorithm exactly (a non-standard/proprietary checksum,
        or one whose Data ID/init scheme isn't covered by the brute force
        in :meth:`_match_crc_algorithm`). Rather than searching for the
        exact formula, recognize a checksum from how it *behaves*, the way
        a human looking at a trace would: an ordinary physical signal
        (speed, temperature, torque, ...) is scaled so its real-world
        range sits comfortably inside the field's bit-width headroom (an
        8-bit speed signal practically never reaches 255) and changes
        gradually frame to frame; a checksum has no such natural ceiling,
        is a near-injective function of the rest of the payload, and jumps
        around unpredictably. Three checks, all must pass:

          1. Uses close to its full bit-width range (unlike a physical
             signal, which rarely approaches its field's extremes).
          2. Changes on almost every frame where anything ELSE in the
             payload changes (a physical signal varies independently of
             unrelated fields; a checksum is a function of them).
          3. Frame-to-frame deltas are spread widely, not clustered near
             zero (a slow physical ramp) -- a real counter would already
             have been claimed by :meth:`_scan_for_counters` and excluded
             via `claimed`, so a small, camped delta here is a physical
             signal, not this.
          4. It is not predictable from its own previous value. A checksum
             is a function of the *other* bytes; a field that instead
             advances by one fixed step every frame is a function of its
             own past, and carries no information about anything else.

        Check 4 is what separates a checksum from a constant-stride ramp,
        which checks 1-3 cannot: a ramp does span its full range, does
        change whenever anything else does (it changes every frame), and
        does have large deltas. Found on real hardware -- Renault Clio
        message 0x511, where all seven bytes advance by a fixed per-byte
        step on 100% of frames, and byte3 was being reported as a checksum.

        It is deliberately measured *only* over frame pairs where the other
        bytes also changed. On pairs where nothing but a counter moved, a
        genuine checksum steps by a constant too -- necessarily, since its
        only varying input did -- and judging it over all pairs would
        reject real checksums. Measured on the right subset the margin is
        wide: on the same captures, genuine checksums 0x29A/0x0C6 sit at
        18.5%/27.0% while 0x511's ramps sit at exactly 100%.
        """
        if len(raw_values) < 10 or len(stats.payload_samples) < 10:
            return False

        full_range = (1 << length) - 1
        if full_range <= 0:
            return False

        span = max(raw_values) - min(raw_values)
        if span / full_range < 0.6:
            return False

        modulus = 1 << length
        other_changed = 0
        this_changed_given_other = 0
        own_steps: list[int] = []
        byte_steps: dict[int, list[int]] = {b: [] for b in field_bytes}
        for i in range(1, len(stats.payload_samples)):
            prev, cur = stats.payload_samples[i - 1], stats.payload_samples[i]
            width = max(len(prev), len(cur))
            other_diff = any(
                (prev[b] if b < len(prev) else 0) != (cur[b] if b < len(cur) else 0)
                for b in range(width)
                if b not in field_bytes
            )
            if not other_diff:
                continue
            other_changed += 1
            if i < len(raw_values):
                if raw_values[i] != raw_values[i - 1]:
                    this_changed_given_other += 1
                own_steps.append((raw_values[i] - raw_values[i - 1]) % modulus)
            for b in byte_steps:
                if b < len(prev) and b < len(cur):
                    byte_steps[b].append((cur[b] - prev[b]) & 0xFF)
        if other_changed == 0 or this_changed_given_other / other_changed < 0.9:
            return False

        if own_steps:
            dominant = Counter(own_steps).most_common(1)[0][1]
            if dominant / len(own_steps) >= _CHECKSUM_MAX_CONSTANT_STEP:
                return False

        # The same test again, per constituent byte. Two independent ramps
        # read as one wider field do *not* have a constant combined step --
        # each byte carries on its own schedule, so the combination takes
        # several step values and slips past the check above. Renault Clio
        # 0x511 does exactly that: rejected as a single byte, it simply
        # reappeared as a 16-bit field spanning bytes 2-3, whose individual
        # strides are a rock-steady 0xC9 and 0xB9. A field every one of
        # whose bytes is separately self-predictable is a block of ramps
        # however it is sliced.
        if len(byte_steps) > 1 and all(
            steps
            and Counter(steps).most_common(1)[0][1] / len(steps)
            >= _CHECKSUM_MAX_CONSTANT_STEP
            for steps in byte_steps.values()
        ):
            return False

        diffs = [abs(raw_values[i] - raw_values[i - 1]) for i in range(1, len(raw_values))]
        mean_delta = sum(diffs) / len(diffs) if diffs else 0.0
        if mean_delta / full_range < 0.15:
            return False

        return True

    def _find_crc_for_counter(
        self,
        stats: CanIdStats,
        counter: DiscoveredSignal,
        claimed: set[int],
        active_len: int,
        sig_id: int,
    ) -> DiscoveredSignal | None:
        """Search every algorithm x byte-aligned position near `counter`
        for the single best-matching AUTOSAR CRC field, verified against
        the *entire* trace (not just the search sample) before being
        accepted -- a promising sample match that doesn't hold up on full
        verification is rejected outright, not just down-scored.
        `active_len` (see :meth:`_active_payload_length`) bounds both the
        candidate search and the "other bytes" fed into every CRC
        computation to the payload's real extent, excluding CAN FD
        padding.
        """
        counter_byte_start = counter.start_pos // 8
        counter_byte_end = (counter.start_pos + counter.length + 7) // 8

        # (sample_frac, algo, start_byte, width_bytes, data_id, big_endian)
        best: tuple[float, str, int, int, int | None, bool] | None = None

        for algo, params in _AUTOSAR_CRC_ALGORITHMS.items():
            width_bytes = int(params["width"]) // 8
            for start_byte in self._crc_candidate_positions(
                counter_byte_start, counter_byte_end, width_bytes, active_len
            ):
                bits = set(range(start_byte * 8, (start_byte + width_bytes) * 8))
                if bits & claimed:
                    continue
                if not self._crc_candidate_looks_variable(stats, start_byte, width_bytes):
                    continue

                match = self._match_crc_algorithm(stats, start_byte, width_bytes, algo, active_len)
                if match is None:
                    continue
                frac, data_id, big_endian = match
                if best is None or frac > best[0]:
                    best = (frac, algo, start_byte, width_bytes, data_id, big_endian)

        if best is None:
            # No constant Data ID matched anywhere. Before giving up on
            # naming the formula, try Profile 2's counter-indexed Data ID
            # list -- an exact match like any other, just parameterised
            # per counter value. Returns None rather than falling back to
            # _find_checksum_by_behavior: this is called once per candidate
            # protected length, and an unnamed behavioural guess returned
            # from the first one would end the sweep before the length that
            # actually fits was ever tried. The caller applies that
            # fallback once, after every length has failed.
            return self._find_crc_data_id_list(
                stats, counter, claimed, active_len, sig_id
            )

        return self._verify_and_build_crc(stats, best, active_len, sig_id)

    def _verify_and_build_crc(
        self,
        stats: CanIdStats,
        best: tuple[float, str, int, int, int | None, bool],
        active_len: int,
        sig_id: int,
    ) -> DiscoveredSignal | None:
        """Re-check the best sample-level CRC match against the *entire*
        trace and build the signal, or reject it outright. `best` is the
        (sample_frac, algo, start_byte, width_bytes, data_id, big_endian)
        tuple either search pass settled on -- the anchored one in
        :meth:`_find_crc_for_counter` or the unanchored
        :meth:`_find_crc_unanchored` -- both of which search on a sample and
        so must both land here before anything is accepted.
        """
        _sample_frac, algo, start_byte, width_bytes, data_id, big_endian = best
        full_frac = self._crc_match_fraction(
            stats.payload_samples, start_byte, width_bytes, algo, data_id, big_endian, active_len
        )
        if full_frac < _CRC_MATCH_THRESHOLD:
            return None

        raw_nums = [
            int.from_bytes(
                bytes(p[start_byte:start_byte + width_bytes]), "big" if big_endian else "little"
            )
            for p in stats.payload_samples
            if len(p) >= start_byte + width_bytes
        ]

        # Byte order is meaningless for a single-byte field (CRC8/CRC8H2F --
        # in practice the overwhelming majority of AUTOSAR CRC fields found
        # here), and big_endian ties there since int.from_bytes gives the
        # same result either way, so the tie-break would otherwise always
        # export ByteOrder=1 (Motorola) by accident of loop order. Force
        # Intel for those -- its DBC StartPos translation (_to_dbc_start_bit)
        # is verified exact; Motorola's multi-byte translation is not, so
        # only genuinely multi-byte (16/32-bit) CRCs keep the detected order.
        byte_order = 0 if width_bytes == 1 else (1 if big_endian else 0)

        return DiscoveredSignal(
            id=sig_id,
            name=algo,
            start_pos=start_byte * 8,
            length=width_bytes * 8,
            byte_order=byte_order,
            value_type="Unsigned",
            factor=1.0,
            offset=0.0,
            min_val=0.0,
            max_val=float((1 << (width_bytes * 8)) - 1),
            unit="",
            enum_values=None,
            is_counter=False,
            is_checksum=True,
            confidence=min(1.0, 0.5 + full_frac / 2),
            raw_values=raw_nums[:100],
            physical_values=[float(v) for v in raw_nums[:100]],
            crc_algorithm=algo,
            crc_data_id=data_id,
        )

    # A message needs at least this many frames before the unanchored scan
    # will look at it: both its behavioral prefilter and the >=95%
    # full-trace CRC verification are meaningless on a handful of frames,
    # and an unconstrained search that can't be verified is exactly the
    # false positive this pass must not produce.
    _UNANCHORED_CRC_MIN_FRAMES = 30

    # Byte widths tried by the unanchored scan, matching the widths the
    # AUTOSAR algorithms actually come in (CRC8/8H2F, CRC16/16ARC,
    # CRC32/32P4).
    _UNANCHORED_CRC_WIDTHS = (1, 2, 4)

    # Largest payload the unanchored scan will brute-force a Data ID on.
    # The Data-ID search is the one genuinely expensive part of this pass
    # (256 guesses x 2 byte orders x 2 algorithms, per surviving position),
    # and on a 64-byte CAN FD frame of high-entropy data -- where every
    # position survives the behavioral prefilter and none of them match --
    # paying it at every position cost seconds per CAN ID for a result that
    # was never going to be found. Capping it at classic CAN's 8 bytes
    # costs nothing real: _CRC_DATA_ID_ALGOS is already limited to the
    # 1-byte CRCs, i.e. to E2E Profiles 1 and 2, which are 8-byte
    # classic-CAN profiles. Wider frames still get the full no-Data-ID
    # sweep across every algorithm and position.
    _UNANCHORED_DATA_ID_MAX_BYTES = 8

    def _scan_crc_candidates(
        self,
        stats: CanIdStats,
        candidates: list[tuple[int, int]],
        active_len: int,
        allow_data_id: bool,
    ) -> tuple[float, str, int, int, int | None, bool] | None:
        """Best (sample_frac, algo, start_byte, width_bytes, data_id,
        big_endian) over `candidates` -- (width_bytes, start_byte) pairs
        that already cleared the behavioral prefilter -- or None if nothing
        clears the sample threshold.
        """
        best: tuple[float, str, int, int, int | None, bool] | None = None
        probe = stats.payload_samples[:self._CRC_PROBE_SIZE]
        for width_bytes, start_byte in candidates:
            for algo, params in _AUTOSAR_CRC_ALGORITHMS.items():
                if int(params["width"]) // 8 != width_bytes:
                    continue
                # Same early-exit trick the Data-ID brute force already
                # uses, applied to the plain no-Data-ID sweep: a wrong
                # (position, algorithm) pair fails on the very first frame,
                # so probing a handful of frames avoids scoring a full
                # _CRC_SAMPLE_SIZE window at every one of a wide CAN FD
                # frame's ~180 candidate positions. Only for this sweep --
                # once a Data ID is in play, _match_crc_algorithm does its
                # own probing per guess and pre-probing without one here
                # would reject every Data-ID-protected field before the
                # search that is meant to find it ever runs.
                if not allow_data_id and not any(
                    self._crc_probe_matches(
                        probe, start_byte, width_bytes, algo, None, big_endian, active_len
                    )
                    for big_endian in (True, False)
                ):
                    continue
                match = self._match_crc_algorithm(
                    stats, start_byte, width_bytes, algo, active_len,
                    allow_data_id=allow_data_id,
                )
                if match is None:
                    continue
                frac, data_id, big_endian = match
                if best is None or frac > best[0]:
                    best = (frac, algo, start_byte, width_bytes, data_id, big_endian)
        return best

    def _find_crc_unanchored(
        self,
        stats: CanIdStats,
        active_len: int,
        sig_id: int,
        claimed: set[int] | None = None,
    ) -> DiscoveredSignal | None:
        """Byte-aligned CRC search across the *whole* payload, for a message
        the anchored pass left without a CRC -- most often because
        :meth:`find_counters` found no counter to anchor on.

        An unconstrained position x algorithm x Data-ID search is far too
        expensive to run over every position of every CAN ID (the Data-ID
        brute force alone is 256 guesses per position per algorithm), so the
        candidate positions are cut down *before* any CRC arithmetic
        happens, by the same two behavioral tests the code already trusts
        elsewhere: :meth:`_crc_candidate_looks_variable` (a real CRC changes
        on nearly every frame) and then :meth:`_looks_like_checksum` (it
        uses its full bit-width range, is a near-injective function of the
        rest of the payload, and jumps around rather than ramping). Both are
        pure payload statistics -- no CRC computed -- and together they
        typically leave one or two positions per message to actually search.

        Note the different roles those tests play in the two passes.
        :meth:`_find_checksum_by_behavior` uses :meth:`_looks_like_checksum`
        as an *acceptance* criterion, reporting a field it cannot name; here
        it is only a *prefilter*, and a survivor is still accepted solely on
        an exact >=95% match over the entire trace. That distinction is what
        makes searching unconstrained positions safe: a wrong position or
        algorithm does not reproduce a CRC across hundreds of frames by
        chance. For the same reason there is deliberately no behavioral
        fallback here -- away from a counter anchor, "looks checksum-shaped"
        on its own is too weak to name a field on.
        """
        claimed = claimed or set()

        candidates: list[tuple[int, int]] = []
        for width_bytes in self._UNANCHORED_CRC_WIDTHS:
            for start_byte in range(0, active_len - width_bytes + 1):
                bits = set(range(start_byte * 8, (start_byte + width_bytes) * 8))
                if bits & claimed:
                    continue
                if not self._crc_candidate_looks_variable(stats, start_byte, width_bytes):
                    continue

                raw_values = [
                    int.from_bytes(bytes(p[start_byte:start_byte + width_bytes]), "big")
                    for p in stats.payload_samples
                    if len(p) >= start_byte + width_bytes
                ]
                field_bytes = range(start_byte, start_byte + width_bytes)
                if not self._looks_like_checksum(
                    raw_values, width_bytes * 8, stats, field_bytes
                ):
                    continue
                candidates.append((width_bytes, start_byte))

        if not candidates:
            return None

        # Sweep every surviving position without a Data ID first: that pass
        # is ~40 CRC computations per position and finds the majority of
        # real CRCs outright. Only if the whole sweep comes up empty is the
        # 256-guess Data-ID search worth paying for anywhere -- and doing it
        # in this order also stops an early position's brute force from
        # running at all when a later position has a clean plain match.
        best = self._scan_crc_candidates(stats, candidates, active_len, allow_data_id=False)
        if best is None and active_len <= self._UNANCHORED_DATA_ID_MAX_BYTES:
            best = self._scan_crc_candidates(stats, candidates, active_len, allow_data_id=True)

        if best is None:
            return None
        return self._verify_and_build_crc(stats, best, active_len, sig_id)

    # Selecting *which* byte plays the checksum role, once the invariant
    # itself is already proven. A checksum byte is unpredictable and uses
    # its whole range, and -- being a function of the rest of the frame --
    # moves whenever anything else does.
    _CHECKSUM_BYTE_MIN_SPAN = 0.6
    _CHECKSUM_BYTE_MIN_DEPENDENCE = 0.9

    @staticmethod
    def _pick_checksum_byte(
        frames: list[bytes], active_len: int, claimed: set[int]
    ) -> int | None:
        """Which byte of a frame is the checksum, given a verified
        whole-frame invariant.

        The invariant is symmetric -- `SUM(all) == K` says a checksum is
        *present*, not where it lives, since every byte satisfies the
        rearranged form equally. So attribution is a separate, behavioural
        question, and this is only ever reached once the arithmetic has
        already been confirmed across the whole capture. Two properties
        separate the checksum from its own inputs:

        1. It spans most of its range (a checksum is near-uniform; the data
           bytes it protects are structured and usually narrow).
        2. It changes on essentially every frame where any other byte
           changed, because it is a function of them.

        Both are needed. Property 2 alone also fires on a rolling counter
        (a counter changes every frame, so trivially whenever anything else
        does) -- on real messages 0x29A and 0x0C6 the counter byte scores a
        perfect 1.000 there, and is separated only by property 1, spanning
        0.06 and 0.12 of its range against the checksum's 1.00.

        Property 2 is a floor rather than a guarantee: a checksum whose
        inputs move in compensating directions -- two bytes changing so the
        sum is unaffected -- does not change on that frame, and can score
        marginally below a data byte that changes every time. Observed on
        real messages the checksum still leads (0.991 and 0.993 against
        0.73 or less), but the ranking is a judgement about which byte fits
        best, not a proof.

        Deliberately *not* :meth:`_looks_like_checksum`, whose third
        condition -- a large mean frame-to-frame delta -- rejects a real
        checksum whose inputs change slowly. Message 0x0C6 is exactly that:
        its counter steps by 2 and its checksum by -2 for long stretches,
        giving a mean delta near zero, and the behavioural test rejects the
        genuine checksum byte. That condition earns its place when a guess
        is all that is on offer; here the formula is already proven and it
        only causes misses.
        """
        best: tuple[float, int, int] | None = None
        for pos in range(active_len):
            if any(b in claimed for b in range(pos * 8, pos * 8 + 8)):
                continue
            values = [f[pos] for f in frames]
            span = (max(values) - min(values)) / 255
            if span < TraceReverseEngineer._CHECKSUM_BYTE_MIN_SPAN:
                continue
            others_changed = moved_with_others = 0
            for i in range(1, len(frames)):
                prev, cur = frames[i - 1], frames[i]
                if any(prev[b] != cur[b] for b in range(active_len) if b != pos):
                    others_changed += 1
                    if prev[pos] != cur[pos]:
                        moved_with_others += 1
            if not others_changed:
                continue
            dependence = moved_with_others / others_changed
            if dependence < TraceReverseEngineer._CHECKSUM_BYTE_MIN_DEPENDENCE:
                continue
            # Rank on dependence, then on how much of the range is used;
            # position last, preferring the later byte, which is where the
            # convention puts a trailing checksum.
            key = (dependence, span, pos)
            if best is None or key > best[:3]:
                best = key
        return best[2] if best else None

    def _find_simple_checksum(
        self,
        stats: CanIdStats,
        active_len: int,
        sig_id: int,
        claimed: set[int] | None = None,
    ) -> DiscoveredSignal | None:
        """Search the simple whole-frame checksum family (SUM8/XOR8 -- see
        :data:`_SIMPLE_CHECKSUM_ALGORITHMS`) for a message no AUTOSAR CRC
        matched.

        This is the scheme pre-AUTOSAR ECUs actually use, so it is what
        turns up on older vehicles where the entire CRC family draws a
        blank. Like the CRC search and unlike
        :meth:`_find_checksum_by_behavior`, acceptance is exact: the
        aggregate must hold at one constant across the whole capture. That
        makes it safe to run with no positional anchor at all, since the
        invariant is a property of the frame rather than of a position.

        Two guards keep it honest. The payload must take at least
        :data:`_SIMPLE_CHECKSUM_MIN_DISTINCT` distinct values, or the
        invariant is vacuous (see that constant). And the byte carrying the
        checksum is chosen separately by :meth:`_pick_checksum_byte` --
        reporting no signal at all if none qualifies, rather than pinning
        the finding on an arbitrary position just because the arithmetic
        worked out.
        """
        claimed = claimed or set()
        frames = [
            bytes(p[:active_len])
            for p in stats.payload_samples
            if len(p) >= active_len
        ]
        if len(frames) < self._UNANCHORED_CRC_MIN_FRAMES or active_len < 2:
            return None
        if len(set(frames)) < _SIMPLE_CHECKSUM_MIN_DISTINCT:
            return None

        best: tuple[str, float, int] | None = None
        for algo in _SIMPLE_CHECKSUM_ALGORITHMS:
            counts = Counter(_simple_checksum_aggregate(f, algo) for f in frames)
            target, hits = counts.most_common(1)[0]
            frac = hits / len(frames)
            if frac >= _CRC_MATCH_THRESHOLD and (best is None or frac > best[1]):
                best = (algo, frac, target)
        if best is None:
            return None

        algo, frac, target = best
        pos = self._pick_checksum_byte(frames, active_len, claimed)
        if pos is None:
            return None

        raw_nums = [f[pos] for f in frames]
        return DiscoveredSignal(
            id=sig_id,
            name=algo,
            start_pos=pos * 8,
            length=8,
            byte_order=0,
            value_type="Unsigned",
            factor=1.0,
            offset=0.0,
            min_val=0.0,
            max_val=255.0,
            unit="",
            enum_values=None,
            is_counter=False,
            is_checksum=True,
            confidence=min(1.0, 0.5 + frac / 2),
            raw_values=raw_nums[:100],
            physical_values=[float(v) for v in raw_nums[:100]],
            crc_algorithm=algo,
            crc_data_id=None,
            checksum_target=target,
        )

    def _find_checksum_by_behavior(
        self,
        stats: CanIdStats,
        counter_byte_start: int,
        counter_byte_end: int,
        claimed: set[int],
        active_len: int,
        sig_id: int,
    ) -> DiscoveredSignal | None:
        """Fallback for when no candidate near the counter matches a known
        AUTOSAR CRC algorithm exactly: check the same candidate positions
        for checksum-*shaped* behavior instead (see
        :meth:`_looks_like_checksum`). Identifies "this is very likely
        some kind of checksum" without knowing its exact formula --
        `crc_algorithm`/`crc_data_id` are left unset (so no E2E profile
        gets guessed from it) and confidence is capped below what a
        verified exact match gets.
        """
        for width_bytes in (1, 2, 4):
            for start_byte in self._crc_candidate_positions(
                counter_byte_start, counter_byte_end, width_bytes, active_len
            ):
                bits = set(range(start_byte * 8, (start_byte + width_bytes) * 8))
                if bits & claimed:
                    continue
                if not self._crc_candidate_looks_variable(stats, start_byte, width_bytes):
                    continue

                raw_values = [
                    int.from_bytes(bytes(p[start_byte:start_byte + width_bytes]), "big")
                    for p in stats.payload_samples
                    if len(p) >= start_byte + width_bytes
                ]
                field_bytes = range(start_byte, start_byte + width_bytes)
                if not self._looks_like_checksum(raw_values, width_bytes * 8, stats, field_bytes):
                    continue

                return DiscoveredSignal(
                    id=sig_id,
                    name="Checksum",
                    start_pos=start_byte * 8,
                    length=width_bytes * 8,
                    byte_order=0,
                    value_type="Unsigned",
                    factor=1.0,
                    offset=0.0,
                    min_val=0.0,
                    max_val=float((1 << (width_bytes * 8)) - 1),
                    unit="",
                    enum_values=None,
                    is_counter=False,
                    is_checksum=True,
                    confidence=0.55,
                    raw_values=raw_values[:100],
                    physical_values=[float(v) for v in raw_values[:100]],
                    crc_algorithm=None,
                    crc_data_id=None,
                )
        return None

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
    def _cluster_correlated_bits(
        matrix: list[list[int]], exclude: set[int] | None = None
    ) -> dict[int, int]:
        """Cluster bit positions by change correlation.

        `exclude` -- bit positions already claimed by an earlier dedicated
        scan (e.g. a counter found by :meth:`_scan_for_counters`) -- are
        treated as inactive here, so the generic clustering pass for
        application signals never re-splits or re-absorbs them.

        Returns a dict mapping bit_position → cluster_id.
        """
        n_frames = len(matrix)
        n_bits = len(matrix[0])

        if n_frames <= 1:
            return {}

        if _HAS_NUMPY:
            return TraceReverseEngineer._cluster_numpy(matrix, exclude)
        else:
            return TraceReverseEngineer._cluster_pure_python(matrix, n_frames, n_bits, exclude)

    @staticmethod
    def _active_bit_indices(
        matrix: list[list[int]],
        min_minority_fraction: float = 0.05,
        exclude: set[int] | None = None,
    ) -> list[int]:
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
        exclude = exclude or set()
        active = []
        for bit in range(n_bits):
            if bit in exclude:
                continue
            ones = sum(row[bit] for row in matrix)
            minority = min(ones, n_frames - ones)
            if minority / n_frames >= min_minority_fraction:
                active.append(bit)
        return active

    @staticmethod
    def _cluster_numpy(matrix: list[list[int]], exclude: set[int] | None = None) -> dict[int, int]:
        arr = np.array(matrix, dtype=np.int8)
        n_bits = arr.shape[1]

        active = np.array(TraceReverseEngineer._active_bit_indices(matrix, exclude=exclude), dtype=np.int64)
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
        matrix: list[list[int]], n_frames: int, n_bits: int, exclude: set[int] | None = None
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

        active_bits = TraceReverseEngineer._active_bit_indices(matrix, exclude=exclude)

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
        counter_stride = (
            self._counter_stride(raw_nums, length)[0] if is_counter else None
        )
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
            counter_stride=counter_stride,
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
        limit: int | None = None,
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
            limit: Stop after this many frames. For cheap prefilters that
                only need to see whether a candidate is worth a full pass
                (:meth:`_quick_counter_check`); `max_len` is still measured
                across the *whole* capture either way, so bit positions mean
                the same thing with and without it.

        Returns:
            List of raw integer values, one per frame.
        """
        if not stats.payload_samples:
            return []

        max_len = max(len(p) for p in stats.payload_samples)
        samples = (
            stats.payload_samples if limit is None else stats.payload_samples[:limit]
        )

        if byte_order == 0:
            ordered_bits = sorted(bits, key=lambda p: (-(p // 8), p))
        else:
            ordered_bits = sorted(bits)

        results: list[int] = []
        for payload in samples:
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
    def _signed_reading_is_smoother(raw_values: list[int], length: int) -> bool:
        """Compare frame-to-frame smoothness of the raw (unsigned) values
        against their two's-complement (signed) reinterpretation, using
        plain, non-circular differences -- deliberately not the circular
        distance :meth:`_is_smoothly_varying` uses elsewhere, since the
        point here is exactly to catch the artificial jump two's
        complement introduces where a value crosses the nominal sign bit
        despite nothing having actually happened there (a value that
        smoothly ramps straight through it, e.g. 6, 7, ..., 31 for a
        5-bit field, is *not* the same situation as a value that
        genuinely wraps/rolls over -- see :meth:`_is_smoothly_varying`
        for that case). Only consulted as a tie-breaker once
        :meth:`_analyze_values`'s quadrant-span precondition already
        suggests "maybe signed"; a value that never approaches the sign
        boundary is left alone regardless of what this returns.
        """
        if len(raw_values) < 5:
            return False
        modulus = 1 << length
        sign_bit = modulus >> 1
        signed_values = [v - modulus if v >= sign_bit else v for v in raw_values]
        unsigned_total = sum(abs(raw_values[i] - raw_values[i - 1]) for i in range(1, len(raw_values)))
        signed_total = sum(abs(signed_values[i] - signed_values[i - 1]) for i in range(1, len(signed_values)))
        return signed_total < unsigned_total

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

        # Min/Max report what the capture actually showed, not the type's
        # range -- the same rule the numeric path below follows. A bool that
        # never went true reports 0/0, which honestly says so; returning
        # 0/1 would claim an observation that was never made, and made a
        # constant-0 field indistinguishable from one that toggled.
        lo, hi = float(min(unique)), float(max(unique))
        if length == 1:
            return "Bool", lo, hi, 1.0, 0.0, {"0": "False", "1": "True"}, False

        if all(v in (0, 1) for v in unique):
            return "Bool", lo, hi, 1.0, 0.0, {"0": "Off", "1": "On"}, False

        # A raw value >= 2^(length-1) uses the sign bit in two's complement,
        # but "any single value crosses the midpoint" is a weak signal on
        # its own -- an ordinary unsigned byte legitimately takes values
        # above 127 all the time. Require values comfortably on *both*
        # sides of the wrap (bottom and top quarter of the range) as a
        # necessary precondition, but that alone isn't sufficient: a value
        # that just smoothly ramps straight through the nominal sign
        # boundary (e.g. 6, 7, 8, ..., 31 for a 5-bit field) also spans
        # both quadrants despite never actually going negative -- treating
        # it as signed would introduce an artificial jump right where the
        # raw value crosses 15 -> 16 (found on a real merged application
        # signal: total frame-to-frame "jumpiness" more than doubled under
        # the signed reading versus the plain unsigned one). Only commit to
        # signed if that reinterpretation is actually smoother, not just
        # numerically possible.
        sign_bit = 1 << (length - 1)
        low_threshold = sign_bit // 2
        high_threshold = sign_bit + sign_bit // 2
        is_signed = (
            any(v < low_threshold for v in unique)
            and any(v >= high_threshold for v in unique)
            and TraceReverseEngineer._signed_reading_is_smoother(raw_values, length)
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

        # Factor and Offset are reported as the identity, and Min/Max as the
        # raw values actually observed.
        #
        # Nothing in a passive capture reveals engineering scaling. A trace
        # shows a 14-bit field taking values between 0 and 13565; whether
        # that is rpm, or millivolts, or tenths of a degree, is a fact about
        # the DBC and not about the bits. An earlier version manufactured a
        # factor from the observed span -- factor = span / full_range,
        # offset = observed minimum -- which is not a measurement of
        # anything: it produced, for that same field, Factor 0.828 and Max
        # 11235.03, numbers with no relation to the physical quantity and
        # no way for a reader to tell they were invented. Identity scaling
        # is the honest export: raw values, plainly labelled as such, which
        # a user can then scale once they know what the signal is.
        working = TraceReverseEngineer._to_signed(raw_values, length) if is_signed else raw_values
        value_type = "Signed" if is_signed else "Unsigned"
        return (
            value_type, float(min(working)), float(max(working)),
            1.0, 0.0, enum_vals, is_signed,
        )

    # ── Counter detection ─────────────────────────────────────────────

    # A wider candidate's "extra" high-order bits (beyond the next-narrower
    # canonical AUTOSAR width) must show real variation somewhere in the
    # capture before it's trusted as genuinely that wide -- see
    # _detect_counter's docstring for why.
    _COUNTER_NARROWER_WIDTH = {32: 16, 16: 8, 8: 4, 4: 2}

    # Fraction of the narrower field's observed rollovers that must carry
    # into the high-order bits before a wider reading is accepted. Not 1.0:
    # a dropped or duplicated frame around a rollover shows up as a wrap
    # with no carry (or the reverse), and a real counter should not lose
    # its width to one glitch in a long capture.
    _COUNTER_CARRY_THRESHOLD = 0.9

    @staticmethod
    def _wider_width_is_justified(raw_values: list[int], narrower: int) -> bool:
        """Is there evidence in the capture that a counter is genuinely
        `narrower`-bits-plus-more wide, rather than a `narrower`-bit counter
        sitting next to unrelated constant bits?

        The test is the *carry relationship*, which is the only thing that
        actually distinguishes the two: take every frame pair where the low
        `narrower` bits rolled over, and require the high-order bits to have
        changed at those points. A genuine wider counter carries into its
        high bits on every rollover; a narrow counter beside a reserved
        field does not, because that field is constant.

        This deliberately replaces an earlier test that asked only whether
        the high-order bits were ever *nonzero*. That is a different
        question, and it let any constant nonzero padding -- an ID byte, a
        fixed status nibble, a reserved field that happens not to be 0x00,
        all commonplace in real payloads -- promote a counter to the next
        width up and claim its neighbours. Only all-zero padding was
        actually caught. Requiring a carry catches both, since constant
        zero high bits produce no carries either.

        Returns False when no rollover was observed at all: without one the
        capture simply contains no evidence either way, and the narrower
        reading is the one the data supports. That does mean a 32-bit
        counter whose low 16 bits never wrap within the capture is reported
        as the 16-bit counter it is indistinguishable from -- which is the
        honest answer, at the cost of needing a long capture (2^16 frames)
        before a 32-bit counter can be confirmed as one.
        """
        mask = (1 << narrower) - 1
        wraps = 0
        carries = 0
        for i in range(1, len(raw_values)):
            prev, cur = raw_values[i - 1], raw_values[i]
            # A rollover of the low field, tolerant of skipped frames --
            # the low part going *down* is a wrap however many steps it took.
            if (cur & mask) < (prev & mask):
                wraps += 1
                if (cur >> narrower) != (prev >> narrower):
                    carries += 1
        if wraps == 0:
            return False
        return carries / wraps >= TraceReverseEngineer._COUNTER_CARRY_THRESHOLD

    @staticmethod
    def _counter_stride(raw_values: list[int], length: int) -> tuple[int, float]:
        """The field's dominant frame-to-frame step modulo its own width,
        and the fraction of steps that take it. `(0, 0.0)` when no step
        qualifies.

        The core of :meth:`_detect_counter`, split out because it is the one
        part of that test that stays meaningful on a *prefix* of the capture
        (see :meth:`_quick_counter_check`); the narrower-width guard around
        it is not.

        Deliberately not restricted to a step of exactly +1. A counter is
        incremented once per *transmission*, so a capture that does not see
        every transmission -- a message routed through a gateway that
        forwards a fraction of frames, a bus observed downstream of one --
        shows a counter advancing by a constant k > 1 while remaining, in
        every way that matters, a rolling counter. Found on a modern
        vehicle: message 0x112's counter advances by 5 and visits all 16
        values, and its CRC's Data ID list is indexed by it perfectly --
        yet a +1-only rule scored it at 0.0% and reported a spurious 2-bit
        field in its place, which then partitioned the Data ID search into
        4 groups instead of 16 and lost the message entirely.

        The stride must be **odd**, which for a power-of-two width is
        exactly the condition that it be coprime with the modulus, and so
        exactly the condition that the field cycles through *every* one of
        its values before repeating. An even stride visits only a fraction
        of the range and is some other kind of field, not a counter.
        """
        if len(raw_values) < 5:
            return 0, 0.0
        mask = (1 << length) - 1
        diffs = [(raw_values[i] - raw_values[i - 1]) & mask for i in range(1, len(raw_values))]
        if not diffs:
            return 0, 0.0
        # Sorted by count then by value, so a tie resolves the same way on
        # every run -- this feeds a detection result, and the determinism
        # invariant covers it.
        for stride, hits in sorted(Counter(diffs).items(), key=lambda kv: (-kv[1], kv[0])):
            if stride % 2 == 1:
                return stride, hits / len(diffs)
        return 0, 0.0

    @staticmethod
    def _detect_counter(raw_values: list[int], length: int) -> bool:
        """Detect if signal is a rolling AUTOSAR-style counter.

        A counter increments by 1 each frame and wraps at its own bit
        width -- counters are always unsigned and in practice only ever 2,
        4, 8, 16, or 32 bits wide (masking wraparound to a fixed 8 bits
        regardless of the signal's actual length would misjudge any other
        width's wrap transition as a huge jump instead of +1). Restricting
        to those five widths also rules out the degenerate 1-bit case,
        which is
        indistinguishable from a plain boolean toggle under modular
        arithmetic: incrementing by 1 mod 2 *is* flipping the bit, so an
        ordinary 0,1,0,1,... flag would otherwise trivially score as a
        "counter" too.

        A parallel ambiguity exists one level up, at every adjacent pair of
        widths: a genuinely 4-bit counter packed into a byte whose other
        nibble is reserved satisfies the 8-bit version of this same test
        too, since "+1 mod 256" and "+1 mod 16" are indistinguishable while
        the top nibble never changes -- and equally, a 16-bit counter's low
        byte on its own is a perfectly valid 8-bit counter. Because the
        widest-first scan order (_COUNTER_WIDTHS) would otherwise let the
        wider claim win every time, the wider width has to earn it, via
        :meth:`_wider_width_is_justified`; otherwise this defers to the
        narrower scan that runs after it.

        Getting that gate right matters more than a mislabelled width would
        suggest, because :meth:`_scan_for_counters` *claims* the bits it
        accepts and stage 3 (find_application_signals) is then never offered
        them. A counter wrongly promoted to 32 bits silently swallows three
        neighbouring bytes, and whatever real signals lived there are lost
        for the rest of the pipeline -- whereas a counter wrongly left
        narrow merely hands the leftover bits back to clustering. False
        positives cost strictly more than false negatives here, so the gate
        errs strict.
        """
        if length not in (2, 4, 8, 16, 32):
            return False
        if len(raw_values) < 5:
            return False

        stride, fraction = TraceReverseEngineer._counter_stride(raw_values, length)
        if stride == 0 or fraction <= TraceReverseEngineer._stride_threshold(stride):
            return False

        narrower = TraceReverseEngineer._COUNTER_NARROWER_WIDTH.get(length)
        if narrower is not None and not TraceReverseEngineer._wider_width_is_justified(
            raw_values, narrower
        ):
            return False

        return True

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
                sig.name = sig.crc_algorithm if sig.crc_algorithm else "Checksum"
            elif sig.enum_values and len(sig.enum_values) <= 4:
                sig.name = f"State_{sig.id}"
            elif len(sig.raw_values) >= 3:
                sig.name = f"Signal_{sig.id}"

        return signals

    # ── Export to PDU database ────────────────────────────────────────

    def _signal_triggers_transmission(
        self, sig: DiscoveredSignal, stats: CanIdStats
    ) -> bool:
        """Does a change on *this* signal trigger a transmission?

        That is what the PDU database's `SigSendType` asks, and it is
        answerable once stage 1 has identified which frames arrived early:
        take those frames and ask whether this particular signal is the one
        that changed on them. A message's early frames are enriched for
        content change as a whole; this narrows that to the field
        responsible.

        The same enrichment bar the message-level test uses applies here,
        so a signal that merely happens to change often does not qualify --
        it has to change on early frames far more than on scheduled ones.
        """
        timing = stats.timing
        if timing is None or not timing.has_fast_cycle or not timing.early_indices:
            return False
        bits = list(range(sig.start_pos, sig.start_pos + sig.length))
        values = self._extract_raw_numbers(bits, sig.byte_order, stats)
        if len(values) < 2:
            return False

        early = set(timing.early_indices)
        early_changed = sum(
            1 for i in early if 0 < i < len(values) and values[i] != values[i - 1]
        )
        on_time = on_time_changed = 0
        for i in range(1, len(values)):
            if i in early:
                continue
            on_time += 1
            on_time_changed += values[i] != values[i - 1]
        if not early:
            return False
        p_early = early_changed / len(early)
        p_on_time = on_time_changed / on_time if on_time else 0.0
        return (
            p_early >= TraceAnalyzer._CHANGE_ENRICHMENT_FLOOR
            and p_early >= TraceAnalyzer._CHANGE_ENRICHMENT_RATIO * max(p_on_time, 1e-9)
        )

    @staticmethod
    def _signal_comment(sig: DiscoveredSignal) -> str:
        """Everything discovered about a signal that the schema has no field
        for, as free text.

        The PDU database can record *that* a message is E2E-protected (one
        digit, `isE2E`) but not which algorithm, over which bytes, against
        which Data IDs, or how fast its counter runs -- and those are the
        expensive findings. Without this they are computed and then dropped
        on export.
        """
        parts: list[str] = []
        if sig.is_mux_selector:
            selected = ", ".join(str(b) for b in (sig.multiplexed_bytes or []))
            parts.append(f"multiplexor selector; selects byte(s) {selected}")
        if sig.is_counter:
            # A stride of 2^width - 1 is -1 modulo the width: an ordinary
            # counter running backwards, not a decimated one. Only a stride
            # that is neither +1 nor -1 means frames are being missed.
            if sig.counter_stride in (None, 1):
                parts.append("rolling counter")
            elif sig.counter_stride == (1 << sig.length) - 1:
                parts.append("rolling counter, counts down")
            else:
                parts.append(
                    f"rolling counter, stride {sig.counter_stride} "
                    f"(capture sees 1 frame in {sig.counter_stride})"
                )
        if sig.is_checksum:
            if sig.crc_algorithm is None:
                parts.append("checksum, formula unidentified (behavioural match)")
            elif sig.checksum_target is not None:
                parts.append(
                    f"{sig.crc_algorithm} over whole frame, "
                    f"invariant 0x{sig.checksum_target:02X}"
                )
            elif sig.crc_data_id_list:
                ids = " ".join(
                    "--" if v is None else f"{v:02X}" for v in sig.crc_data_id_list
                )
                parts.append(
                    f"{sig.crc_algorithm}, Data ID list indexed by counter: {ids}"
                )
            elif sig.crc_data_id is not None:
                parts.append(f"{sig.crc_algorithm}, Data ID 0x{sig.crc_data_id:02X}")
            else:
                parts.append(f"{sig.crc_algorithm}, no Data ID")
        return "; ".join(parts)

    @staticmethod
    def _message_comment(msg: ReverseEngineeredMessage) -> str:
        """Message-level free text -- currently the E2E profile hint in
        full. `isE2E` holds a single number, so it cannot express a hint
        that names two candidate profiles (a passive capture cannot
        separate P02 from P22, or P05 from P06); that ambiguity would
        otherwise be lost at export.
        """
        return msg.e2e_profile or ""

    @staticmethod
    def _to_dbc_start_bit(start_pos: int, length: int, byte_order: int) -> int:
        """Translate this module's own internal bit numbering (bit 0 = MSB
        of byte 0, ascending byte-major -- used throughout clustering/
        counter/CRC detection) into the DBC/Vector "StartPos" convention
        the PDU DB schema actually follows: dbc2boatjson.py copies a real
        DBC file's `start_bit` straight through with no reinterpretation,
        and boat/message.py's _pack_intel/_pack_motorola are the canonical
        consumers of that value -- both confirm the classic Vector
        convention, which is *not* the same numbering for both byte
        orders:

        - Motorola (1): StartPos is the signal's MSB position, itself
          numbered byte-major MSB0 -- i.e. exactly this module's own
          internal numbering already. No translation needed.
        - Intel (0): StartPos is instead the signal's *LSB* position,
          numbered LSB0 *within* each byte (byte_idx*8 + bit_offset_from_
          LSB) -- the mirror image, within the byte its low end sits in,
          of this module's MSB0 numbering. Getting this wrong doesn't
          affect internal analysis (which only uses its own numbering
          self-consistently) but silently exports a signal at the wrong
          bit offset for anything that reads the DBC-standard StartPos
          convention (real hardware traces, other DBC tooling).
        """
        if byte_order == 1:
            return start_pos
        low_byte = start_pos // 8
        lsb_internal_pos = min(start_pos + length - 1, low_byte * 8 + 7)
        return low_byte * 8 + (7 - (lsb_internal_pos % 8))

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

        A message carried on more than one channel is emitted as one source
        row plus a row per routed copy, each with its own DbId: the source
        lists its copies in `TargetDbIds` with a null `SourceDbId`, and each
        copy points back through `SourceDbId` with a null `TargetDbIds`.
        Copies inherit the source's signals rather than being analysed
        separately, which is what makes pairing them worth doing -- on a
        modern vehicle capture 39% of (channel, ID) pairs are copies.
        """
        result = result if result is not None else self.reverse_engineer()
        bus_mapping = bus_mapping or {}
        message_names = message_names or {}

        messages: list[dict] = []

        # Sequential DbId (matching TraceAnalyzer.to_pdu_db()'s scheme) --
        # deliberately not can_id + 1, so toggling signal reverse-engineering
        # on/off and re-exporting doesn't renumber every message.
        # DbIds are assigned to every row -- sources and routed copies --
        # before any row is built, because a source has to carry the DbIds
        # of its copies and each copy the DbId of its source.
        routed_copies = (
            self._analysis.routed_copies if self._analysis is not None else {}
        )
        next_db_id = len(result.messages) + 1
        copy_db_ids: dict[int, list[tuple[int, Any]]] = {}
        for msg in result.messages:
            for copy in routed_copies.get(msg.can_id, []):
                copy_db_ids.setdefault(msg.can_id, []).append((next_db_id, copy))
                next_db_id += 1

        routed_rows: list[dict] = []

        can_stats = self._analysis.can_stats if self._analysis is not None else {}

        for db_id, msg in enumerate(result.messages, start=1):
            stats = can_stats.get(msg.can_id)
            timing = stats.timing if stats is not None else None
            fast_ms = timing.fast_ms if timing is not None else 0.0
            repetitions = timing.repetitions if timing is not None else 0

            # Which signals pull the message forward. Only worth asking
            # when the message actually has a fast cycle -- otherwise every
            # answer is False and the extraction is wasted.
            trigger_signals: dict[int, bool] = {}
            if stats is not None and timing is not None and timing.has_fast_cycle:
                trigger_signals = {
                    sig.id: self._signal_triggers_transmission(sig, stats)
                    for sig in msg.signals
                }

            signals_list: list[dict] = []
            for sig in msg.signals:
                signals_list.append({
                    "id": sig.id,
                    "SignalName": sig.name,
                    "Length": sig.length,
                    "StartPos": self._to_dbc_start_bit(sig.start_pos, sig.length, sig.byte_order),
                    "ByteOrder": sig.byte_order,
                    "ValueType": sig.value_type,
                    # True when a change on this signal is what pulls the
                    # message forward -- see _signal_triggers_transmission.
                    "SigSendType": trigger_signals.get(sig.id, False),
                    "Repetitions": 0,
                    "InitValue": 0,
                    "Factor": sig.factor,
                    "Offset": sig.offset,
                    "Min": sig.min_val,
                    "Max": sig.max_val,
                    "Unit": sig.unit,
                    "EnumValues": sig.enum_values,
                    # MuxValue is None for signals outside the multiplexed
                    # region -- present in every variant -- and the selector
                    # value for those found inside it.
                    "IsMuxor": sig.is_mux_selector,
                    "MuxValue": sig.mux_value,
                    "Comment": self._signal_comment(sig),
                })

            copies = copy_db_ids.get(msg.can_id, [])
            name = message_names.get(msg.can_id, msg.message_name)

            for copy_db_id, copy in copies:
                # A copy is the same logical message on another bus, so its
                # layout is inherited rather than derived again -- that is
                # the entire point of pairing them. Only what genuinely
                # belongs to the bus is measured afresh: which bus, how
                # fast it is forwarded, and the frame's own properties.
                copy_len = max(copy.dlc_values) if copy.dlc_values else msg.length
                routed_rows.append({
                    "DbId": copy_db_id,
                    "MessageName": f"{name}_ch{copy.channel}",
                    "Bus": bus_mapping.get(copy.channel, f"CAN_{copy.channel}"),
                    "BusType": "CANFD" if copy.is_fd else "CAN",
                    "MessageType": 0,
                    "Direction": 1,
                    "RoutingType": 0,
                    "TargetDbIds": None,
                    "SourceDbId": db_id,
                    "isE2E": _e2e_profile_number(msg.e2e_profile),
                    "SendType": "Cyclic" if copy.cycle_time_ms else "Spontaneous",
                    "CycleTime": int(copy.cycle_time_ms),
                    "CycleTimeFast": int(copy.timing.fast_ms) if copy.timing else 0,
                    "NrOfRepetitions": copy.timing.repetitions if copy.timing else 0,
                    "Identifier": msg.identifier,
                    "FrameType": 1 if copy.is_extended else 0,
                    "Length": copy_len,
                    "BRS": copy.is_fd,
                    "signalcount": len(signals_list),
                    "signals": signals_list,
                    "Comment": f"routed copy of DbId {db_id} (channel {copy.channel})",
                    "Node": "",
                })

            messages.append({
                "DbId": db_id,
                "MessageName": name,
                "Bus": bus_mapping.get(msg.channel, msg.bus),
                "BusType": msg.bus_type,
                "MessageType": 0,
                "Direction": 0,
                "RoutingType": 1 if copies else 0,
                "TargetDbIds": [cid for cid, _ in copies] or None,
                "SourceDbId": None,
                "isE2E": _e2e_profile_number(msg.e2e_profile),
                "SendType": msg.send_type,
                "CycleTime": int(msg.cycle_time_ms),
                "CycleTimeFast": int(fast_ms),
                "NrOfRepetitions": repetitions,
                "Identifier": msg.identifier,
                "FrameType": 1 if msg.is_extended else 0,
                "Length": msg.length,
                "BRS": msg.is_fd,
                "signalcount": len(signals_list),
                "signals": signals_list,
                "Comment": self._message_comment(msg),
                "Node": "",
            })

        return {
            "schema_version": "1.0",
            "messages": messages + routed_rows,
            "signal_routes": [],
        }

    def save_pdu_db(self, path: str | Path, **kwargs) -> Path:
        """Reverse-engineer and save the derived PDU database to a JSON file."""
        import json
        pdu_db = self.to_pdu_db(**kwargs)
        out = Path(path)
        out.write_text(json.dumps(pdu_db, indent=2))
        return out
