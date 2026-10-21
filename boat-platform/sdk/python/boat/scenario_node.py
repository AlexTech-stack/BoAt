# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

"""ScenarioNode — manage stored scenario definitions.

Example::

    from boat.scenario_node import ScenarioNode

    scenarios = ScenarioNode()
    scenarios.create("demo", "Demo", '{"id":"demo","name":"Demo", ...}')
    for scenario in scenarios.list():
        print(scenario.scenario_id, scenario.name)
    scenarios.close()
"""

from __future__ import annotations

from typing import Union

from boat.client import BoAtClient
from boat.v1 import common_pb2, scenario_pb2


class ScenarioNode:
    """Ergonomic wrapper around ScenarioService.

    Args:
        client_or_address: an existing BoAtClient (shared, not closed by this
            wrapper), or a "host:port" address. Leave unset to resolve via
            BOAT_HOST, then "localhost:50051".
    """

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

    def create(self, scenario_id: str, name: str, content: str) -> scenario_pb2.Scenario:
        """Store a scenario definition and return it."""
        response = self._client.scenario.CreateScenario(
            scenario_pb2.CreateScenarioRequest(
                scenario=scenario_pb2.Scenario(
                    scenario_id=scenario_id,
                    name=name,
                    content=content,
                )
            )
        )
        return response.scenario

    def get(self, scenario_id: str) -> scenario_pb2.Scenario:
        """Fetch a scenario by id."""
        return self._client.scenario.GetScenario(
            scenario_pb2.GetScenarioRequest(scenario_id=scenario_id)
        ).scenario

    def list_page(
        self,
        page_size: int = 0,
        page_token: str = "",
    ) -> scenario_pb2.ListScenariosResponse:
        """Return one page of scenarios, including its next-page token."""
        return self._client.scenario.ListScenarios(
            scenario_pb2.ListScenariosRequest(
                page=common_pb2.PaginationRequest(
                    page_size=page_size,
                    page_token=page_token,
                )
            )
        )

    def list(self, page_size: int = 0, page_token: str = "") -> list[scenario_pb2.Scenario]:
        """Return scenarios from one page."""
        return list(self.list_page(page_size=page_size, page_token=page_token).scenarios)

    def validate(self, content: str) -> scenario_pb2.ValidateScenarioResponse:
        """Validate scenario JSON without storing it."""
        return self._client.scenario.ValidateScenario(
            scenario_pb2.ValidateScenarioRequest(content=content)
        )

    def delete(self, scenario_id: str) -> bool:
        """Delete a scenario, returning whether the gateway deleted it."""
        return bool(self._client.scenario.DeleteScenario(
            scenario_pb2.DeleteScenarioRequest(scenario_id=scenario_id)
        ).deleted)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
