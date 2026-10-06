# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from boat.test.bus import TestCanBus, TestEthBus
from boat.test.config import BusConfig
from boat.test.exceptions import TestTimeoutError
from boat.v1 import frame_pb2


def _make_bus_config(name: str = "can1", type_: str = "virtual", interface: str = "vcan0") -> BusConfig:
    return BusConfig(logical_name=name, type=type_, interface=interface)


def _can_frame(can_id: int, payload: bytes = b"", iface: str = "vcan0"):
    """A real boat.v1.Frame, not a MagicMock.

    A MagicMock auto-creates whatever attribute is asked of it, so it cannot
    tell frame.can.can_id from frame.can_id -- which is exactly the mistake
    the FrameService migration can make. Real protos fail loudly on a wrong
    field path.
    """
    return frame_pb2.Frame(
        bus_type=frame_pb2.Frame.CAN,
        iface=iface,
        payload=payload,
        can=frame_pb2.CanMetadata(can_id=can_id, dlc=len(payload)),
    )


def _eth_frame(ethertype: int, payload: bytes = b"", iface: str = "veth0"):
    return frame_pb2.Frame(
        bus_type=frame_pb2.Frame.ETHERNET,
        iface=iface,
        payload=payload,
        eth=frame_pb2.EthMetadata(ethertype=ethertype, dst_mac=b"\x00" * 6,
                                  src_mac=b"\x00" * 6),
    )


class TestFrameMatching:
    def test_match_can_id(self) -> None:
        frame = _can_frame(0x300)
        assert TestCanBus._matches(frame, can_id=0x300, data=None, mask=None)
        assert not TestCanBus._matches(frame, can_id=0x100, data=None, mask=None)

    def test_match_data_exact(self) -> None:
        frame = _can_frame(0x300, b'\x01\xF4')
        assert TestCanBus._matches(frame, can_id=0x300, data=b'\x01\xF4', mask=None)
        assert not TestCanBus._matches(frame, can_id=0x300, data=b'\x01\x00', mask=None)

    def test_match_data_with_mask(self) -> None:
        frame = _can_frame(0x300, b'\xFF\xFF')
        assert TestCanBus._matches(frame, can_id=0x300, data=b'\xAB\xCD', mask=b'\x00\x00')

    def test_match_any(self) -> None:
        frame = _can_frame(0x300, b'\x01')
        assert TestCanBus._matches(frame, can_id=None, data=None, mask=None)


class TestTestCanBus:
    def test_send_calls_rpc(self) -> None:
        mock_client = MagicMock()
        resp = MagicMock()
        resp.accepted = True
        mock_client.frame.SendFrame.return_value = resp

        bus = TestCanBus(mock_client, _make_bus_config())
        result = bus.send(0x100, b'\x01\x02')
        assert result is True
        mock_client.frame.SendFrame.assert_called_once()

    def test_send_builds_a_unified_can_frame(self) -> None:
        """The field mapping is the part a mock cannot check for us."""
        mock_client = MagicMock()
        mock_client.frame.SendFrame.return_value = MagicMock(accepted=True)

        bus = TestCanBus(mock_client, _make_bus_config())
        bus.send(0x123, b'\xAA\xBB\xCC', flags=0x04)

        sent = mock_client.frame.SendFrame.call_args[0][0].frame
        assert sent.bus_type == frame_pb2.Frame.CAN
        assert sent.iface == "vcan0"
        assert sent.payload == b'\xAA\xBB\xCC'
        assert sent.can.can_id == 0x123
        assert sent.can.dlc == 3
        assert sent.can.flags == 0x04

    def test_expect_returns_matching_frame(self) -> None:
        mock_client = MagicMock()
        mock_client.frame.SubscribeFrames.return_value = iter([
            _can_frame(0x100, b'\x01'),
            _can_frame(0x300, b'\x02'),
        ])

        bus = TestCanBus(mock_client, _make_bus_config())
        result = bus.expect(can_id=0x300, timeout_ms=500)
        assert result.can.can_id == 0x300

    def test_expect_requests_can_bus_types_and_iface(self) -> None:
        mock_client = MagicMock()
        mock_client.frame.SubscribeFrames.return_value = iter([])

        bus = TestCanBus(mock_client, _make_bus_config())
        with pytest.raises(TestTimeoutError):
            bus.expect(can_id=0x999, timeout_ms=50)

        req = mock_client.frame.SubscribeFrames.call_args[0][0]
        assert set(req.bus_types) == {frame_pb2.Frame.CAN, frame_pb2.Frame.CANFD}
        assert req.iface_filter == "vcan0"

    def test_expect_timeout(self) -> None:
        mock_client = MagicMock()
        mock_client.frame.SubscribeFrames.return_value = iter([])

        bus = TestCanBus(mock_client, _make_bus_config())
        with pytest.raises(TestTimeoutError):
            bus.expect(can_id=0x999, timeout_ms=50)

    def test_send_raises_on_error(self) -> None:
        mock_client = MagicMock()
        mock_client.frame.SendFrame.side_effect = RuntimeError("connection refused")

        bus = TestCanBus(mock_client, _make_bus_config())
        with pytest.raises(RuntimeError, match="CAN send failed"):
            bus.send(0x100, b'\x01')

    def test_properties(self) -> None:
        bus = TestCanBus(MagicMock(), _make_bus_config("mycan", "physical", "can0"))
        assert bus.name == "mycan"
        assert bus.interface == "can0"


