# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

"""Tests for stage-1 trace information triage.

Fixtures build payload sequences whose information content is known by
construction, so every expectation is a fact about data this file creates:
a counter's LSB is perfectly predictable, a seeded-random byte is not, a
constant payload carries nothing. The thresholds under test are documented
in backlog/trace_information_value.md.
"""
from __future__ import annotations

import math
import random

import pytest

from boat.trace_analyzer import CanIdStats, TraceAnalysis, TraceAnalyzer
from boat.trace_information import (
    TraceInformationScorer,
    _MIN_SAMPLES,
)

N = 600


def _stats(
    payloads: list, aid: int = 0x100, period: float = 0.01, start: float = 0.0
) -> CanIdStats:
    s = CanIdStats(channel=1, arbitration_id=aid, is_extended=False, is_fd=False)
    for i, p in enumerate(payloads):
        s.payload_samples.append(bytes(p))
        s.dlc_values.append(len(p))
        s.timestamps.append(start + i * period)
    s.count = len(payloads)
    return s


def _profile(stats: dict[int, CanIdStats]):
    a = TraceAnalysis(path="synthetic")
    a.can_stats = stats
    a.total_frames = sum(s.count for s in stats.values())
    return TraceInformationScorer(a).triage()


class TestPerBitStatistics:
    def test_counter_lsb_is_predictable_but_upper_bits_are_not(self) -> None:
        """First-order per-bit conditioning kills only a counter's LSB: the
        LSB flips every frame (transition probability 1 -> H2 = 0) while
        bit 6 flips every other frame (probability 1/2 -> H2 = 1). This
        asymmetry is why stage 1 must not publish a final innovation score."""
        payloads = [[i & 0xFF] + [0] * 7 for i in range(N)]
        prof = _profile({0x100: _stats(payloads)})
        bits = {b.position: b for b in prof.messages[0x100].partitions[0].bits}
        assert bits[7].h_conditional == pytest.approx(0.0, abs=1e-9)
        assert bits[7].h_marginal == pytest.approx(1.0, abs=0.01)
        assert bits[6].h_conditional == pytest.approx(1.0, abs=0.02)

    def test_random_bits_have_no_mutual_information(self) -> None:
        rng = random.Random(7)
        payloads = [[rng.randrange(256)] + [0] * 7 for _ in range(N)]
        prof = _profile({0x100: _stats(payloads)})
        m = prof.messages[0x100]
        for b in m.partitions[0].bits[:8]:
            assert b.mutual_information == pytest.approx(0.0, abs=0.05)

    def test_occupancy_matches_direct_count(self) -> None:
        """The run-length occupancy accounting must agree with counting
        every frame directly — including runs that end at the capture."""
        rng = random.Random(3)
        payloads = [[rng.randrange(256), 0xF0] for _ in range(97)]
        prof = _profile({0x100: _stats(payloads)})
        bits = prof.messages[0x100].partitions[0].bits
        for pos in range(8):
            direct = sum((p[0] >> (7 - pos)) & 1 for p in payloads)
            assert bits[pos].p1 == pytest.approx(direct / len(payloads))
        # byte 1 constant 0xF0: MSB-first bits 8-11 always one, 12-15 zero
        for pos in range(8, 16):
            assert bits[pos].bucket == "const"

    def test_constant_message_has_no_live_bits(self) -> None:
        prof = _profile({0x100: _stats([[0x55] * 8] * N)})
        m = prof.messages[0x100]
        assert m.live_bits == 0
        assert "constant" in m.flags
        assert m.h_marginal == pytest.approx(0.0)


class TestDlcPartitioning:
    def test_lengths_are_scored_separately(self) -> None:
        """Bit position i means different things in a 4- and an 8-byte frame
        of the same ID; per-bit statistics must never mix the two."""
        payloads = []
        for i in range(N):
            if i % 2:
                payloads.append([(i >> 1) & 0xFF, 0, 0, 0])
            else:
                payloads.append([0xAB, 0, 0, 0, 0, 0, 0, 0])
        prof = _profile({0x100: _stats(payloads)})
        m = prof.messages[0x100]
        assert set(m.lengths) == {4, 8}
        assert "variable_length" in m.flags
        by_len = {p.length: p for p in m.partitions}
        assert by_len[4].live_bits == 8       # the counter byte
        assert by_len[8].live_bits == 0       # constant within its partition

    def test_padding_tail_is_recognized(self) -> None:
        payloads = [[i & 0xFF, i >> 8 & 0xFF] + [0xAA] * 6 for i in range(N)]
        prof = _profile({0x100: _stats(payloads)})
        m = prof.messages[0x100]
        assert m.partitions[0].padding_tail_bytes == 6
        assert "padding_tail" in m.flags


