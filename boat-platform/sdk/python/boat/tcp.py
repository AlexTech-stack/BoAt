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

    def __init__(self, so_path: str, config_json: bytes = b'{}') -> None:
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
                                       ctypes.c_uint16,
                                       ctypes.c_void_p, ctypes.c_void_p,
                                       ctypes.c_void_p]
        self._tcp_connect.restype = ctypes.c_int

        self._tcp_listen = self._lib.tcp_listen
        self._tcp_listen.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                                      ctypes.c_uint16,
                                      ctypes.c_void_p, ctypes.c_void_p,
                                      ctypes.c_void_p]
        self._tcp_listen.restype = ctypes.c_int

        self._tcp_send = self._lib.tcp_send
        self._tcp_send.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                    ctypes.POINTER(ctypes.c_uint8), ctypes.c_uint32]
        self._tcp_send.restype = ctypes.c_int

        self._tcp_set_callbacks = self._lib.tcp_set_callbacks
        self._tcp_set_callbacks.argtypes = [
            ctypes.c_void_p, ctypes.c_int,
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ]

        self._tcp_close = self._lib.tcp_close
        self._tcp_close.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self._tcp_close.restype = ctypes.c_int

        self._tcp_abort = self._lib.tcp_abort
        self._tcp_abort.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self._tcp_abort.restype = ctypes.c_int

        # Call the vtable's initialize to start TX/RX threads
        # BoatPlugin struct: { vtable* (8), ctx* (8) }
        vtable_ptr = ctypes.c_void_p.from_address(self._bp).value
        # BoatPluginVTable: { initialize* (8) at offset 0, ... }
        init_fn_ptr = ctypes.c_void_p.from_address(vtable_ptr).value
        init_fn = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_char_p)(init_fn_ptr)
        init_fn(self._ctx, config_json)

        # Keep ctypes callback objects alive (prevent GC)
        self._cb_refs: list = []
        # Python-side callbacks keyed by id
        self._callbacks: dict[int, tuple] = {}
        self._lock = threading.Lock()

    @property
    def ctx(self) -> int:
        """Raw plugin context pointer."""
        return self._ctx

    # ── Public API ─────────────────────────────────────────────────────────

    def connect(self, src_ip: str, src_port: int,
                dst_ip: str, dst_port: int,
                on_data=None, on_event=None, user_ctx=None) -> int:
        data_cb = self._make_data_cb(on_data) if on_data else None
        event_cb = self._make_event_cb(on_event) if on_event else None
        return self._tcp_connect(
            self._ctx, src_ip.encode(), ctypes.c_uint16(src_port),
            dst_ip.encode(), ctypes.c_uint16(dst_port),
            data_cb, event_cb, user_ctx,
        )

    def listen(self, bind_ip: str, bind_port: int,
               on_data=None, on_event=None, user_ctx=None) -> int:
        data_cb = self._make_data_cb(on_data) if on_data else None
        event_cb = self._make_event_cb(on_event) if on_event else None
        return self._tcp_listen(
            self._ctx, bind_ip.encode(), ctypes.c_uint16(bind_port),
            data_cb, event_cb, user_ctx,
        )

    def send(self, conn_id: int, data: bytes) -> int:
        """Send data on an established connection."""
        buf = (ctypes.c_uint8 * len(data)).from_buffer_copy(data)
        return self._tcp_send(self._ctx, conn_id, buf, len(data))

    def _make_data_cb(self, on_data):
        def _cb(user_ctx, cid, data_ptr, length):
            if on_data:
                data = ctypes.string_at(data_ptr, length)
                on_data(cid, data)
        obj = _TcpOnDataCb(_cb)
        self._cb_refs.append(obj)
        return obj

    def _make_event_cb(self, on_event):
        def _cb(user_ctx, cid, event):
            if on_event:
                on_event(cid, event)
        obj = _TcpOnEventCb(_cb)
        self._cb_refs.append(obj)
        return obj

    def set_callbacks(self, obj_id, on_data=None, on_event=None):
        data_cb = self._make_data_cb(on_data) if on_data else None
        event_cb = self._make_event_cb(on_event) if on_event else None
        self._tcp_set_callbacks(self._ctx, obj_id, data_cb, event_cb, None)

    def close(self, conn_id: int) -> int:
        """Gracefully close a connection (FIN handshake)."""
        return self._tcp_close(self._ctx, conn_id)

    def abort(self, conn_id: int) -> int:
        """Abort a connection (send RST)."""
        return self._tcp_abort(self._ctx, conn_id)
