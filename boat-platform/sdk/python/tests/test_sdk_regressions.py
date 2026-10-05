# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

"""Regression locks for the correctness bugs fixed on sdk_rework.

Each test here corresponds to a defect that a green suite failed to catch,
and the docstring says what the old behaviour was, so a regression is
recognisable rather than just red.
"""

import xml.etree.ElementTree as ET
from unittest.mock import MagicMock

import grpc
import pytest

from boat.client import DEFAULT_ADDRESS, resolve_address


class _FakeRpcError(grpc.RpcError):
    """Minimal stand-in for what a gRPC stream raises when its deadline fires."""

    def __init__(self, code: grpc.StatusCode) -> None:
        super().__init__(str(code))
        self._code = code

    def code(self) -> grpc.StatusCode:
        return self._code


# ── Address resolution: explicit > BOAT_HOST > default ───────────────────────

class TestAddressResolution:
    """CanTpHandle/TraceRecorder/TraceReplayer defaulted to the *literal*
    "localhost:50051", which is indistinguishable from a caller-supplied
    address and so beat BOAT_HOST. FrameNode and PduNode passed None and
    honoured it, giving the SDK two different behaviours."""

    def test_resolve_prefers_explicit(self, monkeypatch) -> None:
        monkeypatch.setenv("BOAT_HOST", "env:1")
        assert resolve_address("explicit:2") == "explicit:2"

    def test_resolve_falls_back_to_env(self, monkeypatch) -> None:
        monkeypatch.setenv("BOAT_HOST", "env:1")
        assert resolve_address(None) == "env:1"

    def test_resolve_falls_back_to_default(self, monkeypatch) -> None:
        monkeypatch.delenv("BOAT_HOST", raising=False)
        assert resolve_address(None) == DEFAULT_ADDRESS

    @pytest.mark.parametrize("ctor_name", [
        "FrameNode", "PduNode", "CanTpHandle", "TraceRecorder", "TraceReplayer",
    ])
    def test_every_entry_point_honours_boat_host(self, monkeypatch, ctor_name) -> None:
        monkeypatch.setenv("BOAT_HOST", "gw.example:60051")

        from boat.can_tp import CanTpHandle
        from boat.frame_node import FrameNode
        from boat.pdu_node import PduNode
        from boat.trace_recorder import TraceRecorder
        from boat.trace_replay import TraceReplayer

        addresses = {
            "FrameNode":     lambda: FrameNode().client.address,
            "PduNode":       lambda: PduNode()._client.address,
            "CanTpHandle":   lambda: CanTpHandle()._client.address,
            "TraceRecorder": lambda: TraceRecorder().gateway,
            "TraceReplayer": lambda: TraceReplayer().gateway,
        }
        assert addresses[ctor_name]() == "gw.example:60051"


# ── JUnit XML escaping ───────────────────────────────────────────────────────

class TestJUnitXmlEscaping:
    """_generate_junit_xml interpolated step names and assertion strings into
    attributes unescaped, so a single '<', '&' or '"' made the document
    unparseable and a CI runner discarded the whole report."""

    def _report_with(self, step_name: str, expr: str, expected: str, actual: str):
        from boat.test.report import (AssertionRecord, TestInfo, TestReport,
                                      TestStepRecord)
        r = TestReport()
        r.test = TestInfo(id="t1", name='Suite "A" & <B>')
        step = TestStepRecord(id=1, name=step_name)
        step.add_assertion(AssertionRecord.fail(expr, expected, actual))
        step.verdict = "FAIL"
        r.add_step(step)
        return r

    def test_metacharacters_still_parse(self) -> None:
        from boat.test.runner import _generate_junit_xml
        xml = _generate_junit_xml(
            self._report_with('Verify a < b & "quoted"', "x & y < z", '"1"', "<2>")
        )
        root = ET.fromstring(xml)  # the actual assertion: it parses at all
        tc = root.find("testcase")
        assert tc.get("name") == 'Verify a < b & "quoted"'
        assert tc.find("failure").get("message") == "x & y < z"

    def test_error_verdict_also_escapes(self) -> None:
        from boat.test.runner import _generate_junit_xml
        r = self._report_with("step & <x>", "e & <y>", "ok", "boom & <z>")
        r.steps[0].verdict = "ERROR"
        root = ET.fromstring(_generate_junit_xml(r))
        assert root.find("testcase").find("error").get("message") == "e & <y>"

    def test_plain_names_are_unchanged(self) -> None:
        from boat.test.runner import _generate_junit_xml
        xml = _generate_junit_xml(self._report_with("plain name", "a == b", "1", "2"))
        assert ET.fromstring(xml).find("testcase").get("name") == "plain name"


