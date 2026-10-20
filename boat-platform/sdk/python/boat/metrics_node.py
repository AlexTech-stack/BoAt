# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

"""MetricsNode — read and stream gateway metrics.

Two RPCs back this wrapper:

- ``GetMetrics`` is a one-shot snapshot of the event store plus gateway
  metrics (CAN bus health, stream drop counters).
- ``StreamMetrics`` pushes points as they change and additionally emits CAN
  health/drop metrics once a second.

The stream is conflated by metric name: a slow consumer sees the latest value
of each series rather than an unbounded backlog. See ``metrics.proto``.

Example::

    from boat.metrics_node import MetricsNode

    metrics = MetricsNode()
    for point in metrics.get():
        print(point.name, point.value)
    metrics.close()
"""

from __future__ import annotations

from typing import Iterator, Union

from boat.client import BoAtClient
from boat.v1 import metrics_pb2


class MetricsNode:
    """Read or stream metrics through a gateway's MetricsService.

    Args:
        client_or_address: an existing BoAtClient, or a "host:port" address
            to open a new one. Leave unset to resolve via BOAT_HOST, then
            "localhost:50051".
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

    def get(self, simulation_id: str = "") -> list[metrics_pb2.MetricPoint]:
        """Return the current metric snapshot."""
        response = self._client.metrics.GetMetrics(
            metrics_pb2.GetMetricsRequest(simulation_id=simulation_id)
        )
        return list(response.points)

    def stream(self) -> Iterator[metrics_pb2.MetricPoint]:
        """Stream gateway-scoped metric points until the caller stops.

        The returned object is the gRPC stream; call ``.cancel()`` to stop it
        early, or iterate it in a ``for`` loop. Unlike :meth:`get`, the live
        stream has no simulation filter: it is the gateway's own metrics and
        CAN/drop counters.
        """
        return self._client.metrics.StreamMetrics(metrics_pb2.StreamMetricsRequest())

    def close(self) -> None:
        """Close the channel if this wrapper opened it."""
        if self._owns_client:
            self._client.close()
