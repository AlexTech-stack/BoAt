"""Python SDK for replaying CAN trace files through the BoAt gateway.

Supported formats: ASC, BLF (via python-can).

Two replay modes are available:

1. **Direct mode** (default): Sends each CAN frame one-by-one via gRPC CanService.
2. **Server-side mode**: Converts the trace to the gateway's internal binary format,
   uploads it via ReplayService.ImportTraceData, then plays back using
   ReplayService.StartReplay + StreamReplay.

Quick example::

    from boat.trace_replay import TraceReplayer

    replayer = TraceReplayer(
        gateway="localhost:50051",
        buses=["vcan0", "vcan1"],   # channel 1 → vcan0, channel 2 → vcan1
        speed=1.0,                   # real-time; >1 faster, <1 slower
    )
    # Direct mode (default):
    replayer.replay("recording.asc")

    # Server-side mode:
    replayer.replay_server_side("recording.asc")
"""
from __future__ import annotations

import struct
import time
from pathlib import Path
from typing import Callable, List, Optional

# CAN FD flags (matches gateway constants)
_CANFD_BRS = 0x01   # bit-rate switch
_CANFD_FDF = 0x04   # FD frame

# Internal trace format magic number
_TRACE_MAGIC = 0xB0A7B0A7


class TraceReplayError(RuntimeError):
    pass


