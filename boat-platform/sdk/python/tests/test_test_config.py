# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

import os

from boat.test.config import EnvironmentConfig, ManifestConfig


class TestEnvironmentConfig:
    def test_from_dict(self) -> None:
        raw = {
            "schema_version": "1.0",
            "name": "test-env",
            "description": "A test environment",
            "gateway": {"address": "localhost:50051", "tick_ms": 10},
            "buses": {
                "can1": {"type": "virtual", "interface": "vcan0"},
                "eth0": {"type": "virtual_eth", "interface": "veth0",
                         "multicast_group": "239.255.0.1", "port": 51000},
            },
            "dut": {"name": "sim-dut", "type": "plugin",
                    "so_path": "plugin.so", "config_json": "{}"},
            "plugins": [{"so_path": "extra.so", "config_json": '{"a":1}'}],
        }
        cfg = EnvironmentConfig.from_dict(raw)
        assert cfg.schema_version == "1.0"
        assert cfg.name == "test-env"
        assert cfg.description == "A test environment"
        assert cfg.gateway.address == "localhost:50051"
        assert cfg.gateway.tick_ms == 10
        assert "can1" in cfg.buses
        assert cfg.buses["can1"].type == "virtual"
        assert cfg.buses["can1"].interface == "vcan0"
        assert cfg.buses["eth0"].multicast_group == "239.255.0.1"
        assert cfg.buses["eth0"].port == 51000
        assert cfg.dut is not None
        assert cfg.dut.name == "sim-dut"
        assert cfg.dut.so_path == "plugin.so"
        assert len(cfg.plugins) == 1
        assert cfg.plugins[0].so_path == "extra.so"

    def test_round_trip(self) -> None:
        raw = {
            "schema_version": "1.0",
            "name": "roundtrip",
            "gateway": {"address": "127.0.0.1:50051", "tick_ms": 5},
            "buses": {
                "can1": {"type": "physical", "interface": "can0",
                         "bitrate": 500000, "fd": True},
            },
        }
        cfg = EnvironmentConfig.from_dict(raw)
        restored = EnvironmentConfig.from_dict(cfg.to_dict())
        assert restored.name == "roundtrip"
        assert restored.gateway.tick_ms == 5
        assert restored.buses["can1"].bitrate == 500000
        assert restored.buses["can1"].fd is True

    def test_validate_ok(self) -> None:
        cfg = EnvironmentConfig.from_dict({
            "schema_version": "1.0",
            "name": "valid",
            "gateway": {"address": "x:1"},
            "buses": {"can1": {"type": "virtual", "interface": "vcan0"}},
        })
        issues = cfg.validate()
        assert len(issues) == 0

    def test_validate_virtual_wrong_iface(self) -> None:
        cfg = EnvironmentConfig.from_dict({
            "schema_version": "1.0",
            "name": "bad",
            "gateway": {"address": "x:1"},
            "buses": {"can1": {"type": "virtual", "interface": "can0"}},
        })
        issues = cfg.validate()
        assert any("vcan*" in i for i in issues)

    def test_validate_plugin_no_so(self) -> None:
        cfg = EnvironmentConfig.from_dict({
            "schema_version": "1.0",
            "name": "bad",
            "gateway": {"address": "x:1"},
            "buses": {"can1": {"type": "virtual", "interface": "vcan0"}},
            "dut": {"name": "x", "type": "plugin"},
        })
        issues = cfg.validate()
        assert any("so_path" in i for i in issues)


class TestManifestConfig:
    def test_from_dict(self) -> None:
        raw = {
            "schema_version": "1.0",
            "name": "suite1",
            "version": "1.2.0",
            "description": "A test suite",
            "environment_config": "env_virtual.json",
            "setup": [{"action": "load_scenario", "params": {"id": "s1"}}],
            "teardown": [{"action": "cleanup", "params": {}}],
            "tests": [
                {"id": "TC-001", "name": "Test 1", "file": "test1",
                 "timeout_s": 30},
                {"id": "TC-002", "name": "Test 2", "file": "test2"},
            ],
        }
        m = ManifestConfig.from_dict(raw)
        assert m.name == "suite1"
        assert m.version == "1.2.0"
        assert m.environment_config == "env_virtual.json"
        assert len(m.setup) == 1
        assert m.setup[0].action == "load_scenario"
        assert len(m.tests) == 2
        assert m.tests[0].id == "TC-001"
        assert m.tests[0].timeout_s == 30
        assert m.tests[1].timeout_s == 60  # default

    def test_round_trip(self) -> None:
        raw = {
            "schema_version": "1.0",
            "name": "round",
            "tests": [{"id": "T1", "name": "T1", "file": "f1"}],
        }
        m = ManifestConfig.from_dict(raw)
        restored = ManifestConfig.from_dict(m.to_dict())
        assert restored.name == "round"
        assert restored.tests[0].id == "T1"