class TestSaturation:
    def test_repeating_payloads_saturate(self) -> None:
        payloads = [[i % 4, 0, 0, 0] for i in range(N)]
        m = _profile({0x100: _stats(payloads)}).messages[0x100]
        assert m.distinct_payloads == 4
        assert m.saturation_verdict == "saturated"

    def test_ever_new_payloads_do_not_saturate(self) -> None:
        payloads = [[i & 0xFF, i >> 8, 0, 0] for i in range(N)]
        m = _profile({0x100: _stats(payloads)}).messages[0x100]
        assert m.distinct_payloads == N
        assert m.saturation_verdict == "unsaturated"

    def test_random_payload_entropy_is_pinned_to_the_ceiling(self) -> None:
        """A wide random field's naive payload entropy measures log2(N), not
        the field — the estimator ceiling the concept doc is built around."""
        rng = random.Random(11)
        payloads = [list(rng.randbytes(8)) for _ in range(N)]
        m = _profile({0x100: _stats(payloads)}).messages[0x100]
        assert m.payload_entropy_ceiling == pytest.approx(math.log2(N))
        assert m.payload_saturation == pytest.approx(1.0, abs=0.01)
        assert "payload_entropy_pinned" in m.flags


class TestVerdicts:
    def test_under_sampled_message_is_flagged_not_judged(self) -> None:
        m = _profile(
            {0x100: _stats([[i, 0] for i in range(_MIN_SAMPLES - 1)]),
             0x200: _stats([[i & 0xFF] * 8 for i in range(N)], aid=0x200)}
        ).messages[0x100]
        assert m.under_sampled
        assert m.saturation_verdict == "insufficient"

    def test_idle_bus_is_not_worth_the_re_pass(self) -> None:
        prof = _profile({0x100: _stats([[0] * 8] * N)})
        assert not prof.worth_re_pass
        assert any("no payload bit ever changed" in r for r in prof.reasons)

    def test_all_under_sampled_is_not_worth_the_re_pass(self) -> None:
        prof = _profile({0x100: _stats([[1, 0], [2, 0], [3, 0]])})
        assert not prof.worth_re_pass
        assert any("under-sampled" in r for r in prof.reasons)

    def test_live_trace_is_worth_the_re_pass(self) -> None:
        payloads = [[i & 0xFF, (i * 3) & 0xFF, 0, 0] for i in range(N)]
        prof = _profile({0x100: _stats(payloads)})
        assert prof.worth_re_pass


class TestPhases:
    def test_flat_discovery_yields_one_steady_phase(self) -> None:
        stats = {
            aid: _stats([[i & 0xFF] for i in range(N)], aid=aid)
            for aid in range(0x100, 0x10A)
        }
        prof = _profile(stats)
        assert not prof.staged_discovery
        assert [p.label for p in prof.phases] == ["steady"]

    def test_staged_discovery_yields_warmup_and_steady(self) -> None:
        """Half the IDs appear only after 40% of the capture — the
        startup-from-bus-sleep shape. The warm-up must cover them and
        excitation must come from the steady phase."""
        stats = {}
        for k in range(5):
            stats[0x100 + k] = _stats(
                [[i & 0xFF] for i in range(N)], aid=0x100 + k
            )
        for k in range(5):
            stats[0x200 + k] = _stats(
                [[i & 0xFF] for i in range(300)], aid=0x200 + k,
                start=2.4, period=0.01,
            )
        prof = _profile(stats)
        assert prof.staged_discovery
        assert [p.label for p in prof.phases] == ["warmup", "steady"]
        assert prof.phases[0].end_s >= 2.4
        assert all(
            m.live_bits_steady is not None for m in prof.messages.values()
        )

    def test_sna_fill_during_warmup_does_not_count_as_excitation(self) -> None:
        """A byte that changes once — init fill to its real constant value —
        is live over the whole capture but dead in the steady phase."""
        early = [[0xFF, i & 0xFF] for i in range(50)]
        late = [[0x10, i & 0xFF] for i in range(N)]
        stats = {0x100: _stats(early + late)}
        # Force staged discovery with late-arriving IDs.
        for k in range(9):
            stats[0x200 + k] = _stats(
                [[i & 0xFF] for i in range(300)], aid=0x200 + k,
                start=4.0, period=0.005,
            )
        prof = _profile(stats)
        assert prof.staged_discovery
        m = prof.messages[0x100]
        assert m.live_bits > 8              # init flip made byte 0 "live"
        assert m.live_bits_steady == 8      # steady phase: only the counter


