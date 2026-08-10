from __future__ import annotations

import sys
from enum import Enum
from typing import Annotated, List, Optional

import grpc
import typer

from boat.v1 import can_tp_pb2

from .output import print_error, print_table

can_tp_app = typer.Typer(help="CAN Transport Protocol (ISO 15765-2) commands.")


class AddressingMode(str, Enum):
    """ISO 15765-2 §10.3 addressing formats. 11-bit vs. 29-bit CAN ID isn't
    a separate mode here -- it's just a property of the numeric value you
    pass as --source-addr/--target-addr (values > 0x7FF get the CAN
    extended-frame flag automatically). Conventional 29-bit "Normal Fixed"
    (0x18DA<TA><SA>/0x18DB<TA><SA>) and "Mixed 29-bit" (0x18CE<TA><SA>/
    0x18CD<TA><SA>) IDs are yours to construct and pass as --source-addr/
    --target-addr like any other CAN ID."""
    normal = "normal"      # no address byte; source/target-addr are the literal CAN IDs
    extended = "extended"  # first payload byte = N_TA (target address)
    mixed = "mixed"        # first payload byte = N_AE (address extension) -- wire-identical to extended


_ADDRESSING_MODE_PROTO = {
    AddressingMode.normal: can_tp_pb2.CANTP_ADDR_NORMAL,
    AddressingMode.extended: can_tp_pb2.CANTP_ADDR_EXTENDED,
    AddressingMode.mixed: can_tp_pb2.CANTP_ADDR_MIXED,
}


def _rpc_error(ex: grpc.RpcError) -> None:
    print_error(f"RPC error [{ex.code().name}]: {ex.details()}")
    sys.exit(1)


_RESULT_LABELS = {
    can_tp_pb2.SEND_RESULT_SINGLE_FRAME: "single frame",
    can_tp_pb2.SEND_RESULT_MULTI_FRAME_INITIATED: "multi-frame initiated",
}