class TestManifestPathResolution:
    """`boat test run <manifest>` used to only work from boat-platform/.

    A relative `environment_config` was resolved against the caller's working
    directory, so running the documented command from the repo root died with
    "Config not found: config/tests/env_can_loopback.json". Relative paths in a
    manifest now resolve against the manifest's own directory, which also makes
    a suite directory relocatable.
    """

    def _write(self, tmp_path, name, payload):
        import json
        p = tmp_path / name
        p.write_text(json.dumps(payload))
        return p

    def _manifest(self, tmp_path):
        return self._write(tmp_path, "manifest.json", {
            "schema_version": "1.0",
            "name": "suite",
            "environment_config": "env.json",
            "tests": [{"id": "T1", "name": "T1", "file": "python3 t1.py"}],
        })

    def test_from_file_records_base_dir(self, tmp_path) -> None:
        m = ManifestConfig.from_file(str(self._manifest(tmp_path)))
        assert m.base_dir is not None
        assert os.path.isabs(m.base_dir)
        assert os.path.samefile(m.base_dir, str(tmp_path))

    def test_from_dict_has_no_base_dir(self) -> None:
        # Nothing to resolve against, so resolve() must pass paths through.
        m = ManifestConfig.from_dict({"schema_version": "1.0", "name": "s"})
        assert m.base_dir is None
        assert m.resolve("env.json") == "env.json"

    def test_resolves_sibling_against_manifest_dir(self, tmp_path) -> None:
        manifest = self._manifest(tmp_path)
        (tmp_path / "env.json").write_text("{}")
        m = ManifestConfig.from_file(str(manifest))
        resolved = m.resolve("env.json")
        assert resolved != "env.json"          # not left CWD-relative
        assert os.path.isfile(resolved)

    def test_absolute_path_passes_through(self, tmp_path) -> None:
        m = ManifestConfig.from_file(str(self._manifest(tmp_path)))
        assert m.resolve("/etc/hostname") == "/etc/hostname"

    def test_falls_back_to_cwd_for_old_style_paths(self, tmp_path) -> None:
        # Backward compatibility: a manifest written against the old
        # project-root convention points at something not next to itself, and
        # must still come back unchanged for the caller's CWD to resolve.
        m = ManifestConfig.from_file(str(self._manifest(tmp_path)))
        assert m.resolve("config/tests/env_virtual.json") == "config/tests/env_virtual.json"


class TestEnvironmentConfigPathResolution:
    """`gateway.binary` had the same CWD dependency, and failed *silently*:
    harness.start() did `if binary and os.path.isfile(binary)` and simply did
    not start a gateway when the path missed, so the test then failed with an
    opaque connection error."""

    def _env(self, tmp_path, binary):
        import json
        p = tmp_path / "env.json"
        p.write_text(json.dumps({
            "schema_version": "1.0",
            "name": "env",
            "gateway": {"address": "localhost:50051", "binary": binary},
            "buses": {},
        }))
        return p

    def test_resolves_relative_binary_against_config_dir(self, tmp_path) -> None:
        build = tmp_path / "build"
        build.mkdir()
        (build / "boat_gateway").write_text("#!/bin/sh\n")
        cfg = EnvironmentConfig.from_file(str(self._env(tmp_path, "build/boat_gateway")))
        resolved = cfg.resolve_path("build/boat_gateway")
        assert os.path.isfile(resolved)

    def test_normalises_parent_traversal(self, tmp_path) -> None:
        # The shipped configs under config/tests/ use ../../build/..., so the
        # join must normalise rather than leave "a/b/../.." in the path.
        sub = tmp_path / "config" / "tests"
        sub.mkdir(parents=True)
        (tmp_path / "gw").write_text("x")
        cfg = EnvironmentConfig.from_file(str(self._env(sub, "../../gw")))
        resolved = cfg.resolve_path("../../gw")
        assert os.path.isfile(resolved)
        assert ".." not in resolved

    def test_missing_binary_passes_through_for_caller_to_report(self, tmp_path) -> None:
        cfg = EnvironmentConfig.from_file(str(self._env(tmp_path, "nope/boat_gateway")))
        assert cfg.resolve_path("nope/boat_gateway") == "nope/boat_gateway"
