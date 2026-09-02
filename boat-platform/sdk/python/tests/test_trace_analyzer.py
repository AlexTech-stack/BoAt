# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

"""Tests for trace loading, cycle-time detection, timing profiles and
timing anomalies.

Every fixture here is synthetic and self-contained. Tests that need a real
vehicle capture live at the bottom and skip when the traces are absent, so
the suite runs anywhere.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from boat.trace_analyzer import CanIdStats, TraceAnalysis, TraceAnalyzer


def _write_log(tmp_path, lines: list[str], name: str = "cap.log"):
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n")
    return path


def _cyclic(period_ms: float, count: int, payload=b"\x00", start: float = 0.0) -> CanIdStats:
    """A message arriving exactly every `period_ms`."""
    s = CanIdStats(channel=1, arbitration_id=0x100, is_extended=False, is_fd=False)
    for i in range(count):
        s.payload_samples.append(bytes(payload))
        s.dlc_values.append(len(payload))
        s.timestamps.append(start + i * period_ms / 1000.0)
    s.count = count
    return s


class TestCandumpReader:
    def test_reads_standard_frames(self, tmp_path) -> None:
        path = _write_log(tmp_path, [
            "(1508687283.891357) slcan0 12E#C680027FD0FFFF00",
            "(1508687283.901357) slcan0 12E#C680027FD0FFFF01",
        ])
        a = TraceAnalyzer(path).analyze()
        assert a.total_frames == 2
        assert a.unique_ids == 1
        s = a.can_stats[0x12E]
        assert s.count == 2
        assert s.payload_samples[0] == bytes.fromhex("C680027FD0FFFF00")
        assert s.is_extended is False

    def test_extended_id_from_digit_count(self, tmp_path) -> None:
        path = _write_log(tmp_path, ["(1.0) can0 12DD54FE#0011"])
        a = TraceAnalyzer(path).analyze()
        assert a.can_stats[0x12DD54FE].is_extended is True

    def test_can_fd_double_hash(self, tmp_path) -> None:
        # ID##<flags><data> -- the nibble after the second '#' is flags.
        path = _write_log(tmp_path, ["(1.0) can0 123##1AABBCC"])
        a = TraceAnalyzer(path).analyze()
        s = a.can_stats[0x123]
        assert s.is_fd is True
        assert s.payload_samples[0] == bytes.fromhex("AABBCC")

    def test_remote_and_error_frames_are_reported_not_analysed(self, tmp_path) -> None:
        path = _write_log(tmp_path, [
            "(1.0) can0 300#R",
            "(1.1) can0 20000004#0000000000000000",   # CAN_ERR_FLAG set
            "(1.2) can0 300#AABB",
        ])
        a = TraceAnalyzer(path).analyze()
        assert 0x300 in a.can_stats and a.can_stats[0x300].count == 1
        assert any("remote/error" in e for e in a.errors)

    def test_malformed_lines_are_reported(self, tmp_path) -> None:
        path = _write_log(tmp_path, [
            "not a candump line at all",
            "(1.0) can0 123#AA",
        ])
        a = TraceAnalyzer(path).analyze()
        assert a.can_stats[0x123].count == 1
        assert any("unparseable" in e for e in a.errors)

    def test_interfaces_map_to_channels_in_order(self, tmp_path) -> None:
        path = _write_log(tmp_path, [
            "(1.0) can0 111#AA",
            "(1.1) can1 222#BB",
        ])
        a = TraceAnalyzer(path).analyze()
        assert a.can_stats[0x111].channel == 1
        assert a.can_stats[0x222].channel == 2
        assert any("interface to channel" in e for e in a.errors)

    def test_pcap_rejected(self, tmp_path) -> None:
        path = tmp_path / "x.pcap"
        path.write_bytes(b"\x00")
        with pytest.raises(ValueError, match="Ethernet-only"):
            TraceAnalyzer(path).analyze()

    def test_unknown_suffix_rejected(self, tmp_path) -> None:
        path = tmp_path / "x.bin"
        path.write_bytes(b"\x00")
        with pytest.raises(ValueError, match="Unsupported format"):
            TraceAnalyzer(path).analyze()


class TestCycleTime:
    def test_periodic_message_snaps_to_raster(self) -> None:
        # 9.98 ms jitters around the 10 ms raster step.
        s = _cyclic(9.98, 200)
        assert TraceAnalyzer._cycle_time_of(s) == 10.0

    def test_off_raster_period_reported_raw(self) -> None:
        s = _cyclic(3000.0, 50)
        assert TraceAnalyzer._cycle_time_of(s) == 3000.0

    def test_too_few_frames_claims_no_period(self) -> None:
        # Three frames give two gaps, and two similar gaps always pass a
        # median-versus-MAD test. That is not evidence of a schedule.
        s = _cyclic(5000.0, 3)
        assert TraceAnalyzer._cycle_time_of(s) == 0.0

    def test_irregular_arrival_claims_no_period(self) -> None:
        s = CanIdStats(channel=1, arbitration_id=1, is_extended=False, is_fd=False)
        for i, t in enumerate([0.0, 0.01, 0.5, 0.52, 3.0, 3.01, 9.0, 9.4, 12.0, 20.0, 31.0]):
            s.payload_samples.append(bytes([i]))
            s.dlc_values.append(1)
            s.timestamps.append(t)
        s.count = len(s.timestamps)
        assert TraceAnalyzer._cycle_time_of(s) == 0.0


class TestTimingProfile:
    @staticmethod
    def _with_fast_transmissions(n_extra: int) -> CanIdStats:
        """100 ms cyclic, plus an early frame carrying changed content."""
        s = CanIdStats(channel=1, arbitration_id=1, is_extended=False, is_fd=False)
        t, value = 0.0, 0
        for i in range(400):
            s.timestamps.append(t)
            s.payload_samples.append(bytes([value]))
            s.dlc_values.append(1)
            t += 0.100
            if i % 8 == 0 and n_extra:
                value += 1                      # content changed ...
                s.timestamps.append(t - 0.070)  # ... so it went out early
                s.payload_samples.append(bytes([value]))
                s.dlc_values.append(1)
        s.count = len(s.timestamps)
        return s

    def test_detects_fast_cycle_when_early_frames_carry_change(self) -> None:
        p = TraceAnalyzer._profile_timing(self._with_fast_transmissions(1))
        assert p.base_ms == 100.0
        assert p.has_fast_cycle
        assert p.change_given_early > p.change_given_on_time * 5
        assert p.early_indices

    def test_no_fast_cycle_without_early_frames(self) -> None:
        p = TraceAnalyzer._profile_timing(self._with_fast_transmissions(0))
        assert p.base_ms == 100.0
        assert not p.has_fast_cycle

    def test_jitter_without_content_change_is_not_a_fast_cycle(self) -> None:
        # Frames arrive early but carry nothing new -- contention, not a
        # schedule. Nothing may be claimed, and the indices are cleared.
        s = CanIdStats(channel=1, arbitration_id=1, is_extended=False, is_fd=False)
        t = 0.0
        for i in range(400):
            s.timestamps.append(t)
            s.payload_samples.append(b"\x07")
            s.dlc_values.append(1)
            t += 0.030 if i % 8 == 0 else 0.100
        s.count = len(s.timestamps)
        p = TraceAnalyzer._profile_timing(s)
        assert p.base_ms == 100.0
        assert not p.has_fast_cycle
        assert p.early_indices == []

    def test_change_fraction_drives_onchange_classification(self) -> None:
        s = CanIdStats(channel=1, arbitration_id=1, is_extended=False, is_fd=False)
        for i in range(50):
            s.timestamps.append(i * 0.01)
            s.payload_samples.append(bytes([i % 251]))
            s.dlc_values.append(1)
        s.count = 50
        p = TraceAnalyzer._profile_timing(s)
        assert p.change_fraction > TraceAnalyzer._ONCHANGE_MIN_CHANGE_FRACTION


def _analysis_of(stats: dict[int, CanIdStats]) -> TraceAnalysis:
    a = TraceAnalysis(path="synthetic")
    a.can_stats = stats
    TraceAnalyzer._detect_cycle_times(a)
    return a


class TestTimingAnomalies:
    def test_quiet_capture_reports_nothing(self) -> None:
        a = _analysis_of({0x100: _cyclic(10.0, 500), 0x200: _cyclic(100.0, 50)})
        assert TraceAnalyzer.find_timing_anomalies(a) == {}

    def test_burst_detected(self) -> None:
        s = _cyclic(20.0, 300)
        # splice in 30 frames at 2 ms, as a replay injection would
        at = 150
        t0 = s.timestamps[at]
        for k in range(30):
            s.timestamps.insert(at + k, t0 + k * 0.002)
            s.payload_samples.insert(at + k, b"\xff")
            s.dlc_values.insert(at + k, 1)
        s.count = len(s.timestamps)
        found = TraceAnalyzer.find_timing_anomalies(_analysis_of({0x2C6: s}))
        assert any(x.kind == "burst" for x in found[0x2C6])

    def test_gap_detected(self) -> None:
        s = _cyclic(20.0, 300)
        for i in range(150, len(s.timestamps)):
            s.timestamps[i] += 10.0          # ten seconds of silence
        found = TraceAnalyzer.find_timing_anomalies(_analysis_of({0x2C6: s}))
        gaps = [x for x in found[0x2C6] if x.kind == "gap"]
        assert gaps and gaps[0].end_ts - gaps[0].start_ts == pytest.approx(10.0, abs=0.1)

    def test_sporadic_id_detected(self) -> None:
        # Three stray frames spread across a capture: no period, and they
        # straddle it so neither onset nor offset fires. Only the count
        # gives them away.
        stray = CanIdStats(channel=1, arbitration_id=0x111, is_extended=False, is_fd=False)
        for t in (1.0, 30.0, 70.0):
            stray.timestamps.append(t)
            stray.payload_samples.append(b"\x00")
            stray.dlc_values.append(1)
        stray.count = 3
        a = _analysis_of({0x100: _cyclic(10.0, 8000), 0x111: stray})
        assert any(x.kind == "sporadic" for x in TraceAnalyzer.find_timing_anomalies(a)[0x111])

    def test_single_frame_id_detected(self) -> None:
        one = CanIdStats(channel=1, arbitration_id=0x444, is_extended=False, is_fd=False)
        one.timestamps.append(40.0)
        one.payload_samples.append(b"\x00")
        one.dlc_values.append(1)
        one.count = 1
        a = _analysis_of({0x100: _cyclic(10.0, 8000), 0x444: one})
        assert any(x.kind == "sporadic" for x in TraceAnalyzer.find_timing_anomalies(a)[0x444])

    def test_late_onset_and_early_offset(self) -> None:
        late = _cyclic(10.0, 200, start=40.0)
        a = _analysis_of({0x100: _cyclic(10.0, 8000), 0x200: late})
        kinds = {x.kind for x in TraceAnalyzer.find_timing_anomalies(a)[0x200]}
        assert "late_onset" in kinds and "early_offset" in kinds


class TestMultiChannelRouting:
    @staticmethod
    def _pair(len_a: int, len_b: int) -> dict[tuple[int, int], CanIdStats]:
        out = {}
        for ch, n in ((1, len_a), (2, len_b)):
            s = CanIdStats(channel=ch, arbitration_id=0x123, is_extended=False, is_fd=False)
            for i in range(60):
                s.payload_samples.append(bytes([i % 256]) * n)
                s.dlc_values.append(n)
                s.timestamps.append(i * 0.01 + (0.0 if ch == 1 else 0.002))
            s.count = 60
            out[(0x123, ch)] = s
        return out

    def test_same_length_becomes_a_routed_copy(self) -> None:
        a = TraceAnalysis(path="x")
        a.can_stats = TraceAnalyzer._resolve_multi_channel_ids(self._pair(8, 8), a)
        assert 0x123 in a.routed_copies
        assert len(a.routed_copies[0x123]) == 1

    def test_different_length_is_not_paired(self) -> None:
        a = TraceAnalysis(path="x")
        a.can_stats = TraceAnalyzer._resolve_multi_channel_ids(self._pair(8, 24), a)
        assert 0x123 not in a.routed_copies
        assert any("repacked" in e for e in a.errors)

    def test_copies_are_timed_separately(self) -> None:
        per = {}
        for ch, period in ((1, 0.010), (2, 0.050)):    # gateway forwards 1 in 5
            s = CanIdStats(channel=ch, arbitration_id=0x5C, is_extended=False, is_fd=False)
            for i in range(200):
                s.payload_samples.append(b"\x01\x02")
                s.dlc_values.append(2)
                s.timestamps.append(i * period)
            s.count = 200
            per[(0x5C, ch)] = s
        a = TraceAnalysis(path="x")
        a.can_stats = TraceAnalyzer._resolve_multi_channel_ids(per, a)
        TraceAnalyzer._detect_cycle_times(a)
        assert a.cycle_times_ms[0x5C] == 10.0
        assert a.routed_copies[0x5C][0].cycle_time_ms == 50.0


# --------------------------------------------------------------------------
# Real captures. These skip unless the traces are present, so the suite runs
# anywhere -- but where they are available they are the only checks here
# graded against something other than this file's own reasoning: the dataset
# ships READMEs naming every injected CAN ID and timestamp.
# --------------------------------------------------------------------------

CLIO = Path.home() / "CAN_Traces" / "data" / "RenaultClio"

requires_clio = pytest.mark.skipif(
    not (CLIO / "full_data_capture.log").exists(),
    reason="Renault Clio capture not present on this machine",
)


@requires_clio
class TestRealCapture:
    def test_load_matches_the_dataset_readme(self) -> None:
        a = TraceAnalyzer(CLIO / "full_data_capture.log").analyze()
        assert a.total_frames == 386567          # README: 386567 packets
        assert a.unique_ids == 55                # README: 55 unique CAN IDs
        span = (max(s.timestamps[-1] for s in a.can_stats.values())
                - min(s.timestamps[0] for s in a.can_stats.values()))
        assert span == pytest.approx(275.09, abs=0.01)   # README: 275.09 s

    @pytest.mark.parametrize("capture,injected", [
        ("testing", set()),                                  # unmodified control
        ("dosattack", {0x000}),
        ("replay", {0x2C6}),
        ("suspension", {0x2C6}),
        ("fuzzing_canid", {0x111, 0x222, 0x333, 0x444}),
        ("diagnostic", {0x760, 0x7E0, 0x726}),
    ])
    def test_anomalies_match_the_documented_injections(self, capture, injected) -> None:
        path = CLIO / f"{capture}.log"
        if not path.exists():
            pytest.skip(f"{capture}.log not present")
        a = TraceAnalyzer(path).analyze()
        flagged = set(TraceAnalyzer.find_timing_anomalies(a))
        assert injected <= flagged, f"missed {sorted(injected - flagged)}"
        if capture == "testing":
            assert flagged == set(), "control capture must stay quiet"
        elif capture != "dosattack":
            # The DoS replaces all traffic in its window, so every other
            # message legitimately falls silent for ten seconds. Every
            # other attack should touch only what it injected.
            assert flagged == injected

    def test_dos_displaces_the_rest_of_the_traffic(self) -> None:
        path = CLIO / "dosattack.log"
        if not path.exists():
            pytest.skip("dosattack.log not present")
        a = TraceAnalyzer(path).analyze()
        found = TraceAnalyzer.find_timing_anomalies(a)
        gaps = [aid for aid, items in found.items()
                if any(x.kind == "gap" for x in items)]
        # README: the flood replaces a ten-second block of traffic.
        assert len(gaps) > 40
