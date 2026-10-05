# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

"""Golden-vector tests for signal packing/unpacking.

These assert *exact bytes*, which is the whole point. The pre-existing
pack/unpack tests round-trip a value through the same pair of functions, so
they pass whenever pack and unpack share a convention -- including when that
shared convention is wrong. Both the unsigned-clamping bug (signed signals
packed as 0) and the Motorola bit-inversion bug (StartPos=7/Length=16/0x1234
packed as 48 2c instead of 12 34) survived a green suite that way.

The vectors live in data/signal_vectors.json so the C++ COM library can be
held to the same table once it is fixed.
"""

import json
from pathlib import Path

import pytest

from boat.message import Message
from boat.test.pdu import unpack_message

_VECTORS_PATH = Path(__file__).parent / "data" / "signal_vectors.json"


def _load_vectors() -> list[dict]:
    with open(_VECTORS_PATH) as f:
        return json.load(f)["vectors"]


VECTORS = _load_vectors()


def _message_for(vec: dict) -> Message:
    """Build a single-signal Message matching one vector's layout."""
    return Message({
        "MessageName": vec["name"],
        "DbId": 1,
        "BusType": "CAN",
        "Bus": "vcan0",
        "Length": vec["message_length"],
        "signals": [{
            "SignalName": "S",
            "StartPos": vec["start_pos"],
            "Length": vec["length"],
            "ByteOrder": vec["byte_order"],
            "ValueType": vec["value_type"],
            "Factor": vec["factor"],
            "Offset": vec["offset"],
            "InitValue": 0.0,
        }],
    })


@pytest.mark.parametrize("vec", VECTORS, ids=[v["name"] for v in VECTORS])
def test_pack_matches_golden_bytes(vec: dict) -> None:
    msg = _message_for(vec)
    msg.set("S", vec["physical"])
    assert msg.pack().hex() == vec["bytes"]


@pytest.mark.parametrize("vec", VECTORS, ids=[v["name"] for v in VECTORS])
def test_unpack_matches_golden_physical(vec: dict) -> None:
    msg = _message_for(vec)
    values = unpack_message(bytes.fromhex(vec["bytes"]), msg)
    assert values["S"] == pytest.approx(float(vec["physical"]))


@pytest.mark.parametrize("vec", VECTORS, ids=[v["name"] for v in VECTORS])
def test_round_trip(vec: dict) -> None:
    msg = _message_for(vec)
    msg.set("S", vec["physical"])
    assert unpack_message(msg.pack(), msg)["S"] == pytest.approx(float(vec["physical"]))


def test_vectors_cover_both_byte_orders_and_signedness() -> None:
    """Guard against the table silently losing the cases that caught the bugs."""
    assert {v["byte_order"] for v in VECTORS} == {0, 1}
    assert {v["value_type"] for v in VECTORS} >= {"Signed", "Unsigned"}
    # A multi-byte Motorola signed signal is the shape that hit both bugs at
    # once (config/pdu_db_test.json's HV_Current).
    assert any(v["byte_order"] == 1 and v["value_type"] == "Signed" and v["length"] > 8
               for v in VECTORS)
    # And at least one negative physical value, which the clamp used to zero.
    assert any(float(v["physical"]) < 0 for v in VECTORS)
