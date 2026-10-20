# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

"""TraceNode — read recorded trace events and write step markers."""

from __future__ import annotations

from typing import Iterator, Optional, Union

from boat.client import BoAtClient
from boat.v1 import common_pb2, trace_pb2


class TraceNode:
    """Ergonomic wrapper around TraceService."""

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

    def get(self, trace_id: str) -> list[trace_pb2.TraceEvent]:
        """Return the recorded events for one trace id."""
        return list(self._client.trace.GetTrace(
            trace_pb2.GetTraceRequest(trace_id=trace_id)
        ).events)

    def list_page(
        self,
        page_size: int = 0,
        page_token: str = "",
    ) -> trace_pb2.TraceResponse:
        return self._client.trace.ListTraces(
            trace_pb2.ListTracesRequest(
                page=common_pb2.PaginationRequest(
                    page_size=page_size,
                    page_token=page_token,
                )
            )
        )

    def list(self, page_size: int = 0, page_token: str = "") -> list[trace_pb2.TraceEvent]:
        return list(self.list_page(page_size=page_size, page_token=page_token).events)

    def stream(self, trace_id: str) -> Iterator[trace_pb2.TraceEvent]:
        return self._client.trace.StreamTrace(
            trace_pb2.StreamTraceRequest(trace_id=trace_id)
        )

    def mark_step(
        self,
        trace_id: str,
        step_id: int,
        step_name: str,
        sim_tick: int = 0,
        metadata: Optional[dict[str, str]] = None,
    ) -> int:
        """Record a step marker and return its wall-clock timestamp in ns."""
        request = trace_pb2.MarkStepRequest(
            trace_id=trace_id,
            step_id=step_id,
            step_name=step_name,
            sim_tick=sim_tick,
        )
        if metadata:
            request.metadata.update(metadata)
        return self._client.trace.MarkStep(request).marker_timestamp_ns

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
