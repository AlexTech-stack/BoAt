# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from collections.abc import Mapping


class ScenarioBuilder:
    """Builds the scenario JSON that src/core/scenario/scenario_loader.cpp reads.

    build() emits exactly the loader's key set. There is deliberately no
    tick-rate parameter: the gateway has no per-scenario tick rate -- the tick
    comes from BOAT_NODE_TICK_MS / BOAT_NODE_TICK_US -- and the tick_rate_hz
    argument this class used to accept was stored and never emitted, so
    callers passing it were configuring nothing.
    """

    def __init__(
        self,
        name: str = "default_scenario",
        *,
        scenario_id: str | None = None,
        version: str = "1.0.0",
        duration_ticks: int = 1000,
        seed: int = 0,
    ) -> None:
        self.id: str = scenario_id or name
        self.name: str = name
        self.version: str = version
        self.duration_ticks: int = duration_ticks
        self.seed: int = seed
        self.plugins: list[dict] = []
        self.signals: list[dict] = []
        self.faults: list[dict] = []

    def add_plugin(self, path: str, config: Mapping[str, object]) -> "ScenarioBuilder":
        # No `name`: the loader's plugin entry is {so_path, config_json} only,
        # and the name this used to take was discarded immediately.
        self.plugins.append(
            {
                "so_path": path,
                "config_json": json.dumps(config),
            }
        )
        return self

    def add_signal(
        self,
        signal_id: str,
        initial_value: float | int | str | bool,
        *,
        name: str | None = None,
        signal_type: str | None = None,
        unit: str = "",
    ) -> "ScenarioBuilder":
        inferred_type = signal_type
        if inferred_type is None:
            if isinstance(initial_value, bool):
                inferred_type = "bool"
            elif isinstance(initial_value, int):
                inferred_type = "int"
            elif isinstance(initial_value, float):
                inferred_type = "double"
            else:
                inferred_type = "string"
        self.signals.append(
            {
                "id": signal_id,
                "name": name or signal_id,
                "type": inferred_type,
                "unit": unit,
            }
        )
        return self

    def add_fault(
        self,
        signal_id: str,
        fault_type: str,
        at_tick: int,
        *,
        magnitude: float = 1.0,
    ) -> "ScenarioBuilder":
        self.faults.append(
            {
                "signal_id": signal_id,
                "fault_type": fault_type,
                "tick": at_tick,
                "magnitude": magnitude,
            }
        )
        return self

    def build(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "duration_ticks": self.duration_ticks,
            "seed": self.seed,
            "plugins": self.plugins,
            "signals": self.signals,
            "faults": self.faults,
        }

    def to_json(self) -> str:
        return json.dumps(self.build())
