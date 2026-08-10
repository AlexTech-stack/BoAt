"""Python interface for CAN Transport Protocol (ISO 15765-2).

This is a high-level wrapper around the CanTpService gRPC API, which
delegates to the live CanTp plugin instance running inside the gateway
process (loaded via BOAT_NODE_PLUGINS). Unlike the plugin's raw C ABI
(boat/can_tp.h), this always talks to the actual running gateway instance --
there is no local/offline mode.

This module provides:

- CanTpHandle(client_or_address): connect to a gateway's CanTpService
- configure(nsdu_id, source_addr, target_addr, ...): configure (or edit) an
  N-SDU connection
- send(nsdu_id, data): send a PDU through CanTp segmentation, by nsdu_id only
- remove(nsdu_id): delete a configured connection
- subscribe(nsdu_ids=None): stream decoded RX payloads
- subscribe_errors(nsdu_ids=None): stream N_Result error/abort events

For the CLI, use::
    boat can-tp configure --nsdu-id 0x7E0 --source-addr 0x7E0 --target-addr 0x7E8
    boat can-tp send --nsdu-id 0x7E0 --data 0123456789ABCDEF...
"""

from __future__ import annotations

from typing import Iterable, Optional, Union

from boat.client import BoAtClient
from boat.v1 import can_tp_pb2