@can_tp_app.command("configure")
def can_tp_configure(
    ctx: typer.Context,
    nsdu_id: Annotated[str, typer.Option("--nsdu-id", help="N-SDU ID (hex or decimal). This is the "
                     "session's identity for send/remove/subscribe.")],
    source_addr: Annotated[str, typer.Option("--source-addr", help="CAN ID of this node (hex or decimal).")],
    target_addr: Annotated[str, typer.Option("--target-addr", help="CAN ID of the peer node (hex or decimal).")],
    block_size: Annotated[int, typer.Option("--bs", help="Block Size to advertise in FC (0=unlimited).")] = 0,
    st_min: Annotated[int, typer.Option("--stmin", help="Separation Time in ms to advertise in FC.")] = 0,
    rx_buffer_size: Annotated[int, typer.Option("--rx-buffer", help="RX reassembly buffer size.")] = 4095,
    can_dlc: Annotated[int, typer.Option("--dlc", help="CAN DLC (8 or 64 for CAN-FD).")] = 8,
    n_bs_ms: Annotated[int, typer.Option("--n-bs-ms", help="ISO 15765-2 N_Bs: max ms to wait for "
                     "Flow Control before aborting an in-progress send (ISO default 1000; "
                     "OBD-II/ISO 15765-4 uses 75).")] = 1000,
    n_cr_ms: Annotated[int, typer.Option("--n-cr-ms", help="ISO 15765-2 N_Cr: max ms to wait for "
                     "the next Consecutive Frame before aborting reassembly (ISO default 1000; "
                     "OBD-II/ISO 15765-4 uses 150).")] = 1000,
    addressing_mode: Annotated[AddressingMode, typer.Option("--addressing-mode", help="ISO "
                     "15765-2 addressing format. 'extended' and 'mixed' both use a first-"
                     "payload-byte address (N_TA / N_AE respectively -- same wire format, "
                     "different semantic label) and, combined with --address-byte, let "
                     "multiple connections share one --target-addr.")] = AddressingMode.normal,
    address_byte: Annotated[str, typer.Option("--address-byte", help="N_TA/N_AE byte for this "
                     "connection, hex or decimal (only meaningful with --addressing-mode "
                     "extended/mixed). 0 = derive from --target-addr's low byte (default, "
                     "matches historical behavior); set explicitly to let multiple connections "
                     "share one --target-addr, disambiguated by this byte.")] = "0",
    brs: Annotated[bool, typer.Option("--brs", help="CAN FD Bit Rate Switch for this connection's "
                     "frames -- only meaningful with --dlc 64; not on by default, since not "
                     "every CAN FD bus has a distinct data-phase bit rate configured.")] = False,
    pad_byte: Annotated[str, typer.Option("--pad-byte", help="Fill byte for unused trailing data "
                     "bytes on every emitted frame, hex or decimal (ISO/AUTOSAR default "
                     "0xCC).")] = "0xCC",
    iface: Annotated[str, typer.Option("--iface", help="Which loaded CanTp instance to target "
                     "(one per CAN interface). Only needed if more than one is loaded -- "
                     "omit it while there's exactly one.")] = "",
) -> None:
    """Configure (or edit) an N-SDU connection for ISO 15765-2.

    Both --source-addr and --target-addr are required -- there is no
    implicit fallback to --nsdu-id. For a single-ID session (one CAN ID
    used for both directions), pass the same value for both explicitly.
    Re-running this for an already-configured --nsdu-id overwrites its
    parameters in place; this is also how you edit a running session --
    refused with an error if a transfer is currently in flight (retry once
    it settles, or `boat can-tp remove` first).

    Talks to the live CanTp plugin instance running inside the gateway
    (loaded via BOAT_NODE_PLUGINS). Afterwards, use `boat can-tp send`
    with just --nsdu-id and --data -- no addresses needed there.

    \b
    Examples:
      # Dual-ID session (tester sending to ECU)
      boat can-tp configure --nsdu-id 0x7E0 --source-addr 0x7E0 --target-addr 0x7E8 --bs 0 --stmin 0

      # Single-ID session (same CAN ID both directions) -- pass it explicitly for both
      boat can-tp configure --nsdu-id 0x123 --source-addr 0x123 --target-addr 0x123

      # Extended addressing
      boat can-tp configure --nsdu-id 0x7E0 --source-addr 0x7E0 --target-addr 0x7E8 --addressing-mode extended

      # Two connections sharing one target_addr, disambiguated by address byte
      boat can-tp configure --nsdu-id 1 --source-addr 0x7E0 --target-addr 0x7E8 --addressing-mode mixed --address-byte 0x01
      boat can-tp configure --nsdu-id 2 --source-addr 0x7E0 --target-addr 0x7E8 --addressing-mode mixed --address-byte 0x02

      # 29-bit "Normal Fixed" addressing -- construct the 0x18DAxxyy ID yourself
      boat can-tp configure --nsdu-id 1 --source-addr 0x18DAF110 --target-addr 0x18DA10F1

      # With two CanTp instances loaded (vcan0 + vcan1), pick one:
      boat can-tp configure --nsdu-id 0x7E0 --source-addr 0x7E0 --target-addr 0x7E8 --iface vcan1
    """
    resolved_id = int(nsdu_id, 0)
    resolved_source = int(source_addr, 0)
    resolved_target = int(target_addr, 0)
    resolved_address_byte = int(address_byte, 0)
    resolved_pad_byte = int(pad_byte, 0)

    config = can_tp_pb2.CanTpConfig(
        nsdu_id=resolved_id,
        source_addr=resolved_source,
        target_addr=resolved_target,
        rx_buffer_size=rx_buffer_size,
        block_size=block_size,
        st_min=st_min,
        can_dlc=can_dlc,
        n_bs_ms=n_bs_ms,
        n_cr_ms=n_cr_ms,
        addressing_mode=_ADDRESSING_MODE_PROTO[addressing_mode],
        address_byte=resolved_address_byte,
        brs=brs,
        pad_byte=resolved_pad_byte,
    )

    try:
        resp = ctx.obj["client"].can_tp.Configure(
            can_tp_pb2.ConfigureRequest(config=config, iface=iface))
    except grpc.RpcError as ex:
        _rpc_error(ex)
        return

    print_table(
        ["nsdu_id", "source_addr", "target_addr", "bs", "stmin", "dlc", "n_bs_ms", "n_cr_ms",
         "addr_mode", "address_byte", "brs", "pad_byte", "iface"],
        [[f"0x{resolved_id:X}", f"0x{resolved_source:X}", f"0x{resolved_target:X}",
          block_size, st_min, can_dlc, n_bs_ms, n_cr_ms, addressing_mode.value,
          f"0x{resolved_address_byte:02X}" if resolved_address_byte else "(derived)", brs,
          f"0x{resolved_pad_byte:02X}", resp.iface]],
        ctx.obj.get("json_mode", False),
    )


