#!/usr/bin/env python3
# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

"""Seed-reproducibility probe for TC_Simulation_006/007.

The gateway's determinism guarantee is over *signal events*: the per-tick
PRNG (``DeterminismEngine``) is reseeded from ``(scenario seed, tick)``, and a
scheduled ``noise`` fault draws from it when a signal event is applied by
``SignalRouter``. To observe that reproducibly the probe:

1. uploads a copy of ``example.json`` with the requested seed,
2. creates/starts/steps the simulation to a fixed absolute tick (so where the
   pause happened, which depends on wall-clock scheduling, does not shift the
   sampled ticks),
3. schedules a seeded ``noise`` fault on ``vehicle.speed`` for the sampled
   range,
4. for each tick: steps exactly one tick, then injects a fixed
   ``vehicle.speed`` value and records what the gateway emits.

Two runs with the same seed must produce identical ``(tick, name, value)``
lines; two runs with different seeds must differ.

Requires a running gateway (``BOAT_HOST`` or ``--address``), the CLI/SDK
installed, and ``example.json`` available (default path below).

Usage:
    python3 test/fixtures/scripts/determinism_probe.py \
        --seed 1234 --ticks 32 --out run1.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path

import grpc

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "boat-platform" / "sdk" / "python"))

from boat.client import BoAtClient  # noqa: E402
from boat.fault_node import FaultNode  # noqa: E402
from boat.simulation_node import SimulationNode  # noqa: E402
from boat.v1 import scenario_pb2, signal_pb2  # noqa: E402

SIGNAL = "vehicle.speed"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", default=None,
                        help="gateway address (default: BOAT_HOST, then localhost:50051)")
    parser.add_argument("--scenario-file",
                        default=str(_REPO_ROOT / "boat-platform/config/scenarios/example.json"),
                        help="base scenario JSON (default: boat-platform/config/scenarios/example.json)")
    parser.add_argument("--seed", type=int, required=True, help="scenario seed to apply")
    parser.add_argument("--ticks", type=int, default=32, help="number of ticks to sample")
    parser.add_argument("--target-tick", type=int, default=10_000,
                        help="absolute tick to start sampling at (a fixed value keeps runs comparable)")
    parser.add_argument("--magnitude", type=float, default=1.0, help="noise fault amplitude")
    parser.add_argument("--out", required=True, help="output JSONL of (tick,name,value)")
    return parser


def main() -> int:
    args = build_parser().parse_args()

    client = BoAtClient(address=args.address)
    simulation = SimulationNode(client)
    faults = FaultNode(client)

    content = json.loads(Path(args.scenario_file).read_text(encoding="utf-8"))
    scenario_id = f"determinism-{args.seed}"
    content["id"] = scenario_id
    content["name"] = f"Determinism probe (seed {args.seed})"
    content["seed"] = args.seed
    content.setdefault("plugins", [])
    content.setdefault("signals", [])
    if not any(sig.get("id") == SIGNAL for sig in content["signals"]):
        content["signals"].append(
            {"id": SIGNAL, "name": SIGNAL, "type": "double", "unit": ""}
        )
    # The probe's injected noise is the only randomness source; do not let the
    # base scenario add its own faults to the stream.
    content["faults"] = []

    client.scenario.CreateScenario(
        scenario_pb2.CreateScenarioRequest(
            scenario=scenario_pb2.Scenario(
                scenario_id=scenario_id,
                name=content["name"],
                content=json.dumps(content),
            )
        )
    )

    sim_id = simulation.create(scenario_id).simulation_id
    simulation.start(sim_id)
    simulation.pause(sim_id)

    received: list[tuple[int, str, float]] = []
    done = threading.Event()

    def consume() -> None:
        try:
            for value in client.signal.SubscribeSignals(
                signal_pb2.SubscribeSignalsRequest(simulation_id=sim_id, names=[SIGNAL])
            ):
                if value.name != SIGNAL:
                    continue
                received.append((int(value.tick), value.name, float(value.number_value)))
                if len(received) >= args.ticks:
                    break
        except grpc.RpcError:
            pass
        finally:
            done.set()

    try:
        state = simulation.state(sim_id)
        if state.tick < args.target_tick:
            simulation.step(sim_id, args.target_tick - state.tick)
        start_tick = simulation.state(sim_id).tick
        end_tick = start_tick + args.ticks

        faults.inject_by_name(
            SIGNAL, "noise", start_tick, end_tick=end_tick, magnitude=args.magnitude
        )

        stream_thread = threading.Thread(target=consume, daemon=True)
        stream_thread.start()
        time.sleep(0.2)  # let the subscription register before injecting

        for tick in range(start_tick + 1, end_tick + 1):
            simulation.step(sim_id, 1)
            client.signal.InjectSignal(
                signal_pb2.InjectSignalRequest(
                    simulation_id=sim_id,
                    signal=signal_pb2.SignalValue(
                        name=SIGNAL, tick=tick, number_value=0.0
                    ),
                )
            )

        done.wait(timeout=5.0)
        stream_thread.join(timeout=1.0)
    finally:
        try:
            simulation.stop(sim_id)
        except Exception:
            pass
        simulation.close()
        faults.close()
        client.close()

    by_tick: dict[int, tuple[str, float]] = {}
    for tick, name, value in received:
        by_tick.setdefault(tick, (name, value))
    rows = [
        {"tick": tick, "name": name, "value": value}
        for tick, (name, value) in sorted(by_tick.items())
    ]
    Path(args.out).write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    print(f"wrote {len(rows)} signal values to {args.out}", file=sys.stderr)
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