# ── boat.test star-import ────────────────────────────────────────────────────

def test_star_import_exposes_everything_in_all() -> None:
    """__all__ listed generate_allure_results but __init__ never imported it,
    so `from boat.test import *` raised AttributeError."""
    import boat.test as mod

    namespace: dict = {}
    exec("from boat.test import *", namespace)  # noqa: S102 - the behaviour under test
    for name in mod.__all__:
        assert hasattr(mod, name), f"{name} in __all__ but not importable"
        assert name in namespace


# ── expect() honours its timeout ─────────────────────────────────────────────

class TestExpectTimeout:
    """expect() only checked its deadline when a frame arrived and set no gRPC
    deadline, so a silent bus blocked forever. The pre-existing timeout test
    passed iter([]) -- an immediately-exhausted iterator -- which is exactly
    why the hang was invisible.

    The deadline is enforced by gRPC itself, so what the SDK is responsible
    for is (a) handing the deadline to the RPC and (b) translating the
    resulting DEADLINE_EXCEEDED into TestTimeoutError. Both are asserted
    below. A mock cannot honour a timeout kwarg, so a test built on a
    blocking MagicMock stream would be checking the mock, not the fix.
    """

    @staticmethod
    def _deadline_exceeded_stream():
        """Behaves like a real idle subscription: blocks, then DEADLINE_EXCEEDED."""
        def gen():
            raise _FakeRpcError(grpc.StatusCode.DEADLINE_EXCEEDED)
            yield  # pragma: no cover - never reached
        return gen()

    @staticmethod
    def _aborted_stream():
        def gen():
            raise _FakeRpcError(grpc.StatusCode.UNAVAILABLE)
            yield  # pragma: no cover - never reached
        return gen()

    def _can_bus(self, client):
        from boat.test.bus import TestCanBus
        from boat.test.config import BusConfig
        return TestCanBus(client, BusConfig(logical_name="can1", type="virtual",
                                            interface="vcan0"))

    def test_can_expect_passes_grpc_deadline(self) -> None:
        """The deadline must reach gRPC -- that is what bounds a real stream."""
        from boat.test.exceptions import TestTimeoutError

        client = MagicMock()
        client.can.SubscribeCanFrames.return_value = iter([])
        with pytest.raises(TestTimeoutError):
            self._can_bus(client).expect(can_id=0x999, timeout_ms=250)

        _, kwargs = client.can.SubscribeCanFrames.call_args
        assert kwargs["timeout"] == pytest.approx(0.25)

    def test_can_deadline_exceeded_becomes_test_timeout(self) -> None:
        """gRPC's DEADLINE_EXCEEDED is the timeout, not an infrastructure error."""
        from boat.test.exceptions import TestTimeoutError

        client = MagicMock()
        client.can.SubscribeCanFrames.return_value = self._deadline_exceeded_stream()
        with pytest.raises(TestTimeoutError):
            self._can_bus(client).expect(can_id=0x999, timeout_ms=200)

    def test_can_other_rpc_errors_still_surface(self) -> None:
        """A real failure must not be laundered into a timeout."""
        client = MagicMock()
        client.can.SubscribeCanFrames.return_value = self._aborted_stream()
        with pytest.raises(RuntimeError, match="CAN subscribe error"):
            self._can_bus(client).expect(can_id=0x999, timeout_ms=200)

    def test_eth_expect_passes_grpc_deadline(self) -> None:
        from boat.test.bus import TestEthBus
        from boat.test.config import BusConfig
        from boat.test.exceptions import TestTimeoutError

        client = MagicMock()
        client.ethernet.SubscribeFrames.return_value = iter([])
        bus = TestEthBus(client, BusConfig(logical_name="eth0", type="virtual_eth",
                                           interface="veth0"))
        with pytest.raises(TestTimeoutError):
            bus.expect(ethertype=0x88B5, timeout_ms=250)

        _, kwargs = client.ethernet.SubscribeFrames.call_args
        assert kwargs["timeout"] == pytest.approx(0.25)

    def test_eth_deadline_exceeded_becomes_test_timeout(self) -> None:
        from boat.test.bus import TestEthBus
        from boat.test.config import BusConfig
        from boat.test.exceptions import TestTimeoutError

        client = MagicMock()
        client.ethernet.SubscribeFrames.return_value = self._deadline_exceeded_stream()
        bus = TestEthBus(client, BusConfig(logical_name="eth0", type="virtual_eth",
                                           interface="veth0"))
        with pytest.raises(TestTimeoutError):
            bus.expect(ethertype=0x88B5, timeout_ms=200)


