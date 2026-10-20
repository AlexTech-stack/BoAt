# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

"""FaultNode — schedule and inspect deterministic signal faults.

The gateway's FaultInjector supports five modes: STUCK, NOISE, DROPOUT,
INVERT and DELAY. This wrapper speaks the FaultService gRPC API, which is
the only supported way to reach the injector from outside the gateway
process.

Example::

    from boat.fault_node import FaultNode

    faults = FaultNode()
    faults.inject_by_name("vehicle.speed", "stuck", tick=100, end_tick=200,
                          magnitude=42.0)
    for fault in faults.list_faults():
        print(fault.fault_id, fault.target, fault.type)
    faults.close()
"""

from __future__ import annotations

from typing import Union

from boat.client import BoAtClient
from boat.v1 import common_pb2, fault_pb2

# Human/CLI names mapped to the wire enum. `corruption` is the historical
# proto name for NOISE; accept both so scripts do not have to know which one
# the proto uses.
FAULT_TYPES = {
    "stuck": fault_pb2.FAULT_TYPE_SIGNAL_STUCK,
    "noise": fault_pb2.FAULT_TYPE_SIGNAL_CORRUPTION,
    "corruption": fault_pb2.FAULT_TYPE_SIGNAL_CORRUPTION,
    "dropout": fault_pb2.FAULT_TYPE_PACKET_DROP,
    "drop": fault_pb2.FAULT_TYPE_PACKET_DROP,
    "packet-drop": fault_pb2.FAULT_TYPE_PACKET_DROP,
    "invert": fault_pb2.FAULT_TYPE_SIGNAL_INVERT,
    "delay": fault_pb2.FAULT_TYPE_DELAY,
}


class FaultNode:
    """Schedule and list faults through a gateway's FaultService.

    Args:
        client_or_address: an existing BoAtClient, or a "host:port" address
            to open a new one. Leave unset to resolve via BOAT_HOST, then
            "localhost:50051" -- the same order as BoAtClient and the other
            node classes.
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

    def inject(
        self,
        target: str,
        fault_type: int,
        tick: int,
        *,
        end_tick: int = 0,
        magnitude: float = 0.0,
        delay_ticks: int = 0,
        simulation_id: str = "",
    ) -> fault_pb2.FaultEvent:
        """Schedule one fault and return its recorded event.

        The gateway clears scheduled faults when a simulation is created or
        reset, so inject after ``boat sim create`` (before or during the run).

        Args:
            target: Signal name (the gateway hashes it to its signal id).
            fault_type: A ``fault_pb2.FAULT_TYPE_*`` value.
            tick: Start tick. ``end_tick=0`` means a one-tick fault.
            end_tick: Inclusive end tick, or 0 for ``tick`` itself.
            magnitude: Stuck value or noise amplitude, depending on type.
            delay_ticks: Delay length for ``FAULT_TYPE_DELAY``.
            simulation_id: Optional simulation id recorded on the event.

        Raises:
            ValueError: end_tick is before tick.
            RuntimeError: the gateway returned an ErrorDetail.
            grpc.RpcError: transport or service failure.
        """
        if end_tick and end_tick < tick:
            raise ValueError("end_tick must be greater than or equal to tick")
        request = fault_pb2.InjectFaultRequest(
            simulation_id=simulation_id,
            target=target,
            type=fault_type,
            tick=tick,
            end_tick=end_tick,
            magnitude=magnitude,
            delay_ticks=delay_ticks,
        )
        response = self._client.fault.InjectFault(request)
        if response.HasField("error"):
            raise RuntimeError(response.error.message or response.error.code or "fault injection failed")
        return response.fault

    def inject_by_name(
        self,
        target: str,
        fault_type: str,
        tick: int,
        **kwargs,
    ) -> fault_pb2.FaultEvent:
        """Inject using a CLI-style name: stuck, noise, dropout, invert or delay."""
        try:
            resolved = FAULT_TYPES[fault_type.strip().lower()]
        except KeyError as exc:
            valid = ", ".join(sorted(FAULT_TYPES))
            raise ValueError(f"unknown fault type {fault_type!r}; valid: {valid}") from exc
        return self.inject(target, resolved, tick, **kwargs)

    def list_faults_page(
        self,
        simulation_id: str = "",
        page_size: int = 0,
        page_token: str = "",
    ) -> fault_pb2.ListFaultsResponse:
        """Return one page of injected faults, including its next-page token."""
        request = fault_pb2.ListFaultsRequest(
            simulation_id=simulation_id,
            page=common_pb2.PaginationRequest(
                page_size=page_size,
                page_token=page_token,
            ),
        )
        return self._client.fault.ListFaults(request)

    def list_faults(
        self,
        simulation_id: str = "",
        page_size: int = 0,
        page_token: str = "",
    ) -> list[fault_pb2.FaultEvent]:
        """Return injected faults, optionally scoped to one simulation."""
        return list(self.list_faults_page(
            simulation_id=simulation_id,
            page_size=page_size,
            page_token=page_token,
        ).faults)

    def close(self) -> None:
        """Close the channel if this wrapper opened it."""
        if self._owns_client:
            self._client.close()
