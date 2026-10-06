# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

"""Frame-path tests for the ui/ FastAPI services.

These services had no tests at all, which is why `resp.ifaces` on a response
whose field is `buses` survived in three of them: the AttributeError landed in
a bare `except` and the endpoint just returned an empty list forever.

The focus here is the FrameService mapping -- bus_type, iface, payload and the
can/eth metadata submessages -- because that is the part the migration off
CanService/EthernetService can silently get wrong. Real boat.v1.Frame messages
are used rather than MagicMocks: a mock invents whatever attribute is asked of
it, so it cannot tell frame.can.can_id from frame.can_id.

httpx is not installed, so FastAPI's TestClient is unavailable; these call the
endpoint functions directly, which is enough to pin the gRPC calls they make.
"""

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

_UI_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_UI_DIR))
sys.path.insert(0, str(_UI_DIR.parent / "boat-platform" / "sdk" / "python"))

from boat.v1 import frame_pb2  # noqa: E402


def _iface(name, bus_type, driver="", state="", fd=False):
    return frame_pb2.InterfaceInfo(iface=name, bus_type=bus_type, driver=driver,
                                   state=state, fd_support=fd)


def _listing(*entries):
    return SimpleNamespace(interfaces=list(entries))


def _can_frame(can_id=0x123, payload=b"\xAA\xBB", iface="vcan0", flags=0, dlc=None):
    return frame_pb2.Frame(
        bus_type=frame_pb2.Frame.CAN, iface=iface, payload=payload,
        can=frame_pb2.CanMetadata(
            can_id=can_id, dlc=len(payload) if dlc is None else dlc, flags=flags),
    )


def _eth_frame(ethertype=0x88B5, payload=b"\x01\x02", iface="veth0"):
    return frame_pb2.Frame(
        bus_type=frame_pb2.Frame.ETHERNET, iface=iface, payload=payload,
        eth=frame_pb2.EthMetadata(ethertype=ethertype, src_mac=b"\x11" * 6,
                                  dst_mac=b"\x22" * 6),
    )


def _mod(name):
    """Import a ui service module, skipping if its deps are unavailable."""
    try:
        return importlib.import_module(name)
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"cannot import ui.{name}: {exc}")


# ── commander: the frame builders every send path shares ────────────────────

class TestCommanderFrameBuilders:
    def test_can_builder_maps_every_field(self):
        c = _mod("commander")
        f = c._build_can_frame(0x7DF, b"\x01\x02\x03", "vcan0", flags=0)
        assert f.bus_type == frame_pb2.Frame.CAN
        assert f.iface == "vcan0"
        assert f.payload == b"\x01\x02\x03"
        assert f.can.can_id == 0x7DF
        assert f.can.dlc == 3
        assert f.can.flags == 0

    def test_can_builder_picks_canfd_from_the_fdf_flag(self):
        c = _mod("commander")
        f = c._build_can_frame(0x100, b"\x00" * 12, "vcan0", flags=0x04)
        assert f.bus_type == frame_pb2.Frame.CANFD
        assert f.can.flags == 0x04

    def test_can_builder_honours_an_explicit_dlc(self):
        """Callers that pad or truncate pass their own byte count."""
        c = _mod("commander")
        f = c._build_can_frame(0x100, b"\x01\x02", "vcan0", dlc=8)
        assert f.can.dlc == 8

    def test_eth_builder_maps_every_field(self):
        c = _mod("commander")
        f = c._build_eth_frame(0x0800, b"xyz", "veth0", b"\x11" * 6, b"\x22" * 6)
        assert f.bus_type == frame_pb2.Frame.ETHERNET
        assert f.iface == "veth0"
        assert f.payload == b"xyz"
        assert f.eth.ethertype == 0x0800
        assert f.eth.src_mac == b"\x11" * 6
        assert f.eth.dst_mac == b"\x22" * 6


class TestCommanderEndpoints:
    def test_can_buses_reads_interfaces_not_ifaces(self):
        """Regression: `resp.ifaces` on ListBusesResponse (field: `buses`)
        raised AttributeError into the bare except, so this always returned []."""
        c = _mod("commander")
        client = MagicMock()
        client.frame.ListInterfaces.return_value = _listing(
            _iface("vcan0", frame_pb2.Frame.CAN), _iface("can1", frame_pb2.Frame.CAN))
        with patch.object(c, "_client", return_value=client):
            assert c.api_can_buses(address="h:1") == {"ifaces": ["vcan0", "can1"]}
        req = client.frame.ListInterfaces.call_args[0][0]
        assert list(req.bus_types) == [frame_pb2.Frame.CAN]

    def test_eth_ifaces_filters_to_ethernet(self):
        c = _mod("commander")
        client = MagicMock()
        client.frame.ListInterfaces.return_value = _listing(
            _iface("veth0", frame_pb2.Frame.ETHERNET))
        with patch.object(c, "_client", return_value=client):
            assert c.api_eth_ifaces(address="h:1") == {"ifaces": ["veth0"]}
        req = client.frame.ListInterfaces.call_args[0][0]
        assert list(req.bus_types) == [frame_pb2.Frame.ETHERNET]

    def test_health_uses_frame_service(self):
        c = _mod("commander")
        client = MagicMock()
        with patch.object(c, "_client", return_value=client):
            assert c.api_gw_health() == {"running": True}
        assert client.frame.ListInterfaces.called