# ── assert_frame_matches on a missing frame ──────────────────────────────────

class TestAssertFrameMatches:
    """It dereferenced frame.can_id in the failure path, so passing None --
    what a caller has after a timeout -- raised AttributeError instead of
    recording a FAIL. expected/actual were also passed the wrong way round."""

    @staticmethod
    def _ctx():
        from boat.test.harness import StepContext
        from boat.test.report import TestStepRecord
        return StepContext(TestStepRecord(id=1, name="step"))

    class _Frame:
        can_id = 0x100
        data = b"\x01\x02"

    def test_none_frame_records_fail(self) -> None:
        ctx = self._ctx()
        ctx.assert_frame_matches(None, can_id=0x100)
        record = ctx.record.assertions[-1]
        assert record.result == "FAIL"
        assert record.actual == "no frame"

    def test_matching_frame_records_pass(self) -> None:
        ctx = self._ctx()
        ctx.assert_frame_matches(self._Frame(), can_id=0x100)
        assert ctx.record.assertions[-1].result == "PASS"

    def test_mismatch_reports_frame_as_actual(self) -> None:
        ctx = self._ctx()
        ctx.assert_frame_matches(self._Frame(), can_id=0x999)
        record = ctx.record.assertions[-1]
        assert record.result == "FAIL"
        assert "0x100" in record.actual      # the frame is the *actual*
        assert "0x999" in record.expected    # the criteria are the *expected*


# ── __test__ = False must not displace the docstring ─────────────────────────

@pytest.mark.parametrize("cls_path", [
    ("boat.test.harness", "TestHarness"),
    ("boat.test.bus", "TestCanBus"),
    ("boat.test.bus", "TestEthBus"),
])
def test_pytest_opt_out_does_not_eat_the_docstring(cls_path) -> None:
    """`__test__ = False` placed above the string literal made it an ordinary
    expression statement, so __doc__ was None and help() showed nothing."""
    import importlib

    module, name = cls_path
    cls = getattr(importlib.import_module(module), name)
    assert cls.__test__ is False
    assert cls.__doc__ is not None and cls.__doc__.strip()


# ── PduMessageNode packs through Message ─────────────────────────────────────

def test_pdu_message_node_honours_factor_and_offset() -> None:
    """Its own packer read InitValue as a raw value and ignored Factor/Offset,
    so a Factor 0.1 signal went out scaled wrong (here: 1 instead of 400)."""
    from boat.message import Message

    entry = {
        "MessageName": "Motor_1", "DbId": 15, "BusType": "CAN", "Bus": "Motor_CAN",
        "Length": 2, "Identifier": 0x123,
        "signals": [{
            "SignalName": "MotorSpeed", "StartPos": 0, "Length": 16, "ByteOrder": 0,
            "ValueType": "Unsigned", "Factor": 0.1, "Offset": 0.0, "InitValue": 40.0,
        }],
    }
    # InitValue 40.0 physical / Factor 0.1 => raw 400 => Intel 16-bit "9001".
    assert Message(entry).pack().hex() == "9001"


# ── MockDutBackend no longer advertises a no-op hook ─────────────────────────

def test_mock_dut_has_no_dead_on_can_hook() -> None:
    """on_can() stored handlers in a dict nothing read, so registering one
    looked like it worked and then silently never fired."""
    from boat.test.dut import MockDutBackend

    assert not hasattr(MockDutBackend, "on_can")


# ── ScenarioBuilder emits exactly the loader's keys ──────────────────────────

def test_scenario_builder_rejects_phantom_tick_rate() -> None:
    """tick_rate_hz was accepted, stored and never emitted; the gateway has no
    per-scenario tick rate at all (it comes from BOAT_NODE_TICK_MS/_US)."""
    from boat.scenario_builder import ScenarioBuilder

    with pytest.raises(TypeError):
        ScenarioBuilder(tick_rate_hz=100)

    built = ScenarioBuilder(name="s").build()
    assert "tick_rate_hz" not in built
