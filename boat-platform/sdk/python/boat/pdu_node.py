"""Base class for Python PDU nodes.

A PDU node connects to the BoAt gateway, subscribes to PDU frames, and can
send PDUs or configure routing rules.  Subclass PduNode, override on_pdu(),
then call run() or run_background().

PDUs are AUTOSAR-style protocol data units routed over CAN or Ethernet.
On the Ethernet transport the gateway frames PDUs as:
  [4 bytes PDU ID big-endian] + payload (EtherType defaults to 0x88B5).

Example::

    class MyNode(PduNode):
        def on_pdu(self, pdu) -> None:
            print(f"PDU 0x{pdu.pdu_id:08X}  payload={pdu.payload.hex(':')}")

    node = MyNode(pdu_ids=[0x00AA0001, 0x00AA0002])
    node.run()

Sending a PDU::

    node = PduNode()
    node.send(pdu_id=0x00AA0001, payload=bytes([0x01, 0x02, 0x03]))

Configuring a route::

    from boat.v1 import pdu_pb2
    node.configure_route(
        pdu_id=0x00AA0001,
        transport=pdu_pb2.PDU_TRANSPORT_ETHERNET,
        iface="veth0",
        ethertype=0x88B5,
    )
"""

from __future__ import annotations

import threading
from typing import Any, List

import grpc

from boat.client import BoAtClient
from boat.v1 import pdu_pb2


class PduNode:
    """Abstract base for Python PDU processing nodes.

    Args:
        address:  Gateway gRPC address (host:port).
        pdu_ids:  PDU IDs to subscribe to.  Empty list = subscribe to all PDUs.
    """

    def __init__(
        self,
        address: str = "localhost:50051",
        pdu_ids: List[int] | None = None,
    ) -> None:
        self._client = BoAtClient(address)
        self._pdu_ids: List[int] = pdu_ids or []
        self._stream: Any = None
        self._stop_event = threading.Event()

    # ------------------------------------------------------------------
    # Override in subclass
    # ------------------------------------------------------------------

    def on_pdu(self, pdu: Any) -> None:  # noqa: B027
        """Called for every received PduFrame.  Override in subclass."""

    # ------------------------------------------------------------------
    # Route management
    # ------------------------------------------------------------------

    def configure_route(
        self,
        pdu_id: int,
        transport: int,
        iface: str,
        can_id: int = 0,
        ethertype: int = 0x88B5,
        vlan_id: int = 0,
    ) -> bool:
        """Configure a PDU routing rule in the gateway.

        Args:
            pdu_id:    32-bit PDU identifier.
            transport: ``pdu_pb2.PDU_TRANSPORT_CAN`` or
                       ``pdu_pb2.PDU_TRANSPORT_ETHERNET``.
            iface:     Interface name (e.g. ``"vcan0"`` or ``"veth0"``).
            can_id:    CAN frame ID override (0 = use pdu_id).
            ethertype: EtherType for Ethernet PDUs (default 0x88B5).
            vlan_id:   VLAN ID (0 = untagged).

        Returns:
            True if the gateway accepted the route.
        """
        route = pdu_pb2.PduRoute(
            pdu_id=pdu_id,
            transport=transport,
            iface=iface,
            can_id=can_id,
            ethertype=ethertype,
            vlan_id=vlan_id,
        )
        try:
            resp = self._client.pdu.ConfigureRoute(
                pdu_pb2.ConfigureRouteRequest(route=route)
            )
            return bool(resp.ok)
        except grpc.RpcError:
            return False

    def list_routes(self) -> list:
        """Return all configured routes from the gateway."""
        try:
            resp = self._client.pdu.ListRoutes(pdu_pb2.ListRoutesRequest())
            return list(resp.routes)
        except grpc.RpcError:
            return []

    # ------------------------------------------------------------------
    # Send helpers
    # ------------------------------------------------------------------

    def send(self, pdu_id: int, payload: bytes) -> bool:
        """Send a PDU via the gateway.

        Args:
            pdu_id:  32-bit PDU identifier (must have a configured route).
            payload: PDU payload bytes.

        Returns:
            True if the gateway accepted the PDU.
        """
        pdu = pdu_pb2.PduFrame(pdu_id=pdu_id, payload=bytes(payload))
        try:
            resp = self._client.pdu.SendPdu(pdu_pb2.SendPduRequest(pdu=pdu))
            return bool(resp.accepted)
        except grpc.RpcError:
            return False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Subscribe to PDU frames and block until stop() is called."""
        self._stop_event.clear()
        self._stream = self._client.pdu.SubscribePdus(
            pdu_pb2.SubscribePdusRequest(pdu_ids=self._pdu_ids)
        )
        try:
            for pdu in self._stream:
                if self._stop_event.is_set():
                    break
                self.on_pdu(pdu)
        except grpc.RpcError:
            pass
        finally:
            self._stream.cancel()
            self._client.close()

    def run_background(self) -> threading.Thread:
        """Start the node in a daemon thread.  Returns the thread."""
        thread = threading.Thread(target=self.run, daemon=True)
        thread.start()
        return thread

    def stop(self) -> None:
        """Signal the node to stop after the current PDU."""
        self._stop_event.set()
        if self._stream is not None:
            self._stream.cancel()
