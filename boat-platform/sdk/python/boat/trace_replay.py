"""Python SDK for replaying CAN trace files through the BoAt gateway.

Supported formats: ASC, BLF (via python-can).

Quick example::

    from boat.trace_replay import TraceReplayer

    replayer = TraceReplayer(
        gateway="localhost:50051",
        buses=["vcan0", "vcan1"],   # channel 1 → vcan0, channel 2 → vcan1
        speed=1.0,                   # real-time; >1 faster, <1 slower
    )
    replayer.replay("recording.asc")
    # or loop forever:
    replayer.replay("recording.asc", loop=True)
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, List, Optional

# CAN FD flags (matches gateway constants)
_CANFD_BRS = 0x01   # bit-rate switch
_CANFD_FDF = 0x04   # FD frame


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
    ) -> None:
        self.gateway       = gateway
        self.buses         = buses or []
        self.speed         = speed
        self.simulation_id = simulation_id
        self.on_frame      = on_frame
        self._stub         = None

    # ── Public API ─────────────────────────────────────────────────────────────

    def replay(self, path: str | Path, loop: bool = False) -> int:
        """Replay a trace file.

        Args:
            path: Path to an ``.asc`` or ``.blf`` file.
            loop: If *True* replay the file indefinitely until the process is
                  interrupted.

        Returns:
            Total number of frames sent (across all loop iterations).

        Raises:
            TraceReplayError: If the file cannot be opened or a gRPC error
            occurs.
        """
        stub = self._get_stub()
        path = Path(path)
        total = 0
        first = True
        while True:
            total += self._replay_once(stub, path, first)
            first = False
            if not loop:
                break
        return total

    # ── Internals ──────────────────────────────────────────────────────────────

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