class TraceReplayer:
    """Replay a CAN trace file through the BoAt gateway via gRPC.

    Args:
        gateway:     gRPC address of the BoAt gateway (host:port).
        buses:       Ordered list of CAN interface names.  Channel *N* in the
                     trace file maps to ``buses[N-1]`` (1-based, ASC/BLF
                     convention).  If the channel number exceeds the list
                     length the last entry is used.  Pass an empty list to
                     use ``"vcan0"`` for every frame.
        speed:       Playback speed multiplier.  ``1.0`` = real-time,
                     ``2.0`` = twice as fast, ``0.5`` = half speed.
                     ``0`` means send as fast as possible (no delay).
        simulation_id: Simulation ID forwarded to the gateway (usually ``""``).
        on_frame:    Optional callback ``(index, msg) -> None`` called for
                     every frame just before it is sent.  Useful for progress
                     reporting.
    """

    def __init__(
        self,
        gateway: str = "localhost:50051",
        buses: Optional[List[str]] = None,
        speed: float = 1.0,
        simulation_id: str = "",
        on_frame: Optional[Callable] = None,
        channel_filter: Optional[int] = None,
        id_filter: Optional[set[int]] = None,
    ) -> None:
        self.gateway       = gateway
        self.buses         = buses or []
        self.speed         = speed
        self.simulation_id = simulation_id
        self.on_frame      = on_frame
        self.channel_filter = channel_filter
        self.id_filter     = id_filter or set()
        self._stub         = None
        self._replay_stub  = None

    # ── Public API ─────────────────────────────────────────────────────────────

    def replay(self, path: str | Path, loop: bool = False, server_side: bool = False) -> int:
        """Replay a trace file.

        Args:
            path: Path to an ``.asc`` or ``.blf`` file.
            loop: If *True* replay the file indefinitely until the process is
                  interrupted.
            server_side: If *True*, upload the trace and use the gateway's
                         ReplayService for server-side playback.

        Returns:
            Total number of frames sent (across all loop iterations).

        Raises:
            TraceReplayError: If the file cannot be opened or a gRPC error
            occurs.
        """
        if server_side:
            return self.replay_server_side(path, loop=loop)

        stub = self._get_stub()
        path = Path(path)
        total = 0
        while True:
            total += self._replay_once(stub, path, total == 0)
            if not loop:
                break
        return total

    def replay_server_side(self, path: str | Path, loop: bool = False) -> int:
        """Replay a trace file using the gateway's ReplayService.

        Converts the trace to the internal binary format, uploads it via
        ImportTraceData, then plays back using StartReplay + StreamReplay.

        Args:
            path: Path to an ``.asc`` or ``.blf`` file.
            loop: If *True* replay the file indefinitely.

        Returns:
            Total number of frames replayed.

        Raises:
            TraceReplayError: On conversion, upload, or playback errors.
        """
        from boat.v1 import replay_pb2

        path = Path(path)
        trace_id = path.stem

        binary_data = self._convert_to_binary(path)

        replay_stub = self._get_replay_stub()

        try:
            upload_resp = replay_stub.ImportTraceData(
                replay_pb2.ImportTraceDataRequest(
                    trace_id=trace_id,
                    format=path.suffix.lstrip(".").upper(),
                    data=binary_data,
                )
            )
        except Exception as e:
            raise TraceReplayError(f"ImportTraceData failed: {e}") from e

        if not upload_resp.accepted:
            raise TraceReplayError(f"ImportTraceData rejected: {upload_resp.error.message}")

        speed_mult = 1_000_000.0 if self.speed == 0 else self.speed
        start_resp = replay_stub.StartReplay(
            replay_pb2.StartReplayRequest(
                trace_id=trace_id,
                simulation_id=self.simulation_id,
                speed=replay_pb2.REPLAY_SPEED_ACCELERATED,
                speed_multiplier=speed_mult,
            )
        )

        if not start_resp.accepted:
            raise TraceReplayError(f"StartReplay rejected: {start_resp.error.message}")

        replay_id = start_resp.replay_id
        total = 0
        while True:
            stream = replay_stub.StreamReplay(
                replay_pb2.StreamReplayRequest(replay_id=replay_id)
            )
            pass_total = 0
            try:
                for event in stream:
                    pass_total += 1
                    total += 1
                    if self.on_frame is not None:
                        self.on_frame(pass_total, event)
            except Exception:
                pass
            if not loop:
                break

        return total

    # ── Internals ──────────────────────────────────────────────────────────────

    def _convert_to_binary(self, path: Path) -> bytes:
        """Convert an .asc/.blf file to the gateway's internal binary trace format."""
        reader = self._open_reader(path)
        result = bytearray()
        first_ts: Optional[float] = None

        with reader:
            for msg in reader:
                ch = getattr(msg, "channel", None)
                if self.channel_filter is not None and ch != self.channel_filter:
                    continue
                if self.id_filter and msg.arbitration_id not in self.id_filter:
                    continue

                ts = msg.timestamp
                if first_ts is None:
                    first_ts = ts
                relative_ts = ts - first_ts

                tick = int(relative_ts * 1000)
                wall_time_ns = int(relative_ts * 1_000_000_000)
                payload = bytes(msg.data)
                event_type = msg.arbitration_id

                header = struct.pack(
                    "<IIQqI",
                    _TRACE_MAGIC,
                    event_type,
                    tick,
                    wall_time_ns,
                    len(payload),
                )
                result.extend(header)
                result.extend(payload)

        return bytes(result)

    def _get_replay_stub(self):
        if self._replay_stub is not None:
            return self._replay_stub
        try:
            import grpc
            from boat.v1 import replay_pb2_grpc
        except ImportError as e:
            raise TraceReplayError(f"Cannot import boat gRPC stubs: {e}") from e
        channel = grpc.insecure_channel(self.gateway)
        self._replay_stub = replay_pb2_grpc.ReplayServiceStub(channel)
        return self._replay_stub

    def _get_stub(self):
        if self._stub is not None:
            return self._stub
        try:
            import grpc
            from boat.v1 import can_pb2_grpc
        except ImportError as e:
            raise TraceReplayError(f"Cannot import boat gRPC stubs: {e}") from e
        channel     = grpc.insecure_channel(self.gateway)
        self._stub  = can_pb2_grpc.CanServiceStub(channel)
        return self._stub

    def _iface_for_channel(self, channel: int) -> str:
        """Map a 1-based trace channel number to a bus interface name."""
        if not self.buses:
            return "vcan0"
        idx = max(0, channel - 1)
        return self.buses[min(idx, len(self.buses) - 1)]

    def _open_reader(self, path: Path):
        """Return a python-can log reader appropriate for *path*."""
        try:
            import can
        except ImportError as e:
            raise TraceReplayError(
                "python-can is required for trace replay: "
                "pip install python-can"
            ) from e
        suffix = path.suffix.lower()
        if suffix == ".asc":
            return can.ASCReader(str(path))
        if suffix == ".blf":
            return can.BLFReader(str(path))
        raise TraceReplayError(
            f"Unsupported trace format '{suffix}'. Supported: .asc, .blf"
        )

    def _replay_once(self, stub, path: Path, is_first: bool) -> int:
        """Stream one pass through *path*, return frame count."""
        from boat.v1 import can_pb2

        sent            = 0
        prev_trace_ts: Optional[float] = None
        prev_wall_ts:  Optional[float] = None

        try:
            reader = self._open_reader(path)
        except Exception as e:
            raise TraceReplayError(f"Cannot open trace file '{path}': {e}") from e

        with reader:
            for msg in reader:
                # ── filters ───────────────────────────────────────────────────
                ch = getattr(msg, "channel", None)
                if self.channel_filter is not None and ch != self.channel_filter:
                    continue
                if self.id_filter and msg.arbitration_id not in self.id_filter:
                    continue

                # ── timing ────────────────────────────────────────────────────
                if self.speed > 0 and prev_trace_ts is not None:
                    delta_trace = msg.timestamp - prev_trace_ts
                    delta_wall  = time.monotonic() - prev_wall_ts  # type: ignore[operator]
                    wait        = delta_trace / self.speed - delta_wall
                    if wait > 0:
                        time.sleep(wait)

                prev_trace_ts = msg.timestamp
                prev_wall_ts  = time.monotonic()

                # ── build CAN frame ───────────────────────────────────────────
                iface  = self._iface_for_channel(getattr(msg, "channel", 1) or 1)
                flags  = 0
                if getattr(msg, "is_fd", False):
                    flags |= _CANFD_FDF
                if getattr(msg, "bitrate_switch", False):
                    flags |= _CANFD_BRS

                frame = can_pb2.CanFrame(
                    can_id       = msg.arbitration_id,
                    dlc          = len(msg.data),
                    data         = bytes(msg.data),
                    timestamp_ns = int(msg.timestamp * 1_000_000_000),
                    iface        = iface,
                    flags        = flags,
                )

                if self.on_frame is not None:
                    self.on_frame(sent, msg)

                # ── send ──────────────────────────────────────────────────────
                try:
                    stub.SendCanFrame(
                        can_pb2.SendCanFrameRequest(
                            simulation_id = self.simulation_id,
                            frame         = frame,
                        )
                    )
                except Exception as e:
                    raise TraceReplayError(
                        f"gRPC error sending frame {sent}: {e}"
                    ) from e

                sent += 1

        return sent