@can_tp_app.command("send")
def can_tp_send(
    ctx: typer.Context,
    nsdu_id: Annotated[str, typer.Option("--nsdu-id", help="N-SDU ID of an already-configured "
                     "connection (hex or decimal) -- see `boat can-tp configure`.")],
    data: Annotated[str, typer.Option("--data", help="Hex payload (large payloads will be segmented).")],
    iface: Annotated[str, typer.Option("--iface", help="Which loaded CanTp instance to target "
                     "(one per CAN interface). Only needed if more than one is loaded -- "
                     "omit it while there's exactly one.")] = "",
) -> None:
    """Send a PDU via ISO 15765-2 (CanTp) on an already-configured connection.

    No addressing is passed here -- it comes from the connection's
    `boat can-tp configure` call, looked up by --nsdu-id alone. Payloads
    that fit in a single CAN frame are sent directly as a Single Frame;
    larger payloads are segmented into a First Frame followed by
    Consecutive Frames, paced by the gateway plugin's flow-control state
    machine -- both cases are handled by this one command.

    \b
    Example:
      boat can-tp configure --nsdu-id 0x7E0 --source-addr 0x7E0 --target-addr 0x7E8
      boat can-tp send --nsdu-id 0x7E0 --data 0123456789ABCDEF...
    """
    resolved_id = int(nsdu_id, 0)
    payload = bytes.fromhex(data.replace(":", "").replace(" ", ""))

    try:
        send_resp = ctx.obj["client"].can_tp.Send(
            can_tp_pb2.SendRequest(nsdu_id=resolved_id, data=payload, iface=iface))
    except grpc.RpcError as ex:
        _rpc_error(ex)
        return

    print_table(
        ["nsdu_id", "len", "result"],
        [[f"0x{resolved_id:X}", len(payload), _RESULT_LABELS.get(send_resp.result, "unknown")]],
        ctx.obj.get("json_mode", False),
    )


@can_tp_app.command("remove")
def can_tp_remove(
    ctx: typer.Context,
    nsdu_id: Annotated[str, typer.Option("--nsdu-id", help="N-SDU ID to remove (hex or decimal).")],
    iface: Annotated[str, typer.Option("--iface", help="Which loaded CanTp instance to target. "
                     "Only needed if more than one is loaded.")] = "",
) -> None:
    """Delete a configured N-SDU connection.

    Fails with FAILED_PRECONDITION if a multi-frame transfer on this
    nsdu_id is still in progress -- wait for it to settle and retry rather
    than forcing it.

    \b
    Example:
      boat can-tp remove --nsdu-id 0x7E0
    """
    resolved_id = int(nsdu_id, 0)
    try:
        resp = ctx.obj["client"].can_tp.RemoveSession(
            can_tp_pb2.RemoveSessionRequest(nsdu_id=resolved_id, iface=iface))
    except grpc.RpcError as ex:
        _rpc_error(ex)
        return

    print_table(
        ["nsdu_id", "removed"],
        [[f"0x{resolved_id:X}", resp.ok]],
        ctx.obj.get("json_mode", False),
    )


@can_tp_app.command("subscribe")
def can_tp_subscribe(
    ctx: typer.Context,
    nsdu_ids: Annotated[Optional[List[str]], typer.Option("--nsdu-id", help="N-SDU ID to subscribe "
                     "(repeatable, default: all sessions on the targeted instance(s)).")] = None,
    iface: Annotated[str, typer.Option("--iface", help="Scope to one loaded CanTp instance. "
                     "Omit to stream across every loaded instance.")] = "",
    count: Annotated[int, typer.Option("--count", help="Stop after N events (0 = unlimited).")] = 0,
) -> None:
    """Stream decoded RX payloads (completed Single Frames, or fully
    reassembled multi-frame transfers) from configured CanTp sessions.

    \b
    Examples:
      boat can-tp subscribe                    # every session, every instance
      boat can-tp subscribe --nsdu-id 0x7E0     # just one session
    """
    resolved_ids = [int(i, 0) for i in (nsdu_ids or [])]
    stream = ctx.obj["client"].can_tp.Subscribe(
        can_tp_pb2.SubscribeRequest(nsdu_ids=resolved_ids, iface=iface))

    received = 0
    try:
        for event in stream:
            print_table(
                ["nsdu_id", "data", "iface", "timestamp_ns"],
                [[f"0x{event.nsdu_id:X}", event.data.hex().upper(), event.iface, event.timestamp_ns]],
                ctx.obj.get("json_mode", False),
            )
            received += 1
            if count > 0 and received >= count:
                break
    except grpc.RpcError as ex:
        _rpc_error(ex)
    finally:
        stream.cancel()


