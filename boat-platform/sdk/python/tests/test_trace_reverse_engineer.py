# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

"""Tests for counter, checksum and multiplexor discovery.

The fixtures build AUTOSAR-shaped payloads directly rather than reading a
capture, so every expectation is a fact about data this file constructs.
Several cases exist because a real vehicle capture contradicted the code;
those carry a note on the shape of data that caused it, since that shape is
the whole point of the test.
"""
from __future__ import annotations

import random

import pytest

from boat.trace_analyzer import CanIdStats, TraceAnalysis, TraceAnalyzer
from boat.trace_reverse_engineer import (
    _AUTOSAR_CRC_ALGORITHMS,
    _E2E_PROFILE_HINTS,
    _crc_autosar,
    _e2e_profile_number,
    DiscoveredSignal,
    TraceReverseEngineer,
)

N = 600


def _stats(payloads: list, aid: int = 0x100, period: float = 0.01) -> CanIdStats:
    s = CanIdStats(channel=1, arbitration_id=aid, is_extended=False, is_fd=False)
    for i, p in enumerate(payloads):
        s.payload_samples.append(bytes(p))
        s.dlc_values.append(len(p))
        s.timestamps.append(i * period)
    s.count = len(payloads)
    return s


def _engineer(stats: dict[int, CanIdStats], cycles: dict[int, float] | None = None):
    a = TraceAnalysis(path="synthetic")
    a.can_stats = stats
    TraceAnalyzer._detect_cycle_times(a)
    if cycles:
        a.cycle_times_ms.update(cycles)
    analyzer = TraceAnalyzer("synthetic")
    analyzer._analysis = a
    return TraceReverseEngineer(analyzer)


def _app(i: int, n: int) -> list[int]:
    """Smoothly varying filler, deliberately not a +1 ramp, so it cannot be
    mistaken for a counter or a checksum."""
    out = []
    for k in range(n):
        t = i * (3 + k) % 512
        out.append((t if t < 256 else 511 - t) & 0xFF)
    return out


def _counter_signal(length: int, stride: int) -> DiscoveredSignal:
    return DiscoveredSignal(
        id=1, name="c", start_pos=0, length=length, byte_order=0,
        value_type="Unsigned", factor=1.0, offset=0.0, min_val=0,
        max_val=(1 << length) - 1, unit="", enum_values=None,
        is_counter=True, is_checksum=False, confidence=1.0, counter_stride=stride)


class TestCrcEngine:
    """CRC of "123456789" -- the check value every CRC specification lists."""

    @pytest.mark.parametrize("algo,expected", [
        ("CRC8", 0x4B), ("CRC8H2F", 0xDF), ("CRC16", 0x29B1),
        ("CRC16ARC", 0xBB3D), ("CRC32", 0xCBF43926), ("CRC32P4", 0x1697D06A),
    ])
    def test_spec_check_values(self, algo: str, expected: int) -> None:
        assert _crc_autosar(b"123456789", algo) == expected

    def test_profile_1_crc_differs_from_the_library_crc8(self) -> None:
        # Same polynomial, but E2E Profile 1 uses start and XOR of 0x00
        # where Crc_CalculateCRC8 uses 0xFF. Not interchangeable.
        assert _AUTOSAR_CRC_ALGORITHMS["CRC8P01"]["poly"] == 0x1D
        assert _AUTOSAR_CRC_ALGORITHMS["CRC8P01"]["init"] == 0x00
        assert _crc_autosar(b"123456789", "CRC8P01") != _crc_autosar(b"123456789", "CRC8")
        assert _crc_autosar(b"", "CRC8P01") == 0x00

    def test_data_id_is_folded_as_a_trailing_byte(self) -> None:
        assert _crc_autosar(b"\x01\x02", "CRC8H2F", extra_byte=0x2A) == \
               _crc_autosar(b"\x01\x02\x2a", "CRC8H2F")


class TestE2EProfileHints:
    def test_hint_table_matches_the_specification(self) -> None:
        # PRS_E2EProtocol tables 6.1 / 6.14 / 6.20 / 6.48 / 6.58.
        assert _E2E_PROFILE_HINTS[(4, "CRC8P01")] == "E2E_Profile_1"
        assert _E2E_PROFILE_HINTS[(4, "CRC8")] == "E2E_Profile_11"
        assert _E2E_PROFILE_HINTS[(16, "CRC32P4")] == "E2E_Profile_4"

    def test_indistinguishable_profiles_are_named_together(self) -> None:
        # P02/P22 share a counter width, polynomial and Data ID list;
        # P05/P06 share a counter width and polynomial. A passive capture
        # cannot separate either pair, so neither is claimed alone.
        assert _E2E_PROFILE_HINTS[(4, "CRC8H2F")] == "E2E_Profile_2_or_22"
        assert _E2E_PROFILE_HINTS[(8, "CRC16")] == "E2E_Profile_5_or_6"

    def test_combinations_describing_no_profile_are_absent(self) -> None:
        # CRC16ARC is used by no profile; P07 pairs a 32-bit counter with a
        # 64-bit CRC, which is out of scope entirely.
        assert (8, "CRC16ARC") not in _E2E_PROFILE_HINTS
        assert (8, "CRC32P4") not in _E2E_PROFILE_HINTS

    @pytest.mark.parametrize("label,number", [
        ("E2E_Profile_11", 11), ("E2E_Profile_4", 4),
        ("E2E_Profile_2_or_22", 2), ("E2E_Profile_5_or_6", 5),
        ("E2E_Unknown", 0), (None, 0),
    ])
    def test_profile_number_takes_the_first_not_the_last(self, label, number) -> None:
        # Reading the trailing token would export 22 and 6 -- the higher
        # profile, chosen by how the label happens to be spelled.
        assert _e2e_profile_number(label) == number


