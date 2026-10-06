# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional

import grpc

DEFAULT_ADDRESS = "localhost:50051"


class TlsConfigError(RuntimeError):
    """Raised when TLS is configured but unusable.

    Always raised rather than falling back to an insecure channel: a client
    that silently downgrades when its certificate is missing or unreadable
    sends traffic in the clear that the operator believed was encrypted,
    which is worse than failing to connect.
    """


@dataclass(frozen=True)
class TlsConfig:
    """Client-side TLS settings for reaching a TLS-enabled gateway.

    The gateway turns TLS on with BOAT_TLS_CERT + BOAT_TLS_KEY, and demands
    client certificates as well when BOAT_TLS_CLIENT_CA is set (see
    MakeServerCredentials in src/gateway/grpc_gateway/main.cpp). These are the
    matching client-side knobs:

      BOAT_TLS_CA           PEM path of the CA that signed the gateway's
                            certificate. Setting it enables TLS.
      BOAT_TLS=1            Enable TLS using gRPC's default root store, for a
                            gateway whose certificate chains to a public CA.
                            Unnecessary when BOAT_TLS_CA is set.
      BOAT_TLS_CLIENT_CERT  PEM path of this client's certificate, for a
      BOAT_TLS_CLIENT_KEY   gateway started with BOAT_TLS_CLIENT_CA (mTLS).
                            Must be set together, mirroring the gateway's own
                            refusal to start with only one of CERT/KEY.
      BOAT_TLS_SERVER_NAME  Expected certificate name, when the gateway is
                            reached by an address the certificate does not
                            name -- an IP on a HIL bench, say. Hostname
                            verification still happens, against this name.

    With none of these set the channel is insecure, which is the historical
    behaviour and still the right default for vcan work on localhost.
    """

    root_ca: Optional[str] = None
    client_cert: Optional[str] = None
    client_key: Optional[str] = None
    server_name: Optional[str] = None

    @classmethod
    def from_env(cls) -> Optional["TlsConfig"]:
        """Build a config from the environment, or None if TLS is not wanted.

        Raises TlsConfigError if the environment asks for something
        contradictory, rather than guessing.
        """
        root_ca = os.environ.get("BOAT_TLS_CA") or None
        client_cert = os.environ.get("BOAT_TLS_CLIENT_CERT") or None
        client_key = os.environ.get("BOAT_TLS_CLIENT_KEY") or None
        server_name = os.environ.get("BOAT_TLS_SERVER_NAME") or None
        # Only the exact string "1" or "true" opts in, so a typo cannot
        # quietly leave a bench talking in the clear -- the same reasoning as
        # BOAT_TIME_SOURCE accepting only "virtual".
        explicit = os.environ.get("BOAT_TLS", "").strip().lower() in ("1", "true")

        if (client_cert is None) != (client_key is None):
            raise TlsConfigError(
                "BOAT_TLS_CLIENT_CERT and BOAT_TLS_CLIENT_KEY must be set "
                "together (mTLS needs both halves of the client identity)"
            )
        if not (root_ca or client_cert or server_name or explicit):
            return None
        return cls(root_ca=root_ca, client_cert=client_cert,
                   client_key=client_key, server_name=server_name)

    def credentials(self) -> grpc.ChannelCredentials:
        """Load the PEM files and build gRPC channel credentials."""
        return grpc.ssl_channel_credentials(
            root_certificates=_read_pem(self.root_ca, "BOAT_TLS_CA"),
            private_key=_read_pem(self.client_key, "BOAT_TLS_CLIENT_KEY"),
            certificate_chain=_read_pem(self.client_cert, "BOAT_TLS_CLIENT_CERT"),
        )

    def channel_options(self) -> list:
        if self.server_name:
            return [("grpc.ssl_target_name_override", self.server_name)]
        return []


def _read_pem(path: Optional[str], env_name: str) -> Optional[bytes]:
    if path is None:
        return None
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as exc:
        raise TlsConfigError(f"{env_name}: cannot read '{path}': {exc}") from exc
    if not data.strip():
        raise TlsConfigError(f"{env_name}: '{path}' is empty")
    return data


def make_channel(address: Optional[str] = None,
                 tls: Optional[TlsConfig] = None) -> grpc.Channel:
    """Open a channel to a gateway, secure or not according to `tls`/the env.

    The single place a channel is created, so TLS cannot be supported in one
    entry point and missing from another -- which is how a TLS-enabled
    gateway came to be unreachable from the SDK and CLI at all while the
    gateway had supported it for some time.

    `tls=None` resolves from the environment via TlsConfig.from_env(); pass a
    TlsConfig explicitly to override it.
    """
    resolved_address = resolve_address(address)
    tls = tls if tls is not None else TlsConfig.from_env()
    if tls is None:
        return grpc.insecure_channel(resolved_address)
    return grpc.secure_channel(resolved_address, tls.credentials(),
                               options=tls.channel_options())


