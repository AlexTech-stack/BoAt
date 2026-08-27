# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
from typing import Any, Optional

import grpc

DEFAULT_ADDRESS = "localhost:50051"


class BoAtClient:
    def __init__(self, address: Optional[str] = None) -> None:
        # Resolution order: explicit `address` arg > BOAT_HOST env var > hardcoded
        # default. This is what makes a node script/binary portable across
        # gateways/devices without editing code -- point it at a different
        # gateway by setting BOAT_HOST in its environment, not by hardcoding
        # a host:port into the script itself.
        self.address = address or os.environ.get("BOAT_HOST", DEFAULT_ADDRESS)
        self.channel = grpc.insecure_channel(self.address)
        self._stubs_loaded = False

    def _load_stubs(self) -> None:
        if self._stubs_loaded:
            return
        from boat.v1 import bus_pb2_grpc
        from boat.v1 import can_pb2_grpc
        from boat.v1 import can_tp_pb2_grpc
        from boat.v1 import debug_pb2_grpc
        from boat.v1 import ethernet_pb2_grpc
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
        self._ethernet = ethernet_pb2_grpc.EthernetServiceStub(self.channel)
        self._simulation = simulation_pb2_grpc.SimulationServiceStub(self.channel)
        self._signal = signal_pb2_grpc.SignalServiceStub(self.channel)
        self._scenario = scenario_pb2_grpc.ScenarioServiceStub(self.channel)
        self._replay = replay_pb2_grpc.ReplayServiceStub(self.channel)
        self._plugin = plugin_pb2_grpc.PluginServiceStub(self.channel)
        self._node_plugin = node_plugin_pb2_grpc.NodePluginServiceStub(self.channel)
        self._metrics = metrics_pb2_grpc.MetricsServiceStub(self.channel)
        self._trace = trace_pb2_grpc.TraceServiceStub(self.channel)
        self._fault = fault_pb2_grpc.FaultServiceStub(self.channel)
        self._can = can_pb2_grpc.CanServiceStub(self.channel)
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
    def ethernet(self) -> Any:
        self._load_stubs()
        return self._ethernet

    @property
    def can(self) -> Any:
        self._load_stubs()
        return self._can

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