@can_tp_app.command("subscribe-errors")
def can_tp_subscribe_errors(
    ctx: typer.Context,
    nsdu_ids: Annotated[Optional[List[str]], typer.Option("--nsdu-id", help="N-SDU ID to subscribe "
                     "(repeatable, default: all sessions on the targeted instance(s)).")] = None,
    iface: Annotated[str, typer.Option("--iface", help="Scope to one loaded CanTp instance. "
                     "Omit to stream across every loaded instance.")] = "",
    count: Annotated[int, typer.Option("--count", help="Stop after N events (0 = unlimited).")] = 0,
) -> None:
    """Stream N_Result error/abort events (ISO 15765-2's detectable subset:
    N_Bs/N_Cr timeout, wrong CF sequence number, buffer overflow) from
    configured CanTp sessions.

    Fires instead of (not in addition to) `subscribe`'s RX-payload event for
    an attempt that didn't complete -- e.g. a peer that stops sending CFs
    produces one N_TIMEOUT_CR event here, not a payload event over there.

    \b
    Examples:
      boat can-tp subscribe-errors                # every session, every instance
      boat can-tp subscribe-errors --nsdu-id 0x7E0 # just one session
    """
    resolved_ids = [int(i, 0) for i in (nsdu_ids or [])]
    stream = ctx.obj["client"].can_tp.SubscribeErrors(
        can_tp_pb2.SubscribeRequest(nsdu_ids=resolved_ids, iface=iface))

    received = 0
    try:
        for event in stream:
            print_table(
                ["nsdu_id", "result", "message", "iface", "timestamp_ns"],
                [[f"0x{event.nsdu_id:X}", can_tp_pb2.CanTpResult.Name(event.result)[len("CANTP_N_"):],
                  event.message, event.iface, event.timestamp_ns]],
                ctx.obj.get("json_mode", False),
            )
            received += 1
            if count > 0 and received >= count:
                break
    except grpc.RpcError as ex:
        _rpc_error(ex)
    finally:
        stream.cancel()


@can_tp_app.command("list-sessions")
def can_tp_list_sessions(
    ctx: typer.Context,
    iface: Annotated[str, typer.Option("--iface", help="Scope to one loaded CanTp instance. "
                     "Omit to list sessions across every loaded instance (each tagged with "
                     "its iface).")] = "",
) -> None:
    """List currently-configured N-SDU connections (ISO 15765-2 sessions).

    \b
    Examples:
      boat can-tp list-sessions               # every loaded instance
      boat can-tp list-sessions --iface vcan0  # just one
    """
    try:
        resp = ctx.obj["client"].can_tp.ListSessions(can_tp_pb2.ListSessionsRequest(iface=iface))
    except grpc.RpcError as ex:
        _rpc_error(ex)
        return

    json_mode = ctx.obj.get("json_mode", False)
    columns = ["iface", "nsdu_id", "source_addr", "target_addr", "bs", "stmin", "dlc", "addr_mode"]
    rows = [
        [s.iface, f"0x{s.nsdu_id:X}", f"0x{s.source_addr:X}", f"0x{s.target_addr:X}",
         s.block_size, s.st_min, s.can_dlc, can_tp_pb2.CanTpAddressingMode.Name(s.addressing_mode)[len("CANTP_ADDR_"):].lower()]
        for s in resp.sessions
    ]
    # n_bs_ms/n_cr_ms/brs/pad_byte/address_byte ride along in --json (no
    # width limit there) but are left out of the plain table -- this table
    # already sits at the edge of Rich's default 80-col non-tty fallback,
    # and more columns push existing ones into "…"-truncation.
    if json_mode:
        columns += ["n_bs_ms", "n_cr_ms", "brs", "pad_byte", "address_byte"]
        for row, s in zip(rows, resp.sessions):
            row += [s.n_bs_ms, s.n_cr_ms, s.brs, f"0x{s.pad_byte:02X}", f"0x{s.address_byte:02X}"]
    columns += ["rx_state", "tx_state"]
    for row, s in zip(rows, resp.sessions):
        row += [s.rx_state, s.tx_state]
    print_table(columns, rows, json_mode)