def resolve_address(address: Optional[str] = None) -> str:
    """Resolve a gateway address: explicit arg > BOAT_HOST env var > default.

    This is what makes a node script/binary portable across gateways/devices
    without editing code -- point it at a different gateway by setting
    BOAT_HOST in its environment, not by hardcoding a host:port into the
    script itself.

    Everything that takes a gateway address must route through here rather
    than defaulting its own parameter to the literal "localhost:50051": such
    a literal is indistinguishable from a caller-supplied address, so it
    wins over BOAT_HOST and silently pins the caller to localhost. That is
    exactly how CanTpHandle, TraceRecorder and TraceReplayer ended up
    ignoring BOAT_HOST while FrameNode and PduNode honoured it.
    """
    return address or os.environ.get("BOAT_HOST", DEFAULT_ADDRESS)


class BoAtClient:
    def __init__(self, address: Optional[str] = None,
                 tls: Optional[TlsConfig] = None) -> None:
        self.address = resolve_address(address)
        self.tls = tls if tls is not None else TlsConfig.from_env()
        self.channel = make_channel(self.address, self.tls)
        self._stubs_loaded = False

    def _load_stubs(self) -> None:
        if self._stubs_loaded:
            return
        from boat.v1 import bus_pb2_grpc
        from boat.v1 import can_tp_pb2_grpc
        from boat.v1 import debug_pb2_grpc
        from boat.v1 import fault_pb2_grpc
        from boat.v1 import metrics_pb2_grpc
        from boat.v1 import plugin_pb2_grpc
        from boat.v1 import node_plugin_pb2_grpc
        from boat.v1 import replay_pb2_grpc
        from boat.v1 import scenario_pb2_grpc
        from boat.v1 import signal_pb2_grpc
        from boat.v1 import simulation_pb2_grpc
        from boat.v1 import pdu_pb2_grpc
        from boat.v1 import trace_pb2_grpc
        from boat.v1 import frame_pb2_grpc

        self._bus = bus_pb2_grpc.BusServiceStub(self.channel)
        self._simulation = simulation_pb2_grpc.SimulationServiceStub(self.channel)
        self._signal = signal_pb2_grpc.SignalServiceStub(self.channel)
        self._scenario = scenario_pb2_grpc.ScenarioServiceStub(self.channel)
        self._replay = replay_pb2_grpc.ReplayServiceStub(self.channel)
        self._plugin = plugin_pb2_grpc.PluginServiceStub(self.channel)
        self._node_plugin = node_plugin_pb2_grpc.NodePluginServiceStub(self.channel)
        self._metrics = metrics_pb2_grpc.MetricsServiceStub(self.channel)
        self._trace = trace_pb2_grpc.TraceServiceStub(self.channel)
        self._fault = fault_pb2_grpc.FaultServiceStub(self.channel)
        self._can_tp = can_tp_pb2_grpc.CanTpServiceStub(self.channel)
        self._pdu = pdu_pb2_grpc.PduServiceStub(self.channel)
        self._debug = debug_pb2_grpc.DebugServiceStub(self.channel)
        self._frame = frame_pb2_grpc.FrameServiceStub(self.channel)
        self._stubs_loaded = True

    @property
    def bus(self) -> Any:
        self._load_stubs()
        return self._bus

    @property
    def simulation(self) -> Any:
        self._load_stubs()
        return self._simulation

    @property
    def signal(self) -> Any:
        self._load_stubs()
        return self._signal

    @property
    def scenario(self) -> Any:
        self._load_stubs()
        return self._scenario

    @property
    def replay(self) -> Any:
        self._load_stubs()
        return self._replay

    @property
    def plugin(self) -> Any:
        self._load_stubs()
        return self._plugin

    @property
    def node_plugin(self) -> Any:
        self._load_stubs()
        return self._node_plugin

    @property
    def metrics(self) -> Any:
        self._load_stubs()
        return self._metrics

    @property
    def trace(self) -> Any:
        self._load_stubs()
        return self._trace

    @property
    def fault(self) -> Any:
        self._load_stubs()
        return self._fault

    @property
    def pdu(self) -> Any:
        self._load_stubs()
        return self._pdu

    @property
    def can_tp(self) -> Any:
        self._load_stubs()
        return self._can_tp

    @property
    def debug(self) -> Any:
        self._load_stubs()
        return self._debug

    @property
    def frame(self) -> Any:
        self._load_stubs()
        return self._frame

    def close(self) -> None:
        self.channel.close()
