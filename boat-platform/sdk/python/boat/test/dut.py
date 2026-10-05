# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional

from boat.test.exceptions import TestDutError


class DutBackend(ABC):
    """Abstract backend for a DUT."""

    @abstractmethod
    def configure(self, config: dict) -> None:
        ...

    @abstractmethod
    def reset(self) -> None:
        ...

    @property
    def version(self) -> Optional[str]:
        return None


class PluginDutBackend(DutBackend):
    """DUT loaded as a gateway plugin."""

    def __init__(self, client, config) -> None:
        self._client = client
        self._config = config
        self._plugin_id: Optional[str] = None

    def configure(self, config: dict) -> None:
        from boat.v1 import plugin_pb2

        so_path = config.get("so_path") or self._config.so_path
        if not so_path:
            raise TestDutError("Plugin DUT requires so_path")
        try:
            req = plugin_pb2.RegisterPluginRequest(path=so_path)
            resp = self._client.plugin.RegisterPlugin(req)
            self._plugin_id = resp.plugin.plugin_id
        except Exception as exc:
            raise TestDutError(f"Failed to register plugin DUT: {exc}") from exc

    def reset(self) -> None:
        """Unload and re-register the plugin, returning it to initial state.

        The re-register is the point: unloading alone left no DUT at all, so
        "reset" silently destroyed it and every subsequent step ran against
        nothing. Re-registration goes through configure() so there is one
        code path that knows how to bring a plugin DUT up.
        """
        if not self._plugin_id:
            return

        from boat.v1 import plugin_pb2

        try:
            self._client.plugin.UnloadPlugin(
                plugin_pb2.UnloadPluginRequest(plugin_id=self._plugin_id)
            )
        except Exception as exc:
            raise TestDutError(f"Failed to unload plugin DUT for reset: {exc}") from exc
        finally:
            self._plugin_id = None

        self.configure({})

    @property
    def version(self) -> Optional[str]:
        if self._plugin_id:
            from boat.v1 import plugin_pb2

            try:
                resp = self._client.plugin.GetPluginInfo(
                    plugin_pb2.GetPluginInfoRequest(plugin_id=self._plugin_id)
                )
                return resp.plugin.version or None
            except Exception:
                pass
        return None


class PhysicalDutBackend(DutBackend):
    """A real ECU on the bus. No plugin management — just a reference."""

    def __init__(self, client, config) -> None:
        self._client = client
        self._config = config

    def configure(self, config: dict) -> None:
        pass

    def reset(self) -> None:
        pass


class MockDutBackend(DutBackend):
    """In-process mock DUT: satisfies the DutBackend contract, does nothing.

    There used to be an ``on_can(can_id, handler)`` here that recorded
    handlers into a dict nothing ever read, so registering one looked like it
    worked and then silently never fired. It is removed rather than left in
    place; wiring this backend into the frame path is a feature, not a fix.
    """

    def __init__(self, client=None, config=None) -> None:
        self._config = config

    def configure(self, config: dict) -> None:
        pass

    def reset(self) -> None:
        pass


class DutProxy:
    """High-level proxy for the Device Under Test.

    Wraps three backends:
        - **plugin**: DUT loaded as a gateway plugin via gRPC PluginService.
        - **physical**: Real ECU on the bus (no gateway-side management).
        - **mock**: In-process mock for unit testing.

    Usage::

        dut = harness.dut
        dut.configure({"mode": "normal"})
        print(dut.version)
        dut.reset()
    """

    def __init__(self, client, config) -> None:
        self._client = client
        self._config = config
        self._backend = self._create_backend()

    def _create_backend(self) -> DutBackend:
        if self._config is None:
            return MockDutBackend()
        t = self._config.type
        if t == "plugin":
            return PluginDutBackend(self._client, self._config)
        elif t == "physical":
            return PhysicalDutBackend(self._client, self._config)
        elif t == "mock":
            return MockDutBackend(self._client, self._config)
        raise TestDutError(f"Unknown DUT type: {t}")

    def configure(self, config: dict) -> None:
        """Send configuration to the DUT."""
        self._backend.configure(config)

    def reset(self) -> None:
        """Reset the DUT to known initial state."""
        self._backend.reset()

    @property
    def version(self) -> Optional[str]:
        """Firmware/software version of the DUT, if available."""
        return self._backend.version