class TestCounterDetection:
    def test_finds_a_four_bit_counter(self) -> None:
        s = _stats([[0, i % 16] + _app(i, 6) for i in range(N)])
        found = _engineer({0x100: s}).find_counters()[0x100]
        assert (found[0].start_pos, found[0].length) == (12, 4)

    def test_finds_a_two_bit_counter(self) -> None:
        # Read as 4 bits a 2-bit cycle scores exactly 75% (+1,+1,+1,-3),
        # clearing the 70% bar -- so without width 2 it is accepted at
        # double its real width and claims two bits belonging elsewhere.
        s = _stats([[(i % 4) << 6] + _app(i, 7) for i in range(N)])
        found = _engineer({0x100: s}).find_counters()[0x100]
        assert (found[0].start_pos, found[0].length) == (0, 2)

    def test_finds_a_sixteen_bit_counter(self) -> None:
        payloads = []
        for i in range(N):
            c = (60000 + i) % 65536      # crosses a low-byte rollover
            payloads.append([c >> 8, c & 0xFF] + _app(i, 6))
        found = _engineer({0x100: _stats(payloads)}).find_counters()[0x100]
        assert (found[0].start_pos, found[0].length) == (0, 16)

    def test_counter_at_an_odd_byte_offset(self) -> None:
        payloads = []
        for i in range(N):
            v = (0x0001FFF0 + i) % (1 << 32)
            payloads.append([(i // 7) & 0xFF] + list(v.to_bytes(4, "big")) + _app(i, 3))
        found = _engineer({0x100: _stats(payloads)}).find_counters()[0x100]
        assert (found[0].start_pos, found[0].length) == (8, 32)

    @pytest.mark.parametrize("pad", [0x00, 0x50])
    def test_constant_padding_does_not_widen_a_counter(self, pad: int) -> None:
        # The guard must ask whether the high bits *carry* at rollover, not
        # merely whether they are ever non-zero: 0x00 padding was caught by
        # the old test, 0x50 padding was not.
        s = _stats([[pad | (i % 16)] + [0] * 7 for i in range(N)])
        found = _engineer({0x100: s}).find_counters()[0x100]
        assert (found[0].start_pos, found[0].length) == (4, 4)

    def test_constant_bytes_do_not_widen_a_counter(self) -> None:
        s = _stats([[0xA1, 0xB2, 0xC3, i % 256] + [0] * 4 for i in range(N)])
        found = _engineer({0x100: s}).find_counters()[0x100]
        assert (found[0].start_pos, found[0].length) == (24, 8)

    def test_narrow_width_when_the_capture_cannot_justify_a_wider_one(self) -> None:
        # A 32-bit counter whose low 16 bits never wrap is indistinguishable
        # from a 16-bit one, so the narrower reading is the honest answer.
        payloads = []
        for i in range(N):
            v = (100000 + i) % (1 << 32)
            payloads.append([(i // 7) & 0xFF] + list(v.to_bytes(4, "big")) + _app(i, 3))
        found = _engineer({0x100: _stats(payloads)}).find_counters()[0x100]
        assert found[0].length == 16

    def test_slice_of_a_ramp_is_not_a_counter(self) -> None:
        # Any steadily climbing value contains a +1 field at some bit scale;
        # a 16-bit value rising ~3.75 per frame has one scoring 93%.
        payloads = []
        for i in range(N):
            v = int(2000 + 1500 * (i % 400) / 400)
            payloads.append([v >> 8, v & 0xFF] + _app(i, 6))
        assert _engineer({0x100: _stats(payloads)}).find_counters() == {}

    def test_down_counter_is_accepted(self) -> None:
        s = _stats([[((-i) % 4) << 6] + _app(i, 7) for i in range(N)])
        found = _engineer({0x100: s}).find_counters()[0x100]
        assert found[0].counter_stride == 3        # -1 modulo 4


class TestCounterStride:
    def test_even_stride_is_never_a_counter(self) -> None:
        # An even stride cannot visit every value, so the field never rolls
        # over its full range and is not a counter.
        _stride, frac = TraceReverseEngineer._counter_stride(
            [(i * 2) % 16 for i in range(50)], 4)
        assert frac == 0.0

    def test_non_unit_stride_needs_near_total_consistency(self) -> None:
        # "+1 most of the time" tolerates dropped frames; "+k" is itself the
        # claim that the step is constant. A narrow cycle inside a wider
        # field takes its stride on exactly 75% of steps.
        narrow = [v for _ in range(60) for v in (0xA, 0xD, 0x0, 0x7)]
        stride, frac = TraceReverseEngineer._counter_stride(narrow, 4)
        assert frac == pytest.approx(0.75, abs=0.02)
        assert not TraceReverseEngineer._detect_counter(narrow, 4)

    def test_unusual_stride_needs_corroboration(self) -> None:
        # Six bytes each advancing by a fixed odd amount are a block of
        # ramps, not six counters: nothing else in the message depends on
        # them, and their strides do not divide the period onto the raster.
        strides = [0x3F, 0xC9, 0xB9, 0x6B, 0x0D, 0xE3]
        payloads = [[0] + [(i * k) % 256 for k in strides] for i in range(N)]
        e = _engineer({0x100: _stats(payloads, period=0.1)})
        counters = e.find_counters()
        assert counters                      # they do look like counters ...
        assert e._corroborated_counters(counters, e.find_crcs(counters)) == {}

    def test_cycle_time_corroborates_a_decimated_counter(self) -> None:
        # 50 ms observed with a stride of 5 implies 10 ms natively, a raster
        # value -- timing and stride agree from two directions.
        e = _engineer({0x100: _stats([[0] for _ in range(50)])}, cycles={0x100: 50.0})
        assert e._stride_matches_cycle_time(0x100, _counter_signal(4, 5))

    def test_implausible_decimation_is_rejected(self) -> None:
        # Divide 100 ms by a large enough stride and the result lands near
        # some raster value by arithmetic alone. Gateways drop one frame in
        # five, not one in 107.
        e = _engineer({0x100: _stats([[0] for _ in range(50)])}, cycles={0x100: 100.0})
        assert not e._stride_matches_cycle_time(0x100, _counter_signal(8, 107))

    def test_ordinary_strides_need_no_corroboration(self) -> None:
        e = _engineer({0x100: _stats([[0] for _ in range(50)])})
        assert e._stride_matches_cycle_time(0x100, _counter_signal(4, 1))
        assert e._stride_matches_cycle_time(0x100, _counter_signal(4, 15))


class TestChecksumDetection:
    def test_autosar_crc_with_a_constant_data_id(self) -> None:
        payloads = []
        for i in range(N):
            p = [0] * 8
            p[1] = i % 16
            p[2:8] = _app(i, 6)
            p[0] = _crc_autosar(bytes(p[1:8]), "CRC8H2F", extra_byte=0x2A)
            payloads.append(p)
        e = _engineer({0x100: _stats(payloads)})
        crc = e.find_crcs(e.find_counters())[0x100][0]
        assert (crc.crc_algorithm, crc.start_pos, crc.crc_data_id) == ("CRC8H2F", 0, 0x2A)

    def test_profile_2_data_id_list(self) -> None:
        # Profile 2 indexes a list of sixteen Data IDs by the counter, so no
        # single constant can match anywhere.
        ids = [0x34, 0xC1, 0x3D, 0x3B, 0x03, 0x72, 0xE6, 0xE3,
               0xEF, 0x93, 0xF0, 0x7D, 0xC2, 0x8C, 0xB2, 0x86]
        payloads = []
        for i in range(N):
            p = [0] * 8
            p[1] = i % 16
            p[2:8] = _app(i, 6)
            p[0] = _crc_autosar(bytes(p[1:8]), "CRC8H2F", extra_byte=ids[i % 16])
            payloads.append(p)
        e = _engineer({0x100: _stats(payloads)})
        crc = e.find_crcs(e.find_counters())[0x100][0]
        assert crc.crc_algorithm == "CRC8H2F"
        assert crc.crc_data_id_list == ids

    def test_static_payload_cannot_identify_an_algorithm(self) -> None:
        # Genuine Profile 2, but with a payload that never changes except
        # for the counter. Each counter partition then holds exactly one
        # distinct payload, and for fixed data the map from Data ID to CRC
        # is a bijection -- so a Data ID exists under *every* algorithm and
        # solving one proves nothing. The algorithm must stay unnamed
        # rather than be decided by which was tried first.
        ids = [0x34, 0xC1, 0x3D, 0x3B, 0x03, 0x72, 0xE6, 0xE3,
               0xEF, 0x93, 0xF0, 0x7D, 0xC2, 0x8C, 0xB2, 0x86]
        payloads = []
        for i in range(N):
            p = [0] * 8
            p[1] = i % 16
            p[0] = _crc_autosar(bytes(p[1:8]), "CRC8H2F", extra_byte=ids[i % 16])
            payloads.append(p)
        e = _engineer({0x100: _stats(payloads)})
        found = e.find_crcs(e.find_counters()).get(0x100, [])
        assert all(c.crc_algorithm is None for c in found)

    def test_unanchored_crc_on_a_message_with_no_counter(self) -> None:
        payloads = []
        for i in range(N):
            p = [0] * 8
            p[1:8] = _app(i, 7)
            p[0] = _crc_autosar(bytes(p[1:8]), "CRC8")
            payloads.append(p)
        e = _engineer({0x100: _stats(payloads)})
        assert e.find_counters() == {}
        crc = e.find_crcs({})[0x100][0]
        assert (crc.crc_algorithm, crc.start_pos) == ("CRC8", 0)

    def test_sum8_whole_frame_invariant(self) -> None:
        # One counter and one data byte, both advancing, so the checksum
        # changes on every frame. Filling several bytes with the smooth
        # filler instead lets two of them move in compensating directions,
        # leaving the sum -- and therefore the checksum -- unchanged; the
        # checksum then looks *less* dependent on the rest of the frame
        # than the data bytes do, and attribution picks the wrong byte.
        payloads = []
        for i in range(N):
            p = [0] * 8
            p[1] = i % 16
            p[2] = i % 251
            p[7] = (0xFF - sum(p[:7])) & 0xFF
            payloads.append(p)
        e = _engineer({0x100: _stats(payloads)})
        crc = e.find_crcs(e.find_counters())[0x100][0]
        assert crc.crc_algorithm == "SUM8"
        assert crc.checksum_target == 0xFF
        assert crc.start_pos == 56          # attribution picked the right byte

    def test_near_constant_payload_is_not_a_checksum(self) -> None:
        # A frame that barely changes satisfies every invariant trivially.
        e = _engineer({0x100: _stats([[1, 2, 3, 4, 5, 6, 7, 0xD6] for _ in range(N)])})
        assert e.find_crcs({}) == {}

    def test_constant_stride_ramp_is_not_a_checksum(self) -> None:
        # A ramp spans its range, changes whenever anything else does, and
        # has large deltas -- it passes every behavioural test except being
        # unpredictable from its own past.
        strides = [0x3F, 0xC9, 0xB9, 0x6B, 0x0D, 0xE3]
        payloads = [[0] + [(i * k) % 256 for k in strides] for i in range(N)]
        e = _engineer({0x100: _stats(payloads, period=0.1)})
        assert e.find_crcs({}) == {}

    def test_trailing_constant_bytes_stay_in_the_crc_input(self) -> None:
        # They look like CAN FD padding but are protected bytes that simply
        # never changed; trimming them breaks the match.
        payloads = []
        for i in range(N):
            p = [0] * 24
            p[1] = i % 16
            p[2:18] = _app(i, 16)
            p[0] = _crc_autosar(bytes(p[1:24]), "CRC8H2F")
            payloads.append(p)
        s = _stats(payloads)
        s.is_fd = True
        e = _engineer({0x100: s})
        crc = e.find_crcs(e.find_counters())[0x100][0]
        assert crc.crc_algorithm == "CRC8H2F"


class TestNegativeControls:
    def test_random_payloads_yield_nothing(self) -> None:
        rnd = random.Random(99)
        e = _engineer({0x100: _stats([[rnd.randrange(256) for _ in range(8)]
                                      for _ in range(N)])})
        counters = e.find_counters()
        assert counters == {}
        assert e.find_crcs(counters) == {}

    def test_physical_signals_yield_nothing(self) -> None:
        payloads = []
        for i in range(N):
            v = int(2000 + 1500 * (i % 400) / 400)
            payloads.append([v >> 8, v & 0xFF, (i // 5) % 200,
                             0x0F if (i // 50) % 2 else 0x03, 0,
                             min(255, i // 3), 0, 0])
        e = _engineer({0x100: _stats(payloads)})
        counters = e.find_counters()
        assert counters == {}
        assert e.find_crcs(counters) == {}

    def test_short_capture_yields_nothing(self) -> None:
        e = _engineer({0x100: _stats([[i % 16] + _app(i, 7) for i in range(12)])})
        assert e.find_crcs(e.find_counters()) == {}


# ── Merge pass: opaque runs, overlaps, identity ──────────────────────
#
# All three came out of one 5.4M-frame CAN FD capture whose export had
# 9357 one-bit signals (72% of the database), 308 overlapping pairs, and a
# duplicate signal id in 321 of its 761 populated messages.


def _bit(pos: int, *, enum: bool = True, conf: float = 0.5) -> DiscoveredSignal:
    """A plain one-bit signal at *pos* -- the fragment clustering emits."""
    return DiscoveredSignal(
        id=1, name="b", start_pos=pos, length=1, byte_order=0,
        value_type="Unsigned", factor=1.0, offset=0.0, min_val=0, max_val=1,
        unit="", enum_values={"0": "a", "1": "b"} if enum else None,
        is_counter=False, is_checksum=False, confidence=conf)


def _wide(pos: int, length: int, *, conf: float = 0.5, counter: bool = False,
          checksum: bool = False) -> DiscoveredSignal:
    return DiscoveredSignal(
        id=1, name="w", start_pos=pos, length=length, byte_order=0,
        value_type="Unsigned", factor=1.0, offset=0.0, min_val=0,
        max_val=(1 << length) - 1, unit="", enum_values=None,
        is_counter=counter, is_checksum=checksum, confidence=conf)


def _mac_payloads(n: int = 1000, seed: int = 7) -> list[list[int]]:
    """8-byte frames: 4 quiet bytes then a fresh random 32-bit trailer --
    the shape of a truncated SecOC MAC or a CRC32."""
    rng = random.Random(seed)
    return [[0x10, 0x20, 0x30, i & 0xFF]
            + list(rng.getrandbits(32).to_bytes(4, "big")) for i in range(n)]


class TestOpaqueRunCoalescing:
    def test_random_trailer_becomes_one_field(self):
        stats = _stats(_mac_payloads())
        frags = [_bit(p) for p in range(32, 64)]
        out = TraceReverseEngineer._coalesce_opaque_runs(frags, stats)
        assert len(out) == 1
        assert (out[0].start_pos, out[0].length) == (32, 32)
        assert out[0].is_opaque

    def test_genuine_flags_are_left_alone(self):
        # Bits that are mostly steady and flip rarely are what a real bank
        # of status flags looks like; nothing here is a fair coin.
        payloads = [[0x00, 0x00, 0x00, 0x00,
                     0x01 if i % 97 == 0 else 0x00,
                     0x02 if i % 61 == 0 else 0x00, 0x00, 0x00]
                    for i in range(1000)]
        frags = [_bit(p) for p in range(32, 64)]
        out = TraceReverseEngineer._coalesce_opaque_runs(frags, _stats(payloads))
        assert len(out) == 32
        assert not any(s.is_opaque for s in out)

    def test_biased_neighbour_is_trimmed_off(self):
        # A real capture put a p1=0.82 bit immediately before a 32-bit
        # random field, and the fragment run spanned both. Swallowing that
        # bit would shift the field's start and corrupt every readout.
        rng = random.Random(11)
        payloads = [[0x00, 0x00, 0x00,
                     0x01 if i % 5 else 0x00]
                    + list(rng.getrandbits(32).to_bytes(4, "big"))
                    for i in range(1000)]
        frags = [_bit(31)] + [_bit(p) for p in range(32, 64)]
        out = TraceReverseEngineer._coalesce_opaque_runs(frags, _stats(payloads))
        opaque = [s for s in out if s.is_opaque]
        assert len(opaque) == 1
        assert (opaque[0].start_pos, opaque[0].length) == (32, 32)
        assert any(s.start_pos == 31 and s.length == 1 for s in out)

    def test_short_run_is_never_merged(self):
        stats = _stats(_mac_payloads())
        frags = [_bit(p) for p in range(32, 32 + 7)]  # one below the minimum
        out = TraceReverseEngineer._coalesce_opaque_runs(frags, stats)
        assert len(out) == 7
        assert not any(s.is_opaque for s in out)

    def test_too_few_frames_to_judge(self):
        # 40 frames put the confidence interval wider than the effect, so
        # the test must decline rather than merge on noise.
        stats = _stats(_mac_payloads(n=40))
        frags = [_bit(p) for p in range(32, 64)]
        out = TraceReverseEngineer._coalesce_opaque_runs(frags, stats)
        assert len(out) == 32

    def test_counter_and_checksum_break_a_run(self):
        stats = _stats(_mac_payloads())
        frags = ([_bit(p) for p in range(32, 48)]
                 + [_wide(48, 8, counter=True)]
                 + [_bit(p) for p in range(56, 64)])
        out = TraceReverseEngineer._coalesce_opaque_runs(frags, stats)
        assert any(s.is_counter for s in out)
        # The counter splits one 32-bit block into two runs, and each is
        # judged on its own -- 8 bits is exactly the minimum, so both stand.
        assert [(s.start_pos, s.length) for s in out if s.is_opaque] == [(32, 16), (56, 8)]


class TestOverlapResolution:
    def test_protocol_field_beats_application_signal(self):
        counter = _wide(8, 8, counter=True, conf=0.4)
        app = _wide(4, 12, conf=0.95)
        kept = TraceReverseEngineer._resolve_overlaps([app, counter])
        assert [s.is_counter for s in kept] == [True]

    def test_more_confident_wins_between_equals(self):
        weak, strong = _wide(0, 8, conf=0.4), _wide(4, 8, conf=0.9)
        kept = TraceReverseEngineer._resolve_overlaps([weak, strong])
        assert [(s.start_pos, s.confidence) for s in kept] == [(4, 0.9)]

    def test_disjoint_signals_all_survive(self):
        sigs = [_wide(0, 8), _wide(8, 8), _wide(16, 8)]
        assert len(TraceReverseEngineer._resolve_overlaps(sigs)) == 3

    def test_outcome_is_independent_of_input_order(self):
        sigs = [_wide(0, 8, conf=0.5), _wide(4, 8, conf=0.5), _wide(20, 4, conf=0.7)]
        a = TraceReverseEngineer._resolve_overlaps(list(sigs))
        b = TraceReverseEngineer._resolve_overlaps(list(reversed(sigs)))
        assert [(s.start_pos, s.length) for s in a] == [(s.start_pos, s.length) for s in b]


class TestSignalIdentity:
    def test_ids_are_unique_and_sequential(self):
        stats = _stats([[i & 0xFF] * 8 for i in range(200)])
        sigs = [_wide(0, 8, counter=True), _wide(8, 8, checksum=True), _wide(16, 8)]
        out = TraceReverseEngineer._post_process_signals(sigs, stats)
        assert [s.id for s in out] == list(range(1, len(out) + 1))

    def test_names_are_unique_within_a_message(self):
        stats = _stats([[i & 0xFF] * 8 for i in range(200)])
        sigs = [_wide(0, 8, checksum=True), _wide(8, 8, checksum=True),
                _wide(16, 8, counter=True), _wide(24, 8, counter=True)]
        out = TraceReverseEngineer._post_process_signals(sigs, stats)
        names = [s.name for s in out]
        assert len(set(names)) == len(names)

    def test_lone_checksum_keeps_its_bare_algorithm_name(self):
        stats = _stats([[i & 0xFF] * 8 for i in range(200)])
        sig = _wide(0, 8, checksum=True)
        sig.crc_algorithm = "CRC8H2F"
        out = TraceReverseEngineer._post_process_signals([sig], stats)
        assert out[0].name == "CRC8H2F"

    def test_post_process_leaves_no_overlaps(self):
        stats = _stats([[i & 0xFF] * 8 for i in range(200)])
        sigs = [_wide(0, 8, counter=True), _wide(4, 8, conf=0.9), _wide(16, 8)]
        out = TraceReverseEngineer._post_process_signals(sigs, stats)
        occupied: set[int] = set()
        for s in out:
            bits = set(range(s.start_pos, s.start_pos + s.length))
            assert not (bits & occupied)
            occupied |= bits


# ── Capture-wide byte order, and spans it cannot express ─────────────
#
# A vehicle network uses one byte order throughout. Deciding it once from
# the counters replaces a per-signal smoothness heuristic that never ran on
# the ~95% of signals fitting inside one byte, and whose asymmetric
# threshold favoured Intel -- the order that, unlike Motorola, cannot
# express an arbitrary span.


def _intel_internal_bits(start: int, length: int) -> list[int]:
    """Internal (MSB-first) positions an Intel field at LSB0-absolute
    ``[start, start+length)`` actually occupies."""
    return sorted((b // 8) * 8 + (7 - b % 8) for b in range(start, start + length))


class TestRepresentability:
    def test_matches_the_real_intel_bit_layout(self):
        # The predicate must agree with what an Intel field really covers,
        # not with a rule of thumb about it. Build the set of internal spans
        # some Intel field genuinely occupies, then check both directions.
        expressible = set()
        for length in range(1, 33):
            for start in range(0, 128):
                bits = _intel_internal_bits(start, length)
                if bits == list(range(bits[0], bits[0] + length)):
                    expressible.add((bits[0], length))
        checked = 0
        for length in range(1, 33):
            for pos in range(0, 64):
                assert TraceReverseEngineer._is_representable(pos, length, 0) == (
                    (pos, length) in expressible
                ), f"disagreed at internal start {pos}, length {length}"
                checked += 1
        assert checked == 32 * 64

    def test_intel_accepts_only_byte_aligned_multibyte(self):
        f = TraceReverseEngineer._is_representable
        assert f(8, 16, 0) and f(0, 32, 0)
        assert not f(9, 16, 0)      # not byte-aligned
        assert not f(8, 22, 0)      # not a whole number of bytes
        assert not f(82, 22, 0)     # the real case, from a CAN FD capture

    def test_intel_accepts_a_field_inside_one_byte(self):
        f = TraceReverseEngineer._is_representable
        assert f(8, 4, 0) and f(12, 4, 0) and f(8, 8, 0)
        assert not f(6, 4, 0)       # straddles the byte boundary

    def test_motorola_expresses_any_span(self):
        f = TraceReverseEngineer._is_representable
        assert all(f(p, l, 1) for p in range(0, 40) for l in range(1, 33))


class TestCaptureByteOrder:
    @staticmethod
    def _with_counter(order: int, n: int = 400):
        """A message whose only moving field is a 16-bit counter laid down
        in *order*, so exactly one reading of it advances by one."""
        payloads = []
        for i in range(n):
            v = i & 0xFFFF
            hi, lo = (v >> 8) & 0xFF, v & 0xFF
            pair = [hi, lo] if order == 1 else [lo, hi]
            payloads.append([0x00, 0x00] + pair + [0x00] * 4)
        return _stats(payloads, aid=0x200)

    @pytest.mark.parametrize("order", [0, 1])
    def test_counter_reveals_the_order(self, order):
        stats = self._with_counter(order)
        eng = _engineer({0x200: stats})
        counters = eng.find_counters()
        assert counters, "fixture must produce a counter to vote with"
        assert eng._decide_capture_byte_order(counters) == order

    def test_no_evidence_falls_back_to_intel(self):
        stats = _stats([[i & 0xFF] * 8 for i in range(200)])
        eng = _engineer({0x100: stats})
        # Only sub-byte counters here, which say nothing about byte order.
        assert eng._decide_capture_byte_order({}) == 0


class TestNonRepresentableSpansDropped:
    def test_unexpressable_span_is_dropped_not_moved(self):
        stats = _stats([[i & 0xFF] * 12 for i in range(200)])
        sigs = [_wide(82, 22), _wide(8, 16)]
        out = TraceReverseEngineer._post_process_signals(sigs, stats, byte_order=0)
        assert [(s.start_pos, s.length) for s in out] == [(8, 16)]

    def test_nothing_is_dropped_without_a_decided_order(self):
        stats = _stats([[i & 0xFF] * 12 for i in range(200)])
        sigs = [_wide(82, 22), _wide(8, 16)]
        out = TraceReverseEngineer._post_process_signals(sigs, stats)
        assert len(out) == 2

    def test_counters_and_crcs_are_exempt(self):
        stats = _stats([[i & 0xFF] * 12 for i in range(200)])
        sigs = [_wide(82, 22, counter=True), _wide(20, 12, checksum=True)]
        out = TraceReverseEngineer._post_process_signals(sigs, stats, byte_order=0)
        assert len(out) == 2

    def test_motorola_capture_drops_nothing(self):
        stats = _stats([[i & 0xFF] * 12 for i in range(200)])
        sigs = [_wide(82, 22), _wide(9, 16)]
        out = TraceReverseEngineer._post_process_signals(sigs, stats, byte_order=1)
        assert len(out) == 2

    def test_exported_start_bit_now_round_trips(self):
        # The point of dropping them: every signal that survives can be
        # written down and read back at the bits it was measured on.
        stats = _stats([[i & 0xFF] * 12 for i in range(200)])
        sigs = [_wide(82, 22), _wide(8, 16), _wide(12, 4), _wide(0, 8)]
        out = TraceReverseEngineer._post_process_signals(sigs, stats, byte_order=0)
        for s in out:
            exported = TraceReverseEngineer._to_dbc_start_bit(s.start_pos, s.length, 0)
            assert _intel_internal_bits(exported, s.length)[0] == s.start_pos


# ── Sample sufficiency ───────────────────────────────────────────────
#
# The messages that shredded worst on a real CAN FD capture had 80-152
# frames against 280-370 active bits. A raw frame-count gate does not
# separate them: a subsampling sweep found signal counts flat from 60
# frames to 18630, and one message disagreed with itself at 295 frames
# while another reproduced perfectly at 1078. Reproducibility does.


class TestPartitionAgreement:
    def test_reproducible_layout_scores_high(self):
        # Two independent fields, each varying on its own schedule all the
        # way through: the same partition is there in both halves.
        payloads = []
        for i in range(600):
            a = (i * 7) % 251
            b = (i // 3) % 241
            payloads.append([a, a, 0x00, 0x00, b, b, 0x00, 0x00])
        got = TraceReverseEngineer._partition_agreement(_stats(payloads))
        assert got is not None and got > 0.8

    def test_short_message_is_not_split(self):
        assert TraceReverseEngineer._partition_agreement(
            _stats([[i & 0xFF] * 8 for i in range(20)])) is None

    def test_structure_that_changes_halfway_scores_low(self):
        # First half: bytes 0-1 move together. Second half: they stop and
        # bytes 4-5 move instead. No single partition describes both, and
        # the pairings the halves do find share nothing.
        rng = random.Random(5)
        payloads = []
        for i in range(400):
            v = rng.randrange(256)
            if i < 200:
                payloads.append([v, v, 0, 0, 0x11, 0x22, 0, 0])
            else:
                payloads.append([0x33, 0x44, 0, 0, v, v, 0, 0])
        got = TraceReverseEngineer._partition_agreement(_stats(payloads))
        # Plain Rand scores this 0.97 -- the singleton-heavy baseline --
        # which is exactly why the index is chance-adjusted.
        assert got is not None and got <= 0.05

    def test_pure_noise_agrees_trivially_and_is_not_this_gate_s_job(self):
        # A documented blind spot. Independent random bits leave both
        # halves with nothing to cluster, so both return all-singletons and
        # those agree perfectly -- "found no structure, consistently" is
        # indistinguishable here from "found structure, consistently".
        # The gate measures reproducibility, not opacity; a fair-coin
        # region is _coalesce_opaque_runs' responsibility, and that is what
        # actually collapses it.
        rng = random.Random(3)
        payloads = [[rng.randrange(256) for _ in range(8)] for _ in range(400)]
        stats = _stats(payloads)
        assert TraceReverseEngineer._partition_agreement(stats) == pytest.approx(1.0)
        frags = [_bit(p) for p in range(64)]
        out = TraceReverseEngineer._coalesce_opaque_runs(frags, stats)
        assert len(out) == 1 and out[0].is_opaque and out[0].length == 64

    def test_agreement_scales_signal_confidence(self):
        rng = random.Random(9)
        payloads = []
        for i in range(400):
            v = rng.randrange(256)
            payloads.append([v, v, 0, 0, 0x11, 0x22, 0, 0] if i < 200
                            else [0x33, 0x44, 0, 0, v, v, 0, 0])
        stats = _stats(payloads, aid=0x321)
        eng = _engineer({0x321: stats})
        assert TraceReverseEngineer._partition_agreement(stats) <= 0.05
        # An unreproducible partition is scaled to nothing, so none of its
        # signals clear min_confidence.
        assert eng.find_application_signals().get(0x321, []) == []


class TestAdjustedRand:
    def test_identical_labellings_score_one(self):
        a = [0, 0, 1, 1, 2, 2]
        assert TraceReverseEngineer._adjusted_rand(a, list(a)) == pytest.approx(1.0)

    def test_relabelling_does_not_matter(self):
        a = [0, 0, 1, 1, 2, 2]
        b = [7, 7, 3, 3, 9, 9]
        assert TraceReverseEngineer._adjusted_rand(a, b) == pytest.approx(1.0)

    def test_unrelated_labellings_score_about_zero(self):
        a = [0, 0, 0, 1, 1, 1]
        b = [0, 1, 0, 1, 0, 1]
        assert abs(TraceReverseEngineer._adjusted_rand(a, b)) < 0.35

    def test_all_singletons_against_all_singletons(self):
        # Both halves clustering nothing is agreement, not evidence -- but
        # it is at least not a contradiction, so it must not read negative.
        a = list(range(8))
        assert TraceReverseEngineer._adjusted_rand(a, list(a)) == pytest.approx(1.0)

    def test_unclustered_bits_are_not_one_group(self):
        # -1 means "too inactive to cluster". Treating those bits as a
        # single cluster would assert they form one field.
        labels = TraceReverseEngineer._cluster_labels({0: -1, 1: -1, 2: 5}, [0, 1, 2])
        assert labels[0] != labels[1]
        assert len(set(labels)) == 3


class TestOpaqueFieldsSurviveTheGate:
    def test_fair_coin_field_is_not_scaled_away(self):
        # An opaque region is often *why* a message clusters
        # irreproducibly. Gating it on that reproducibility would discard
        # the one finding that explains the rest -- and because the run is
        # assembled from one-bit fragments, filtering before assembling
        # would leave nothing to assemble.
        rng = random.Random(21)
        payloads = []
        for i in range(400):
            v = rng.randrange(256)
            head = [v, v, 0, 0] if i < 200 else [0x33, 0x44, 0, 0]
            payloads.append(head + list(rng.getrandbits(32).to_bytes(4, "big")))
        stats = _stats(payloads, aid=0x654)
        eng = _engineer({0x654: stats})
        assert TraceReverseEngineer._partition_agreement(stats) < 0.5
        found = eng.find_application_signals().get(0x654, [])
        opaque = [s for s in found if s.is_opaque]
        assert len(opaque) == 1
        assert (opaque[0].start_pos, opaque[0].length) == (32, 32)


class TestSelfCorroboratingFieldsSurviveTheGate:
    def test_clustered_counter_is_not_scaled_away(self):
        # Stage 2's dedicated scan does not reach every counter: on 21
        # messages of a real capture find_counters() returned nothing and
        # the generic clustering found the counter instead. Such a field
        # carries its own corroboration -- it was measured arithmetically
        # on those exact bits, and bits taken from a wrong boundary do not
        # count -- so an unreproducible partition must not remove it.
        rng = random.Random(31)
        payloads = []
        for i in range(400):
            v = rng.randrange(256)
            head = [v, v, 0] if i < 200 else [0x33, 0x44, 0]
            payloads.append(head + [i & 0xFF] + [0, 0, 0, 0])
        stats = _stats(payloads, aid=0x765)
        eng = _engineer({0x765: stats})
        assert TraceReverseEngineer._partition_agreement(stats) < 0.9
        found = eng.find_application_signals().get(0x765, [])
        assert any(s.is_counter for s in found), "the counting byte must survive"
