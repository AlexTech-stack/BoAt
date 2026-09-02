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