class TestTestEthBus:
    def test_send_calls_rpc(self) -> None:
        mock_client = MagicMock()
        resp = MagicMock()
        resp.accepted = True
        mock_client.frame.SendFrame.return_value = resp

        bus = TestEthBus(mock_client, _make_bus_config("eth0", "virtual_eth", "veth0"))
        result = bus.send(dst_mac=b'\x00' * 6, ethertype=0x88B5, payload=b'test')
        assert result is True
        mock_client.frame.SendFrame.assert_called_once()

    def test_send_builds_a_unified_eth_frame(self) -> None:
        mock_client = MagicMock()
        mock_client.frame.SendFrame.return_value = MagicMock(accepted=True)

        bus = TestEthBus(mock_client, _make_bus_config("eth0", "virtual_eth", "veth0"))
        bus.send(dst_mac=b'\x11' * 6, src_mac=b'\x22' * 6, ethertype=0x0800,
                 payload=b'xyz', vlan_id=7)

        sent = mock_client.frame.SendFrame.call_args[0][0].frame
        assert sent.bus_type == frame_pb2.Frame.ETHERNET
        assert sent.iface == "veth0"
        assert sent.payload == b'xyz'
        assert sent.eth.dst_mac == b'\x11' * 6
        assert sent.eth.src_mac == b'\x22' * 6
        assert sent.eth.ethertype == 0x0800
        assert sent.eth.vlan_id == 7

    def test_expect_returns_matching_ethertype(self) -> None:
        mock_client = MagicMock()
        mock_client.frame.SubscribeFrames.return_value = iter([_eth_frame(0x88B5)])

        bus = TestEthBus(mock_client, _make_bus_config("eth0", "virtual_eth", "veth0"))
        result = bus.expect(ethertype=0x88B5, timeout_ms=500)
        assert result.eth.ethertype == 0x88B5

    def test_expect_requests_ethernet_bus_type_and_iface(self) -> None:
        mock_client = MagicMock()
        mock_client.frame.SubscribeFrames.return_value = iter([])

        bus = TestEthBus(mock_client, _make_bus_config("eth0", "virtual_eth", "veth0"))
        with pytest.raises(TestTimeoutError):
            bus.expect(ethertype=0x0800, timeout_ms=50)

        req = mock_client.frame.SubscribeFrames.call_args[0][0]
        assert list(req.bus_types) == [frame_pb2.Frame.ETHERNET]
        assert req.iface_filter == "veth0"

    def test_expect_filters_ethertype_client_side(self) -> None:
        """FrameService has no server-side ethertype filter, so a non-matching
        frame must be skipped here rather than returned."""
        mock_client = MagicMock()
        mock_client.frame.SubscribeFrames.return_value = iter([
            _eth_frame(0x0800), _eth_frame(0x88B5),
        ])

        bus = TestEthBus(mock_client, _make_bus_config("eth0", "virtual_eth", "veth0"))
        assert bus.expect(ethertype=0x88B5, timeout_ms=500).eth.ethertype == 0x88B5

    def test_expect_timeout(self) -> None:
        mock_client = MagicMock()
        mock_client.frame.SubscribeFrames.return_value = iter([])

        bus = TestEthBus(mock_client, _make_bus_config("eth0", "virtual_eth", "veth0"))
        with pytest.raises(TestTimeoutError):
            bus.expect(ethertype=0x0800, timeout_ms=50)
