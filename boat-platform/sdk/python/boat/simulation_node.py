# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

"""SimulationNode — lifecycle control for the gateway's one live simulation.

The gateway hosts one simulation session at a time; ``simulation_id`` selects
a stored scenario definition, not an independent engine. See
``proto/boat/v1/simulation.proto``.
"""

from __future__ import annotations

from typing import Iterator, Union

from boat.client import BoAtClient
from boat.v1 import common_pb2, simulation_pb2


class SimulationNode:
    """Ergonomic wrapper around SimulationService."""

    def __init__(self, client_or_address: Union[BoAtClient, str, None] = None) -> None:
        if client_or_address is not None and not isinstance(client_or_address, str):
            self._client = client_or_address
            self._owns_client = False
        else:
            self._client = BoAtClient(address=client_or_address)
            self._owns_client = True

    @property
    def client(self) -> BoAtClient:
        return self._client

    def create(self, scenario_id: str) -> simulation_pb2.Simulation:
        return self._client.simulation.CreateSimulation(
            simulation_pb2.CreateSimulationRequest(scenario_id=scenario_id)
        ).simulation

    def start(self, simulation_id: str) -> simulation_pb2.Simulation:
        return self._client.simulation.StartSimulation(
            simulation_pb2.StartSimulationRequest(simulation_id=simulation_id)
        ).simulation

    def pause(self, simulation_id: str) -> simulation_pb2.Simulation:
        return self._client.simulation.PauseSimulation(
            simulation_pb2.PauseSimulationRequest(simulation_id=simulation_id)
        ).simulation

    def step(self, simulation_id: str, ticks: int = 1) -> simulation_pb2.Simulation:
        return self._client.simulation.StepSimulation(
            simulation_pb2.StepSimulationRequest(simulation_id=simulation_id, ticks=ticks)
        ).simulation

    def stop(self, simulation_id: str) -> simulation_pb2.Simulation:
        return self._client.simulation.StopSimulation(
            simulation_pb2.StopSimulationRequest(simulation_id=simulation_id)
        ).simulation

    def reset(self, simulation_id: str) -> simulation_pb2.Simulation:
        return self._client.simulation.ResetSimulation(
            simulation_pb2.ResetSimulationRequest(simulation_id=simulation_id)
        ).simulation

    def state(self, simulation_id: str) -> simulation_pb2.Simulation:
        return self._client.simulation.GetSimulationState(
            simulation_pb2.GetSimulationStateRequest(simulation_id=simulation_id)
        ).simulation

    def list_page(
        self,
        page_size: int = 0,
        page_token: str = "",
    ) -> simulation_pb2.ListSimulationsResponse:
        return self._client.simulation.ListSimulations(
            simulation_pb2.ListSimulationsRequest(
                page=common_pb2.PaginationRequest(
                    page_size=page_size,
                    page_token=page_token,
                )
            )
        )

    def list(self, page_size: int = 0, page_token: str = "") -> list[simulation_pb2.Simulation]:
        return list(self.list_page(page_size=page_size, page_token=page_token).simulations)

    def watch(self, simulation_id: str) -> Iterator[simulation_pb2.Simulation]:
        """Yield state changes (not tick progress) until the stream ends."""
        stream = self._client.simulation.WatchSimulation(
            simulation_pb2.GetSimulationStateRequest(simulation_id=simulation_id)
        )
        for response in stream:
            yield response.simulation

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
