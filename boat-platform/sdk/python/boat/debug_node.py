# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

"""DebugNode — effective configuration and live RPC audit events."""

from __future__ import annotations

from typing import Iterator, Union

from boat.client import BoAtClient
from boat.v1 import debug_pb2


class DebugNode:
    """Ergonomic wrapper around DebugService."""

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

    def effective_config(self) -> str:
        """Return the gateway's resolved effective-config JSON document."""
        return self._client.debug.GetEffectiveConfig(
            debug_pb2.GetEffectiveConfigRequest()
        ).config_json

    def stream_events(self, method_filter: str = "") -> Iterator[debug_pb2.RpcEvent]:
        """Yield RPC audit events, optionally filtered by method-path substring."""
        return self._client.debug.StreamEvents(
            debug_pb2.StreamRpcEventsRequest(method_filter=method_filter)
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
