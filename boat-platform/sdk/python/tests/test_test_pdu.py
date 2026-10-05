# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path

from boat.test.pdu import PduHelper, _unpack_intel, _unpack_motorola, unpack_message
from boat.message import Message
from boat.pdu_db import PduDatabase


# Anchored to the repo rather than the CWD: the invocation CLAUDE.md documents
# runs pytest from the repo root, where "./config/..." does not exist, so these
# tests failed for anyone following the README while passing from boat-platform/.
DB_PATH = str(Path(__file__).resolve().parents[3] / "config" / "pdu_db_test.json")


class TestBitUnpacking:
    def test_unpack_intel_full_byte(self) -> None:
        data = bytes([0b10101010])
        val = _unpack_intel(data, start_bit=0, length=8)
        assert val == 0b10101010

    def test_unpack_intel_partial(self) -> None:
        data = bytes([0b11100111])
        # bits 2,3,4 = 1,0,0 → LSB at bit 2 → 001 = 1
        val = _unpack_intel(data, start_bit=2, length=3)
        assert val == 1

    def test_unpack_intel_multi_byte(self) -> None:
        data = bytes([0x01, 0x02])
        val = _unpack_intel(data, start_bit=0, length=16)
        assert val == 0x0201  # little-endian

    def test_unpack_motorola_single_byte(self) -> None:
        """A byte-aligned 8-bit Motorola signal reads back as that byte.

        StartPos=7 is the MSB in DBC/Vector numbering, so bit index 7 is
        byte 0 bit 7 and the eight bits read are simply byte 0. This used to
        assert 85 with the comment "bit-reversed D0->D7", which described the
        spurious 7 - (n % 8) inversion rather than the convention.
        """
        data = bytes([0b10101010])
        val = _unpack_motorola(data, start_bit=7, length=8)
        assert val == 0xAA

    def test_unpack_motorola_high_bits(self) -> None:
        """StartPos=7, Length=2 reads bits 7 and 6 -- the top of the byte."""
        data = bytes([0b00000011])
        val = _unpack_motorola(data, start_bit=7, length=2)
        assert val == 0  # bits 7 and 6 of 0x03 are both clear
        assert _unpack_motorola(bytes([0b11000000]), start_bit=7, length=2) == 3

    def test_unpack_motorola_multi_byte(self) -> None:
        """A 16-bit Motorola signal is big-endian across the two bytes."""
        assert _unpack_motorola(bytes([0x12, 0x34]), start_bit=7, length=16) == 0x1234
        assert _unpack_motorola(bytes([0x01, 0x02]), start_bit=7, length=16) == 0x0102


class TestPduHelper:
    def test_get_message_by_name(self) -> None:
        helper = PduHelper(DB_PATH)
        msg = helper.get_message("CoolantTemp")
        assert msg.name == "CoolantTemp"
        assert msg.length > 0

    def test_pack_unsigned_roundtrip(self) -> None:
        """Unsigned Intel signal roundtrip."""
        helper = PduHelper(DB_PATH)
        payload = helper.pack("VehicleSpeed", {"VehicleSpeed": 100.0})
        values = helper.unpack("VehicleSpeed", payload)
        assert abs(values["VehicleSpeed"] - 100.0) < 0.1

    def test_pack_motorola_roundtrip(self) -> None:
        """Unsigned Motorola signal roundtrip."""
        helper = PduHelper(DB_PATH)
        payload = helper.pack("CoolantTemp", {"CoolantTemp": 80.0})
        values = helper.unpack("CoolantTemp", payload)
        assert abs(values["CoolantTemp"] - 80.0) < 0.1

    def test_pack_motorola_multi_byte(self) -> None:
        """16-bit unsigned Motorola signal roundtrip."""
        helper = PduHelper(DB_PATH)
        payload = helper.pack("HV_Voltage", {"HV_Voltage": 400.0})
        values = helper.unpack("HV_Voltage", payload)
        assert abs(values["HV_Voltage"] - 400.0) < 0.1

    def test_lookup_can_id(self) -> None:
        helper = PduHelper(DB_PATH)
        cid = helper.lookup_can_id("VehicleSpeed", bus="Powertrain_CAN")
        assert cid == 0x100

    def test_get_message_unknown_raises(self) -> None:
        helper = PduHelper(DB_PATH)
        try:
            helper.get_message("NonExistent")
            assert False, "Should have raised"
        except KeyError:
            pass

    def test_unpack_message_function(self) -> None:
        db = PduDatabase(DB_PATH)
        entry = db.by_name_and_bus("VehicleSpeed", "Powertrain_CAN")
        msg = Message(entry)
        msg.set("VehicleSpeed", 100.0)
        payload = msg.pack()
        values = unpack_message(payload, msg)
        assert abs(values["VehicleSpeed"] - 100.0) < 0.1

    def test_pack_unpack_multiple_messages(self) -> None:
        """Different message types pack/unpack correctly."""
        helper = PduHelper(DB_PATH)
        pairs = [
            ("VehicleSpeed", {"VehicleSpeed": 50.0}),
            ("CoolantTemp", {"CoolantTemp": 90.0}),
            ("BatterySOC", {"BatterySOC": 75.0}),
        ]
        for name, signals in pairs:
            payload = helper.pack(name, signals)
            values = helper.unpack(name, payload)
            for sname, expected in signals.items():
                assert abs(values[sname] - expected) < 0.5, f"{name}.{sname}: {values[sname]} != {expected}"

    def test_db_property(self) -> None:
        helper = PduHelper(DB_PATH)
        assert helper.db is not None
