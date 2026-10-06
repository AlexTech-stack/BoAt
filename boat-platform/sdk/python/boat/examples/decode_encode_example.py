# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

"""Example: sensor-data-decoder — decode multi-byte payload fields and re-encode responses.

Listens on vcan0 for ID 0x200.
Payload layout (8 bytes, big-endian):
  bytes 0-1: RPM         (uint16, big-endian)
  bytes 2-3: temperature (uint16, big-endian, unit: 0.1 °C)
  bytes 4-5: voltage     (uint16, big-endian, unit: 0.01 V)
  bytes 6-7: status flags (uint16, big-endian)

Publishes each field as a named bus signal.
Sends an acknowledgement frame (ID 0x201) with a 4-byte big-endian echo of RPM and temp.

Demonstrates:
  - Reading multi-byte integers:  int.from_bytes(frame.payload[0:2], "big")
  - Reading single bytes:         frame.payload[0]
  - Writing multi-byte integers:  value.to_bytes(2, "big")
  - Building a multi-field payload by concatenating .to_bytes() results
  - NEVER use bytes([N]) when N > 255 — always use .to_bytes() for values > 255
"""
from __future__ import annotations

from boat.bus_node import BusNode
from boat.frame_node import FrameNode


class SensorDataDecoder:
    def __init__(self) -> None:
        self._frames = FrameNode()
        self._bus = BusNode(node_id="sensor-decoder")

    def on_frame(self, frame) -> None:
        if frame.can.can_id != 0x200:
            return
        if frame.can.dlc < 8:
            return  # incomplete frame, ignore

        # Decode payload fields — int.from_bytes is the correct way to read
        # multi-byte integers from CAN data; NEVER use bytes([N]) for N > 255.
        # On a unified Frame the bytes are frame.payload, not frame.data.
        rpm   = int.from_bytes(frame.payload[0:2], "big")
        temp  = int.from_bytes(frame.payload[2:4], "big") / 10.0   # 0.1 °C → °C
        volts = int.from_bytes(frame.payload[4:6], "big") / 100.0  # 0.01 V → V
        flags = int.from_bytes(frame.payload[6:8], "big")

        # Single-byte read (no conversion needed, already 0-255)
        status_byte = frame.payload[6]

        # Publish decoded values to the bus
        self._bus.publish("sensor.rpm",   float(rpm))
        self._bus.publish("sensor.temp",  temp)
        self._bus.publish("sensor.volts", volts)
        self._bus.publish("sensor.flags", float(flags))

        # Build response payload: RPM (2 bytes BE) + temp raw (2 bytes BE)
        # Use .to_bytes() — this is the correct way to encode integers into bytes.
        ack_payload = rpm.to_bytes(2, "big") + int(temp * 10).to_bytes(2, "big")
        self._frames.send_can(frame.iface, 0x201, ack_payload)

    def run(self) -> None:
        self._frames.subscribe(self.on_frame, bus_types=["CAN", "CANFD"],
                               iface_filter="vcan0")
        self._frames.run()


if __name__ == "__main__":
    SensorDataDecoder().run()