# ── recorder: subscribe wiring and the writer field mapping ─────────────────

class TestRecorderFrameHandling:
    def test_can_buses_reads_interfaces_not_ifaces(self):
        r = _mod("recorder")
        client = MagicMock()
        client.frame.ListInterfaces.return_value = _listing(
            _iface("vcan0", frame_pb2.Frame.CAN))
        with patch.object(r, "BoAtClient", return_value=client):
            assert r.api_can_buses(gw="h:1") == {"ifaces": ["vcan0"]}

    def test_eth_ifaces_reads_interfaces(self):
        r = _mod("recorder")
        client = MagicMock()
        client.frame.ListInterfaces.return_value = _listing(
            _iface("veth0", frame_pb2.Frame.ETHERNET))
        with patch.object(r, "BoAtClient", return_value=client):
            assert r.api_eth_ifaces(gw="h:1") == {"ifaces": ["veth0"]}

    def test_write_can_reads_unified_fields(self):
        """_write_can must read frame.can.* and frame.payload, not the old
        CanFrame's .can_id/.dlc/.data."""
        r = _mod("recorder")
        session = MagicMock()
        session.fmt = "asc"
        session._channel_map = {"vcan0": 0}
        writer = MagicMock()
        session._can_writer = writer

        r._write_can(session, _can_frame(0x321, b"\xDE\xAD\xBE\xEF"), ts=1.0)

        msg = writer.call_args[0][0]
        assert msg.arbitration_id == 0x321
        assert bytes(msg.data) == b"\xDE\xAD\xBE\xEF"

    def test_write_can_maps_canfd_flags(self):
        r = _mod("recorder")
        session = MagicMock()
        session.fmt = "asc"
        session._channel_map = {"vcan0": 0}
        writer = MagicMock()
        session._can_writer = writer

        # FDF (0x04) | BRS (0x01)
        r._write_can(session, _can_frame(0x100, b"\x00" * 12, flags=0x05), ts=1.0)

        msg = writer.call_args[0][0]
        assert msg.is_fd is True
        assert msg.bitrate_switch is True


# ── dashboard: the frame -> row mapping shown in the UI ─────────────────────

class TestDashboardPush:
    def test_push_can_frame_reads_unified_fields(self):
        d = _mod("dashboard")
        dash = d.DashboardState() if hasattr(d, "DashboardState") else d.dash
        dash.push_can_frame(_can_frame(0x1ABCDEF, b"\x01\x02", iface="vcan0"))
        row = dash.can_frames[-1]
        assert row["can_id"] == "0x1ABCDEF"
        assert row["dlc"] == 2
        assert row["data"] == "01:02"
        assert row["iface"] == "vcan0"

    def test_push_eth_frame_reads_unified_fields(self):
        d = _mod("dashboard")
        dash = d.DashboardState() if hasattr(d, "DashboardState") else d.dash
        dash.push_eth_frame(_eth_frame(0x88B5, b"\xAA\xBB", iface="veth0"))
        row = dash.eth_frames[-1]
        assert row["ethertype"] == "0x88B5"
        assert row["src_mac"] == "11:11:11:11:11:11"
        assert row["dst_mac"] == "22:22:22:22:22:22"
        assert row["length"] == 2
        assert row["iface"] == "veth0"


# ── the remaining health endpoints ──────────────────────────────────────────

class TestHealthEndpoints:
    def test_debug_health_reports_true_when_reachable(self):
        """Regression: a `return` inside the finally block discarded the
        success return, so this reported running=False against a live gateway."""
        d = _mod("debug")
        with patch.object(d, "BoAtClient", return_value=MagicMock()):
            assert d.api_gw_health() == {"running": True}

    def test_debug_health_reports_false_when_unreachable(self):
        d = _mod("debug")
        with patch.object(d, "BoAtClient", side_effect=RuntimeError("down")):
            assert d.api_gw_health() == {"running": False}

    def test_system_dashboard_lists_can_buses(self):
        """Regression: `resp.ifaces` on ListBusesResponse meant can_buses was
        always empty and `connected` always False."""
        s = _mod("system_dashboard")
        client = MagicMock()
        client.frame.ListInterfaces.return_value = _listing(
            _iface("vcan0", frame_pb2.Frame.CAN))
        client.simulation.ListSimulations.side_effect = RuntimeError("no sims")
        with patch.object(s, "client", client):
            out = s.api_system()
        assert out["can_buses"] == [{"iface": "vcan0"}]
        assert out["gateway"]["connected"] is True
