# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for ``tools/dbc2boatjson.py``.

A DBC ``SG_`` line's ``[min|max]`` range is already in physical units, but the
converter treated it as raw and re-applied factor and offset, so every signal
with factor != 1 or offset != 0 got a wrong ``Min``/``Max`` in the generated
PDU database. Reported by a consumer project against a converted restbus
database (issue #11); the cases below are the reporter's table.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_TOOLS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOLS_DIR))

from dbc2boatjson import build_boat_db, parse_dbc  # noqa: E402


# name, factor, offset, DBC range min, DBC range max
SIGNALS = [
    ("VehicleSpeed", 0.01, 0.0, 0.0, 400.0),
    ("EngineSpeed", 0.25, 0.0, 0.0, 10000.0),
    ("BoostPressure", 0.001, 0.0, -1.0, 3.0),
    ("OutsideTemp", 0.5, -50.0, -50.0, 77.5),
    ("CoolantTemp", 1.0, -40.0, -40.0, 215.0),
    ("BatteryVoltage", 0.1, 0.0, 0.0, 25.5),
    ("TyrePressureFL", 0.02, 0.0, 0.0, 5.1),
]

DBC = """\
VERSION ""

NS_ :
    CM_
    BA_
    VAL_

BS_:

BU_: ECU

BO_ 100 Restbus : 10 ECU
 SG_ VehicleSpeed : 0|8@1+ (0.01,0) [0|400] "km/h" ECU
 SG_ EngineSpeed : 8|16@1+ (0.25,0) [0|10000] "rpm" ECU
 SG_ BoostPressure : 24|16@1+ (0.001,0) [-1|3] "bar" ECU
 SG_ OutsideTemp : 40|16@1+ (0.5,-50) [-50|77.5] "degC" ECU
 SG_ CoolantTemp : 56|8@1+ (1,-40) [-40|215] "degC" ECU
 SG_ BatteryVoltage : 64|8@1+ (0.1,0) [0|25.5] "V" ECU
 SG_ TyrePressureFL : 72|8@1+ (0.02,0) [0|5.1] "bar" ECU
"""


@pytest.fixture(scope="module")
def boat_db(tmp_path_factory: pytest.TempPathFactory) -> dict:
    dbc_path = tmp_path_factory.mktemp("dbc") / "restbus.dbc"
    dbc_path.write_text(DBC)
    return build_boat_db(parse_dbc(str(dbc_path)))


@pytest.mark.parametrize(
    "name,factor,offset,dbc_min,dbc_max",
    SIGNALS,
    ids=[s[0] for s in SIGNALS],
)
def test_min_max_pass_through_unscaled(
    boat_db: dict,
    name: str,
    factor: float,
    offset: float,
    dbc_min: float,
    dbc_max: float,
) -> None:
    sig = next(
        s
        for m in boat_db["messages"]
        for s in m["signals"]
        if s["SignalName"] == name
    )
    assert sig["Min"] == dbc_min
    assert sig["Max"] == dbc_max
    assert sig["Factor"] == factor
    assert sig["Offset"] == offset
