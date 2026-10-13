# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

import xml.etree.ElementTree as ET
from unittest.mock import MagicMock, patch

import pytest

from boat.test.config import ManifestConfig, ManifestTestEntry
from boat.test.runner import TestSuiteRunner, _generate_junit_xml
from boat.test.report import TestReport, TestInfo, TestStepRecord, AssertionRecord


class TestJunitXmlGeneration:
    def test_empty_passing(self) -> None:
        report = TestReport()
        report.test = TestInfo(id="TC-1", name="Test 1")
        xml = _generate_junit_xml(report)
        assert 'tests="0"' in xml
        assert 'failures="0"' in xml
        assert "testsuite" in xml

    def test_step_mapped_to_testcase(self) -> None:
        report = TestReport()
        report.test = TestInfo(id="TC-1", name="Test 1")
        step = TestStepRecord(id=1, name="Step 1", verdict="PASS")
        report.add_step(step)
        xml = _generate_junit_xml(report)
        assert 'tests="1"' in xml
        assert 'name="Step 1"' in xml

    def test_failure_included(self) -> None:
        report = TestReport()
        report.test = TestInfo(id="TC-1", name="Test 1")
        step = TestStepRecord(id=1, name="Fail Step", verdict="FAIL")
        step.add_assertion(AssertionRecord.fail("x == 42", "42", "0"))
        report.add_step(step)
        xml = _generate_junit_xml(report)
        assert 'failures="1"' in xml
        assert "<failure" in xml

    def test_script_failure_is_one_failing_testcase(self) -> None:
        """A script test has no steps to map. Before this it produced
        tests="0" failures="0" -- a CI JUnit reporter showed a green run for a
        script that exited 1 (issue #50)."""
        report = TestReport()
        report.test = TestInfo(id="TC-1", name="Script test")
        report.verdict = "FAIL"
        report.summary = "script exited with code 1"
        report.execution.duration_ms = 1177

        root = ET.fromstring(_generate_junit_xml(report))
        assert root.get("tests") == "1"
        assert root.get("failures") == "1"
        testcase = root.find("testcase")
        assert testcase is not None
        assert testcase.get("name") == "Script test"
        failure = testcase.find("failure")
        assert failure is not None
        assert failure.get("message") == "script exited with code 1"

    def test_script_pass_is_one_passing_testcase(self) -> None:
        report = TestReport()
        report.test = TestInfo(id="TC-1", name="Script test")
        report.verdict = "PASS"
        root = ET.fromstring(_generate_junit_xml(report))
        assert root.get("tests") == "1"
        assert root.get("failures") == "0"
        assert root.find("testcase").find("failure") is None

    def test_unstarted_report_stays_empty(self) -> None:
        # verdict RUNNING with no steps is "nothing has run yet", not a test.
        report = TestReport()
        report.test = TestInfo(id="TC-1", name="Test 1")
        root = ET.fromstring(_generate_junit_xml(report))
        assert root.get("tests") == "0"

    def test_script_failure_reason_reaches_junit_and_report(self, tmp_path) -> None:
        """End to end through the runner: a real failing subprocess must set
        the report's summary and land it in report.junit.xml."""
        manifest = ManifestConfig(
            schema_version="1.0", name="suite",
            tests=[ManifestTestEntry(id="T1", name="Failing script", file="false")],
        )
        runner = TestSuiteRunner(manifest, MagicMock(), report_dir=str(tmp_path),
                                 generate_html=False)
        harness = MagicMock()
        harness.config.snapshot.return_value = {}

        assert runner._run_single_test(harness, manifest.tests[0]) is False

        folder = next(tmp_path.iterdir())
        root = ET.fromstring((folder / "report.junit.xml").read_text())
        assert root.get("tests") == "1"
        assert root.get("failures") == "1"
        assert root.find("testcase").find("failure").get("message") == "script exited with code 1"

        import json
        saved = json.loads((folder / "report.json").read_text())
        assert saved["verdict"] == "FAIL"
        assert saved["summary"] == "script exited with code 1"


class TestRunner:
    def test_init(self) -> None:
        manifest = ManifestConfig(schema_version="1.0", name="suite",
                                   tests=[ManifestTestEntry(id="T1", name="Test 1", file="echo ok")])
        env_cfg = MagicMock()
        runner = TestSuiteRunner(manifest, env_cfg, report_dir="/tmp/reports")
        assert runner.manifest.name == "suite"
        assert len(runner.manifest.tests) == 1

    def test_run_delegates_to_harness(self, tmp_path) -> None:
        from boat.test.config import EnvironmentConfig, GatewayConfig, BusConfig
        manifest = ManifestConfig(schema_version="1.0", name="suite",
                                   tests=[ManifestTestEntry(id="T1", name="Test 1", file="echo ok")])
        env_cfg = EnvironmentConfig(
            schema_version="1.0", name="test", description="test env",
            gateway=GatewayConfig(address="localhost:50051"),
            buses={"can1": BusConfig(logical_name="can1", type="virtual", interface="vcan0")},
            dut=None,
        )
        runner = TestSuiteRunner(manifest, env_cfg, report_dir=str(tmp_path))
        assert runner.manifest.name == "suite"
        assert len(runner.manifest.tests) == 1
        assert runner.env_config.name == "test"

    def test_setup_error_is_reported_in_summary(self, tmp_path, capsys) -> None:
        from boat.test.config import EnvironmentConfig, GatewayConfig, BusConfig
        manifest = ManifestConfig(schema_version="1.0", name="suite",
                                   tests=[ManifestTestEntry(id="T1", name="Test 1", file="echo ok")])
        env_cfg = EnvironmentConfig(
            schema_version="1.0", name="test", description="test env",
            gateway=GatewayConfig(address="localhost:50051"),
            buses={"can1": BusConfig(logical_name="can1", type="virtual", interface="vcan0")},
            dut=None,
        )
        runner = TestSuiteRunner(manifest, env_cfg, report_dir=str(tmp_path))

        with patch("boat.test.runner.TestHarness.start",
                   side_effect=RuntimeError("boom during setup")):
            rc = runner.run()

        assert rc == 1
        err = capsys.readouterr().err
        assert "setup failed" in err
        assert "boom during setup" in err
