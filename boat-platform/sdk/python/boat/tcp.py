"""Python binding for the BoAt TCP plugin (.so).

Provides a high-level TcpHandle that wraps the plugin's C API,
exposing connect/send/listen/close with Python callbacks.

Usage::

    from boat.tcp import TcpHandle

    tcp = TcpHandle("build/debug/src/plugins/tcp/tcp.so")
    tcp.set_callbacks(conn_id, on_data=lambda cid, data: print(data))

    # Client mode
    conn_id = tcp.connect("192.168.0.1", 5000, "192.168.0.2", 5001)
    tcp.send(conn_id, b"Hello")

    # Server mode
    listener_id = tcp.listen("0.0.0.0", 8080)
"""
from __future__ import annotations

import ctypes
import threading
from typing import Callable, Optional

_TcpOnDataCb = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_int,
                                 ctypes.POINTER(ctypes.c_uint8), ctypes.c_uint32)
_TcpOnEventCb = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_int, ctypes.c_int)


class TcpHandle:
    """High-level wrapper around the TCP plugin's C API."""

    def __init__(self, so_path: str) -> None:
        self._lib = ctypes.CDLL(so_path)

        # Resolve ABI
        self._create_fn = self._lib.boat_plugin_create
        self._create_fn.restype = ctypes.c_void_p
        self._bp = self._create_fn()
        # BoatPlugin struct: { BoatPluginVTable* vtable; void* ctx; }
        # Read ctx (second pointer) at offset sizeof(pointer) = 8
        ctx_ptr = ctypes.c_void_p.from_address(self._bp + 8)
        self._ctx = ctx_ptr.value

        # Resolve C API
        self._tcp_connect = self._lib.tcp_connect
        self._tcp_connect.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                                       ctypes.c_uint16, ctypes.c_char_p,
                                       ctypes.c_uint16]
        self._tcp_connect.restype = ctypes.c_int

        self._tcp_listen = self._lib.tcp_listen
        self._tcp_listen.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                                      ctypes.c_uint16]
        self._tcp_listen.restype = ctypes.c_int

        self._tcp_send = self._lib.tcp_send
        self._tcp_send.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                    ctypes.POINTER(ctypes.c_uint8), ctypes.c_uint32]
        self._tcp_send.restype = ctypes.c_int

        self._tcp_set_callbacks = self._lib.tcp_set_callbacks
        self._tcp_set_callbacks.argtypes = [
            ctypes.c_void_p, ctypes.c_int,
            _TcpOnDataCb, _TcpOnEventCb, ctypes.c_void_p,
        ]

        self._tcp_close = self._lib.tcp_close
        self._tcp_close.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self._tcp_close.restype = ctypes.c_int

        self._tcp_abort = self._lib.tcp_abort
        self._tcp_abort.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self._tcp_abort.restype = ctypes.c_int

        # Python-side callbacks keyed by id
        self._callbacks: dict[int, tuple] = {}
        self._lock = threading.Lock()

    @property
    def ctx(self) -> int:
        """Raw plugin context pointer."""
        return self._ctx

    # ── Public API ─────────────────────────────────────────────────────────

    def connect(self, src_ip: str, src_port: int,
                dst_ip: str, dst_port: int) -> int:
        """Open an outgoing TCP connection. Returns conn_id."""
        return self._tcp_connect(
            self._ctx,
            src_ip.encode(),
            ctypes.c_uint16(src_port),
            dst_ip.encode(),
            ctypes.c_uint16(dst_port),
        )

    def listen(self, bind_ip: str, bind_port: int) -> int:
        """Start listening for incoming TCP connections. Returns listener_id."""
        return self._tcp_listen(
            self._ctx,
            bind_ip.encode(),
            ctypes.c_uint16(bind_port),
        )

    def send(self, conn_id: int, data: bytes) -> int:
        """Send data on an established connection."""
        buf = (ctypes.c_uint8 * len(data)).from_buffer_copy(data)
        return self._tcp_send(self._ctx, conn_id, buf, len(data))

    def set_callbacks(
        self,
        obj_id: int,
        on_data: Optional[Callable[[int, bytes], None]] = None,
        on_event: Optional[Callable[[int, int], None]] = None,
    ) -> None:
        """Register callbacks for a connection or listener.

        Args:
            obj_id: conn_id or listener_id from connect()/listen()
            on_data: called with (conn_id, data_bytes) when data arrives
            on_event: called with (conn_id, event_type) for lifecycle events
        """
        with self._lock:
            self._callbacks[obj_id] = (on_data, on_event)

        # Need to keep the ctypes callable objects alive
        def _make_data_cb(oid: int) -> _TcpOnDataCb:
            @_TcpOnDataCb
            def _cb(user_ctx, cid, data_ptr, length):
                cb_tuple = self._callbacks.get(oid)
                if cb_tuple and cb_tuple[0]:
                    data = ctypes.string_at(data_ptr, length)
                    cb_tuple[0](cid, data)
            return _cb

        def _make_event_cb(oid: int) -> _TcpOnEventCb:
            @_TcpOnEventCb
            def _cb(user_ctx, cid, event):
                cb_tuple = self._callbacks.get(oid)
                if cb_tuple and cb_tuple[1]:
                    cb_tuple[1](cid, event)
            return _cb

        data_cb = _make_data_cb(obj_id)
        event_cb = _make_event_cb(obj_id)
        self._tcp_set_callbacks(self._ctx, obj_id, data_cb, event_cb, None)

    def close(self, conn_id: int) -> int:
        """Gracefully close a connection (FIN handshake)."""
        return self._tcp_close(self._ctx, conn_id)

    def abort(self, conn_id: int) -> int:
        """Abort a connection (send RST)."""
        return self._tcp_abort(self._ctx, conn_id)