class TestScorerInput:
    def test_accepts_analyzer_and_analysis(self) -> None:
        a = TraceAnalysis(path="synthetic")
        a.can_stats = {0x100: _stats([[i & 0xFF] for i in range(N)])}
        analyzer = TraceAnalyzer("synthetic")
        analyzer._analysis = a
        assert TraceInformationScorer(analyzer).triage().unique_ids == 1
        assert TraceInformationScorer(a).triage().unique_ids == 1

    def test_rejects_unanalyzed_analyzer(self) -> None:
        with pytest.raises(RuntimeError):
            TraceInformationScorer(TraceAnalyzer("synthetic"))


# ── Stage 2: classification and audit ───────────────────────────────────

from boat.trace_information import TraceInformationProfile  # noqa: E402
from boat.trace_reverse_engineer import (  # noqa: E402
    DiscoveredSignal,
    ReverseEngineeredMessage,
    ReverseEngineeringResult,
    TraceReverseEngineer,
)


def _signal(start: int, length: int, name: str = "sig", **kw) -> DiscoveredSignal:
    defaults = dict(
        id=1, name=name, start_pos=start, length=length, byte_order=1,
        value_type="Unsigned", factor=1.0, offset=0.0, min_val=0.0,
        max_val=255.0, unit="", enum_values=None, is_counter=False,
        is_checksum=False, confidence=0.9,
    )
    defaults.update(kw)
    return DiscoveredSignal(**defaults)


def _re_result(can_id: int, signals: list[DiscoveredSignal]) -> ReverseEngineeringResult:
    msg = ReverseEngineeredMessage(
        can_id=can_id, channel=1, db_id=1, message_name=f"Msg_0x{can_id:X}",
        bus="CAN_1", bus_type="CAN", identifier=can_id, is_extended=False,
        is_fd=False, length=8, cycle_time_ms=10.0, send_type="Cyclic",
        signals=signals,
    )
    return ReverseEngineeringResult(messages=[msg], total_can_ids=1)


def _scorer(stats: dict[int, CanIdStats]) -> TraceInformationScorer:
    a = TraceAnalysis(path="synthetic")
    a.can_stats = stats
    a.total_frames = sum(s.count for s in stats.values())
    return TraceInformationScorer(a)


def _ramp_payloads(n: int = N) -> list[list[int]]:
    """Byte 0: smooth application ramp. Byte 1: seeded noise. Bytes 2-7: 0."""
    rng = random.Random(5)
    out = []
    v = 0
    for i in range(n):
        v = (v + rng.choice((-1, 0, 1))) % 256
        out.append([v, rng.randrange(256), 0, 0, 0, 0, 0, 0])
    return out


class TestBucketAssignment:
    def test_claimed_kinds_land_in_their_buckets(self) -> None:
        payloads = _ramp_payloads()
        scorer = _scorer({0x100: _stats(payloads)})
        re = _re_result(0x100, [
            _signal(0, 8, "App", max_val=255.0),
            _signal(8, 8, "Crc", is_checksum=True),
        ])
        prof = scorer.classify(re)
        m = prof.messages[0x100]
        assert m.bucket_bits["signal"] == 8
        assert m.bucket_bits["derived"] == 8
        assert m.bucket_bits["residual"] == 0
        assert m.bucket_bits["const"] == 48
        for b in m.partitions[0].bits[:8]:
            assert b.bucket == "signal"

    def test_unclaimed_live_bits_are_residual(self) -> None:
        """A weak RE result must not flatter the trace: everything alive
        but unclaimed is opacity."""
        payloads = _ramp_payloads()
        scorer = _scorer({0x100: _stats(payloads)})
        prof = scorer.classify(_re_result(0x100, []))
        m = prof.messages[0x100]
        assert m.bucket_bits["signal"] == 0
        assert m.bucket_bits["residual"] == m.live_bits
        assert prof.opacity == pytest.approx(1.0)
        assert prof.explainability == pytest.approx(0.0)

    def test_counter_bits_are_seq_and_excluded_from_net_innovation(self) -> None:
        payloads = [[i & 0xFF] + [0] * 7 for i in range(N)]
        scorer = _scorer({0x100: _stats(payloads)})
        prof = scorer.classify(
            _re_result(0x100, [_signal(0, 8, "Ctr", is_counter=True)])
        )
        m = prof.messages[0x100]
        assert m.bucket_bits["seq"] == 8
        assert m.net_innovation_bits_per_s == pytest.approx(0.0)
        assert prof.redundancy == pytest.approx(1.0)

    def test_net_innovation_is_below_gross(self) -> None:
        payloads = _ramp_payloads()
        scorer = _scorer({0x100: _stats(payloads)})
        re = _re_result(0x100, [
            _signal(0, 8, "App"),
            _signal(8, 8, "Crc", is_checksum=True),
        ])
        prof = scorer.classify(re)
        assert prof.net_innovation_bits_per_s is not None
        assert 0 < prof.net_innovation_bits_per_s < prof.gross_innovation_bits_per_s