class CanTpHandle:
    """Handle to a gateway's live CanTp plugin instance, via CanTpService.

    Args:
        client_or_address: an existing BoAtClient, or a "host:port" address
            to open a new one (defaults to the gateway's default address).

    RPC failures (e.g. the CanTp plugin isn't loaded, or the request is
    invalid) raise grpc.RpcError -- callers that want the CLI's friendlier
    error messages should catch it themselves.
    """

    def __init__(self, client_or_address: Union[BoAtClient, str] = "localhost:50051") -> None:
        if isinstance(client_or_address, BoAtClient):
            self._client = client_or_address
        else:
            self._client = BoAtClient(address=client_or_address)

    def configure(self, nsdu_id: int, source_addr: int, target_addr: int,
                  iface: str = "", **kwargs) -> bool:
        """Configure (or edit) an N-SDU connection.

        Re-configuring an already-configured nsdu_id overwrites its
        parameters in place -- this is also how you edit a running session.

        Args:
            nsdu_id: N-SDU identifier -- the session's identity for send(),
                remove(), and subscribe().
            source_addr: CAN ID of this node. Required, non-zero. For a
                single-ID session (one CAN ID for both directions), pass the
                same value as target_addr -- there is no implicit fallback.
            target_addr: CAN ID of the peer node. Required, non-zero.
            iface: which loaded CanTp instance to target (one per CAN
                interface). Only needed if more than one is loaded --
                leave empty while there's exactly one; the RPC fails with
                a clear "ambiguous, specify iface" error otherwise.
            **kwargs: Override remaining CanTpConfig fields (rx_buffer_size,
                      block_size, st_min, can_dlc, n_bs_ms, n_cr_ms, brs,
                      pad_byte, addressing_mode, address_byte).
                      addressing_mode is a can_tp_pb2.CanTpAddressingMode
                      value (CANTP_ADDR_NORMAL/_EXTENDED/_MIXED); the older
                      extended_addressing=True/False is still accepted as a
                      shorthand for CANTP_ADDR_NORMAL/_EXTENDED but only
                      takes effect when addressing_mode is left unset.
                      address_byte is this connection's N_TA/N_AE (0 =
                      derive from target_addr's low byte); set it
                      explicitly to let multiple connections share one
                      target_addr, disambiguated by this byte.

        Returns:
            True if configured successfully.

        Raises:
            grpc.RpcError: FAILED_PRECONDITION if nsdu_id already exists and
                has an active transfer in progress (retry once it settles,
                or remove() first), or if target_addr is already used by a
                different nsdu_id on this instance in a way that can't be
                told apart on RX -- sharing a target_addr is only allowed
                when every connection using it has addressing_mode
                EXTENDED/MIXED with a distinct address_byte.
        """
        config = can_tp_pb2.CanTpConfig(
            nsdu_id=nsdu_id,
            source_addr=source_addr,
            target_addr=target_addr,
            rx_buffer_size=kwargs.get("rx_buffer_size", 4095),
            block_size=kwargs.get("block_size", 0),
            st_min=kwargs.get("st_min", 0),
            can_dlc=kwargs.get("can_dlc", 8),
            extended_addressing=kwargs.get("extended_addressing", False),
            n_bs_ms=kwargs.get("n_bs_ms", 1000),
            n_cr_ms=kwargs.get("n_cr_ms", 1000),
            brs=kwargs.get("brs", False),
            pad_byte=kwargs.get("pad_byte", 0xCC),
            addressing_mode=kwargs.get("addressing_mode", can_tp_pb2.CANTP_ADDR_NORMAL),
            address_byte=kwargs.get("address_byte", 0),
        )
        resp = self._client.can_tp.Configure(
            can_tp_pb2.ConfigureRequest(config=config, iface=iface))
        return resp.ok

    def send(self, nsdu_id: int, data: bytes, iface: str = "") -> bool:
        """Send a PDU through CanTp segmentation, to an already-configured session.

        Small payloads are sent as a single CAN frame directly; larger
        payloads are segmented into First Frame + Consecutive Frames, paced
        by the gateway plugin's internal TX thread. No addressing is passed
        here -- it comes from the connection's configure() call, identified
        by nsdu_id alone.

        Args:
            nsdu_id: N-SDU identifier of an already-configured connection.
            data: PDU payload bytes.
            iface: which loaded CanTp instance to target -- see configure().

        Returns:
            True if the send was accepted (either sent immediately as a
            single frame, or a multi-frame transfer was initiated).
        """
        resp = self._client.can_tp.Send(
            can_tp_pb2.SendRequest(nsdu_id=nsdu_id, data=data, iface=iface))
        return resp.result in (
            can_tp_pb2.SEND_RESULT_SINGLE_FRAME,
            can_tp_pb2.SEND_RESULT_MULTI_FRAME_INITIATED,
        )

    def remove(self, nsdu_id: int, iface: str = "") -> bool:
        """Delete a configured N-SDU connection.

        Args:
            nsdu_id: N-SDU identifier to remove.
            iface: which loaded CanTp instance to target -- see configure().

        Returns:
            True if removed. Raises grpc.RpcError (FAILED_PRECONDITION) if
            a multi-frame transfer is still in progress -- wait for it to
            settle and retry rather than forcing it.
        """
        resp = self._client.can_tp.RemoveSession(
            can_tp_pb2.RemoveSessionRequest(nsdu_id=nsdu_id, iface=iface))
        return resp.ok

    def subscribe(self, nsdu_ids: Optional[Iterable[int]] = None, iface: str = ""):
        """Stream decoded RX payloads (completed Single Frames, or fully
        reassembled multi-frame transfers).

        Args:
            nsdu_ids: which sessions to stream; None/empty streams every
                session on the targeted instance(s).
            iface: scope to one loaded instance; "" streams across every
                loaded instance, each event tagged with its iface.

        Returns:
            A gRPC stream of CanTpRxEvent protobuf messages (iface, nsdu_id,
            data, timestamp_ns). Iterate it directly; call .cancel() when done.
        """
        return self._client.can_tp.Subscribe(can_tp_pb2.SubscribeRequest(
            nsdu_ids=list(nsdu_ids or []), iface=iface))

    def subscribe_errors(self, nsdu_ids: Optional[Iterable[int]] = None, iface: str = ""):
        """Stream N_Result error/abort events (ISO 15765-2's detectable
        subset: N_Bs/N_Cr timeout, wrong CF sequence number, buffer
        overflow). Fires instead of (not in addition to) subscribe()'s
        RX-payload event for an attempt that didn't complete.

        Args:
            nsdu_ids: which sessions to stream; None/empty streams every
                session on the targeted instance(s).
            iface: scope to one loaded instance; "" streams across every
                loaded instance, each event tagged with its iface.

        Returns:
            A gRPC stream of CanTpErrorEvent protobuf messages (iface,
            nsdu_id, result, message, timestamp_ns). Iterate it directly;
            call .cancel() when done.
        """
        return self._client.can_tp.SubscribeErrors(can_tp_pb2.SubscribeRequest(
            nsdu_ids=list(nsdu_ids or []), iface=iface))

    def list_sessions(self, iface: str = "") -> list:
        """List currently-configured N-SDU connections.

        Args:
            iface: scope to one loaded instance; "" lists across every
                loaded instance, each session tagged with its iface.

        Returns:
            A list of CanTpSession protobuf messages (nsdu_id, source_addr,
            target_addr, rx_state, tx_state, etc.).
        """
        resp = self._client.can_tp.ListSessions(can_tp_pb2.ListSessionsRequest(iface=iface))
        return list(resp.sessions)
