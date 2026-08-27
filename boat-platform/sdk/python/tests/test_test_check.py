# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import patch, MagicMock

from boat.test.check import check_environment
from boat.test.config import EnvironmentConfig, GatewayConfig, BusConfig


def _make_config(bus_type="virtual", iface="vcan0", dut_type=None, gateway_binary=None) -> EnvironmentConfig:
    return EnvironmentConfig(
        schema_version="1.0",
        name="test-env",
        description="test",
        gateway=GatewayConfig(binary=gateway_binary, tick_ms=10, address=""),
        buses={"can1": BusConfig(logical_name="can1", type=bus_type, interface=iface)},
        dut=None,
    )


class TestCheckEnvironment:
    def test_clean_virtual(self) -> None:
        cfg = _make_config()
        with patch("os.path.isfile", return_value=True), \
             patch("os.path.exists", return_value=True), \
             patch("boat.test.check._read_sysfs", return_value="up"):
            issues = check_environment(cfg)
            assert len(issues) == 0, issues

    def test_gateway_binary_missing(self) -> None:
        cfg = _make_config(gateway_binary="/nonexistent/gateway")
        with patch("os.path.isfile", return_value=False), \
             patch("os.path.exists", return_value=True), \
             patch("boat.test.check._read_sysfs", return_value="up"):
            issues = check_environment(cfg)
            assert any("Gateway binary" in i for i in issues)

    def test_can_interface_missing(self) -> None:
        cfg = _make_config()
        with patch("os.path.exists", return_value=False), \
             patch("os.path.isfile", return_value=True):
            issues = check_environment(cfg)
            assert any("not found" in i for i in issues)

    def test_empty_config_no_issues(self) -> None:
        cfg = EnvironmentConfig(
            schema_version="1.0", name="minimal", description="",
            gateway=GatewayConfig(address=""),
            buses={},
        )
        issues = check_environment(cfg)
        assert len(issues) == 0

    def test_physical_can_with_bound_driver_is_clean(self) -> None:
        # device/driver is a symlink to the driver's own sysfs directory
        # (e.g. /sys/bus/usb/drivers/peak_usb), not a regular file --
        # open(path).read() (what _read_sysfs does for plain attributes
        # like operstate) always raises IsADirectoryError against it,
        # regardless of whether a driver is genuinely bound. This is the
        # real-hardware bug _read_driver_link() exists to fix: verified
        # against a real PEAK-USB dongle on agn-testcomputer, where this
        # path previously reported "no driver detected" unconditionally.
        cfg = _make_config(bus_type="physical", iface="can0")
        with patch("os.path.exists", return_value=True), \
             patch("boat.test.check._read_sysfs", return_value="up"), \
             patch("os.readlink", return_value="/sys/bus/usb/drivers/peak_usb"):
            issues = check_environment(cfg)
            assert len(issues) == 0, issues

    def test_physical_can_with_no_driver_bound(self) -> None:
        cfg = _make_config(bus_type="physical", iface="can0")
        with patch("os.path.exists", return_value=True), \
             patch("boat.test.check._read_sysfs", return_value="up"), \
             patch("os.readlink", side_effect=OSError("no such file")):
            issues = check_environment(cfg)
            assert any("no driver detected" in i for i in issues)