class TestAudit:
    def test_noise_claimed_as_signal_is_flagged_and_damped(self) -> None:
        """The measured case: a rolling-code field claimed as signals. High
        surprise, no structure -> suspect, damped, residual."""
        payloads = _ramp_payloads()
        scorer = _scorer({0x100: _stats(payloads)})
        noise = _signal(8, 8, "Fake")
        prof = scorer.classify(_re_result(0x100, [_signal(0, 8, "App"), noise]))
        m = prof.messages[0x100]
        assert m.suspect_signals == ["Fake"]
        assert "suspect_noise" in m.flags
        assert noise.confidence == pytest.approx(0.9 * 0.25)
        assert m.bucket_bits["signal"] == 8       # only the real one
        assert m.bucket_bits["residual"] == 8
        assert prof.suspect_signals == ["0x100:Fake"]
        assert any("audit" in a for a in prof.actions)

    def test_genuine_signal_is_not_flagged(self) -> None:
        payloads = _ramp_payloads()
        scorer = _scorer({0x100: _stats(payloads)})
        sig = _signal(0, 8, "App")
        prof = scorer.classify(_re_result(0x100, [sig]))
        assert prof.messages[0x100].suspect_signals == []
        assert sig.confidence == pytest.approx(0.9)

    def test_rarely_flipping_boolean_is_not_flagged(self) -> None:
        """Low MI alone is not suspicion: a slow boolean has little
        structure *and* little surprise — the h_cond gate must keep it."""
        payloads = [[1 if (i // 200) % 2 else 0, 0] for i in range(N)]
        scorer = _scorer({0x100: _stats(payloads)})
        sig = _signal(7, 1, "Flag")
        prof = scorer.classify(_re_result(0x100, [sig]))
        assert prof.messages[0x100].suspect_signals == []

    def test_damping_can_be_disabled(self) -> None:
        payloads = _ramp_payloads()
        scorer = _scorer({0x100: _stats(payloads)})
        noise = _signal(8, 8, "Fake")
        scorer.classify(_re_result(0x100, [noise]), damp_suspect_confidence=False)
        assert noise.confidence == pytest.approx(0.9)


class TestOpaqueTail:
    def test_structured_head_with_random_tail(self) -> None:
        rng = random.Random(9)
        payloads = []
        v = 0
        for i in range(N):
            v = (v + rng.choice((-1, 0, 1))) % 256
            payloads.append([v, 0, 0, 0] + list(rng.randbytes(4)))
        scorer = _scorer({0x100: _stats(payloads)})
        prof = scorer.classify(_re_result(0x100, [_signal(0, 8, "App")]))
        m = prof.messages[0x100]
        assert m.opaque_tail_bytes == 4
        assert "opaque_tail" in m.flags
        assert any("opaque tail" in a for a in prof.actions)

    def test_fully_claimed_message_has_no_opaque_tail(self) -> None:
        payloads = _ramp_payloads()
        scorer = _scorer({0x100: _stats(payloads)})
        prof = scorer.classify(_re_result(0x100, [
            _signal(0, 8, "App"), _signal(8, 8, "Crc", is_checksum=True),
        ]))
        assert prof.messages[0x100].opaque_tail_bytes == 0


class TestGrade:
    def test_stage_1_profile_has_no_grade(self) -> None:
        prof = _profile({0x100: _stats(_ramp_payloads())})
        assert prof.grade is None
        assert prof.opacity is None

    def test_grade_is_bounded_and_rewards_explained_structure(self) -> None:
        payloads = _ramp_payloads()
        good = _scorer({0x100: _stats(payloads)}).classify(
            _re_result(0x100, [_signal(0, 8, "App"),
                               _signal(8, 8, "Crc", is_checksum=True)])
        )
        bad = _scorer({0x100: _stats(payloads)}).classify(_re_result(0x100, []))
        assert 0.0 <= bad.grade <= good.grade <= 1.0


class TestStage2Integration:
    def test_real_re_pass_counter_and_checksum_land_in_buckets(self) -> None:
        """End-to-end: the actual TraceReverseEngineer finds the counter and
        the SUM8 checksum this fixture constructs, and classify() must file
        them under seq/derived using the RE module's own bit positions."""
        payloads = []
        for i in range(N):
            # Byte 0 is itself a stride-7 counter — the RE pass will claim
            # it as one, which is fine: this test is about counter and
            # checksum bits landing in seq/derived, not about signals.
            p = [(i * 7) % 200, 3, 1, 4, 1, 5, i % 16, 0]
            p[7] = (-sum(p)) & 0xFF          # SUM8 over the whole frame == 0
            payloads.append(p)
        a = TraceAnalysis(path="synthetic")
        a.can_stats = {0x100: _stats(payloads)}
        a.total_frames = N
        TraceAnalyzer._detect_cycle_times(a)
        analyzer = TraceAnalyzer("synthetic")
        analyzer._analysis = a
        result = TraceReverseEngineer(analyzer).reverse_engineer()
        kinds = {("crc" if s.is_checksum else "cnt" if s.is_counter else "sig")
                 for m in result.messages for s in m.signals}
        assert {"crc", "cnt"} <= kinds
        prof = TraceInformationScorer(analyzer).classify(result)
        m = prof.messages[0x100]
        # Only live claimed bits count — constant bits stay H_const even
        # inside a claimed field, since they carry no entropy either way.
        assert m.bucket_bits["derived"] >= 5
        assert m.bucket_bits["seq"] >= 4
        assert m.bucket_bits["residual"] == 0
        assert prof.redundancy > 0.3


# ── Search mask: stage-1 -> RE feedback ─────────────────────────────────

from boat.trace_information import SearchMask, _MASK_MIN_RUN_BYTES  # noqa: E402


def _opaque_payloads(n: int = N, structured_bytes: int = 2) -> list[list[int]]:
    """Smooth signal in the leading bytes, i.i.d. noise in the trailing 4 —
    the measured structured-head/opaque-tail shape."""
    rng = random.Random(17)
    out = []
    v = 100
    for _ in range(n):
        v = (v + rng.choice((-1, 0, 1))) % 256
        head = [v, 0][:structured_bytes] + [0] * (4 - structured_bytes)
        out.append(head + list(rng.randbytes(4)))
    return out


class TestSearchMask:
    def test_opaque_tail_is_masked(self) -> None:
        scorer = _scorer({0x100: _stats(_opaque_payloads())})
        mask = scorer.search_mask()
        assert 0x100 in mask.excluded_bits
        # the four noise bytes, none of the structured head
        assert all(pos >= 32 for pos in mask.excluded_bits[0x100])
        assert 0x100 not in mask.skip_ids
        assert bool(mask)

    def test_fully_opaque_message_is_skipped(self) -> None:
        rng = random.Random(19)
        payloads = [list(rng.randbytes(8)) for _ in range(N)]
        mask = _scorer({0x100: _stats(payloads)}).search_mask()
        assert 0x100 in mask.skip_ids
        assert "nothing to cluster" in mask.reasons[0x100]

    def test_smooth_signal_is_not_masked(self) -> None:
        payloads = _ramp_payloads()
        mask = _scorer({0x100: _stats(payloads)}).search_mask()
        assert 0x100 not in mask.excluded_bits
        assert not mask

    def test_single_noisy_byte_is_not_masked(self) -> None:
        """Runs shorter than _MASK_MIN_RUN_BYTES are left alone: per-bit or
        per-byte masking would shred a wide signal's noisy low bits."""
        assert _MASK_MIN_RUN_BYTES == 2
        rng = random.Random(23)
        payloads = []
        v = 0
        for _ in range(N):
            v = (v + rng.choice((-1, 0, 1))) % 256
            payloads.append([v, rng.randrange(256), 0, 0])
        mask = _scorer({0x100: _stats(payloads)}).search_mask()
        assert 0x100 not in mask.excluded_bits

    def test_under_sampled_message_is_never_masked(self) -> None:
        rng = random.Random(29)
        payloads = [list(rng.randbytes(8)) for _ in range(10)]
        mask = _scorer({0x100: _stats(payloads)}).search_mask()
        assert 0x100 not in mask.excluded_bits
        assert 0x100 not in mask.skip_ids


class TestMaskFeedbackIntoReverseEngineering:
    @staticmethod
    def _engineer(stats, mask=None):
        a = TraceAnalysis(path="synthetic")
        a.can_stats = stats
        a.total_frames = sum(s.count for s in stats.values())
        TraceAnalyzer._detect_cycle_times(a)
        analyzer = TraceAnalyzer("synthetic")
        analyzer._analysis = a
        return TraceReverseEngineer(analyzer, search_mask=mask)

    def test_masking_removes_confabulated_signals(self) -> None:
        """The measured failure: clustering i.i.d. noise invents signals."""
        stats = {0x100: _stats(_opaque_payloads())}
        unmasked = self._engineer(stats).reverse_engineer()
        n_unmasked = sum(len(m.signals) for m in unmasked.messages)

        mask = _scorer(stats).search_mask()
        masked = self._engineer(stats, mask).reverse_engineer()
        n_masked = sum(len(m.signals) for m in masked.messages)

        assert n_masked < n_unmasked
        # nothing claimed inside the masked region any more
        claimed = {p for m in masked.messages for s in m.signals
                   for p in range(s.start_pos, s.start_pos + s.length)}
        assert not (claimed & mask.excluded_bits[0x100])

    def test_skip_ids_are_passed_over(self) -> None:
        rng = random.Random(31)
        stats = {0x100: _stats([list(rng.randbytes(8)) for _ in range(N)])}
        mask = _scorer(stats).search_mask()
        assert 0x100 in mask.skip_ids
        result = self._engineer(stats, mask).find_application_signals()
        assert 0x100 not in result

    def test_no_mask_is_unchanged_behaviour(self) -> None:
        stats = {0x100: _stats(_opaque_payloads())}
        a = self._engineer(stats).reverse_engineer()
        b = self._engineer(stats, SearchMask()).reverse_engineer()
        assert ([(s.start_pos, s.length, s.name)
                 for m in a.messages for s in m.signals]
                == [(s.start_pos, s.length, s.name)
                    for m in b.messages for s in m.signals])

    def test_functional_detectors_still_see_masked_regions(self) -> None:
        """Routing, not blanket suppression: a checksum inside an otherwise
        opaque tail must still be found, because find_crcs *proves* it."""
        rng = random.Random(37)
        payloads = []
        v = 0
        for _ in range(N):
            v = (v + rng.choice((-1, 0, 1))) % 256
            p = [v, 0, 0, 0] + list(rng.randbytes(3)) + [0]
            p[7] = (-sum(p[:7])) & 0xFF      # SUM8 over the whole frame
            payloads.append(p)
        stats = {0x100: _stats(payloads)}
        mask = _scorer(stats).search_mask()
        assert mask.excluded_bits.get(0x100)          # tail was masked
        result = self._engineer(stats, mask).reverse_engineer()
        checksums = [s for m in result.messages for s in m.signals if s.is_checksum]
        assert checksums, "masking must not hide a provable checksum"


# ── Counter template scan ───────────────────────────────────────────────

from boat.trace_information import (  # noqa: E402
    _COUNTER_LSB_MIN_CHANGE_RATE,
    _h2,
)


class TestCounterTemplate:
    def test_counter_bits_match_the_analytic_template(self) -> None:
        """H_cond of bit k from a counter's LSB is exactly H2(2^-k). This is
        the identity the whole scan rests on."""
        payloads = [[0, 0, 0, 0, 0, 0, 0, i & 0xFF] for i in range(1024)]
        m = _profile({0x100: _stats(payloads)}).messages[0x100]
        bits = {b.position: b for b in m.partitions[0].bits}
        for k in range(8):                       # k = 0 is the LSB at pos 63
            expected = _h2(2.0 ** -k)
            assert bits[63 - k].h_conditional == pytest.approx(expected, abs=0.02)

    def test_lsb_is_hinted_and_other_bits_are_not(self) -> None:
        payloads = [[0, 0, 0, 0, 0, 0, 0, i & 0xFF] for i in range(1024)]
        hints = _scorer({0x100: _stats(payloads)}).counter_lsb_hints()
        assert hints[0x100] == [63]

    def test_odd_stride_counter_lsb_still_hinted(self) -> None:
        """The reason only the LSB term is used: it survives decimation.
        Measured on a real stride-3 counter, whose higher bits deviate from
        the template while the LSB still reads 0.00."""
        payloads = [[(i * 3) & 0xFF] for i in range(N)]
        hints = _scorer({0x100: _stats(payloads)}).counter_lsb_hints()
        assert 7 in hints[0x100]

    def test_constant_and_noisy_bits_are_not_hinted(self) -> None:
        rng = random.Random(41)
        payloads = [[0xFF, rng.randrange(256)] for _ in range(N)]
        hints = _scorer({0x100: _stats(payloads)}).counter_lsb_hints()
        # constant byte 0 excluded by liveness; random byte 1 by h_cond
        assert hints[0x100] == []

    def test_under_sampled_message_gets_no_hints(self) -> None:
        payloads = [[i & 0xFF] for i in range(10)]
        assert 0x100 not in _scorer({0x100: _stats(payloads)}).counter_lsb_hints()

    def test_entropy_alone_would_not_identify_the_lsb(self) -> None:
        """Why the hint keys on change rate, not H_cond: a counter's slow
        high bits have H_cond near zero too, because H2(2^-k) -> 0 for large
        k as well as k=0. Change rate separates them cleanly."""
        payloads = [[0, 0, 0, 0, 0, 0, 0, i & 0xFF] for i in range(1024)]
        m = _profile({0x100: _stats(payloads)}).messages[0x100]
        bits = {b.position: b for b in m.partitions[0].bits}
        lsb, high = bits[63], bits[56]
        assert lsb.h_conditional < 0.05 and high.h_conditional < 0.25
        assert lsb.p_change == pytest.approx(1.0, abs=0.01)
        assert high.p_change < 0.02
        assert high.p_change < _COUNTER_LSB_MIN_CHANGE_RATE <= lsb.p_change


class TestCounterHintsIntoReverseEngineering:
    @staticmethod
    def _engineer(stats, hints=None):
        a = TraceAnalysis(path="synthetic")
        a.can_stats = stats
        a.total_frames = sum(s.count for s in stats.values())
        TraceAnalyzer._detect_cycle_times(a)
        analyzer = TraceAnalyzer("synthetic")
        analyzer._analysis = a
        return TraceReverseEngineer(analyzer, counter_lsb_hints=hints)

    @staticmethod
    def _counters(result):
        return sorted((m.can_id, s.start_pos, s.length, s.counter_stride)
                      for m in result.messages for s in m.signals if s.is_counter)

    def test_hinted_scan_finds_the_same_counters(self) -> None:
        """The hint is a necessary condition, so pruning by it must not
        change the result -- only how long it takes to reach it."""
        stats = {}
        for k, aid in enumerate((0x100, 0x200, 0x300)):
            payloads = [[(i * (2 * k + 1)) & 0xFF, (i // 3) & 0xFF, 0x5A, 0]
                        for i in range(N)]
            stats[aid] = _stats(payloads, aid=aid)
        hints = _scorer(stats).counter_lsb_hints()
        assert self._counters(self._engineer(stats).reverse_engineer()) == \
               self._counters(self._engineer(stats, hints).reverse_engineer())

    def test_empty_hints_for_an_id_prune_that_id_entirely(self) -> None:
        rng = random.Random(43)
        stats = {0x100: _stats([list(rng.randbytes(8)) for _ in range(N)])}
        hints = _scorer(stats).counter_lsb_hints()
        assert hints[0x100] == []
        assert self._engineer(stats, hints).find_counters() == {}

    def test_unhinted_id_is_scanned_normally(self) -> None:
        """IDs absent from the mapping must not be pruned -- an absent entry
        means 'no information', not 'no candidates'."""
        payloads = [[i & 0xFF, 0, 0, 0] for i in range(N)]
        stats = {0x100: _stats(payloads)}
        assert self._engineer(stats, {}).find_counters() == \
               self._engineer(stats).find_counters()
