# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

"""Echo CAN frame from vcan0 to vcan1."""

from boat.frame_node import FrameNode


class EchoCanFrame:
    def __init__(self) -> None:
        # Address left unset so BOAT_HOST decides.
        self._frames = FrameNode()

    def on_frame(self, frame) -> None:
        if frame.can.can_id != 0x300:
            return
        # Send the same payload with ID 0x301 on vcan1
        self._frames.send_can("vcan1", 0x301, frame.payload)

    def run(self) -> None:
        self._frames.subscribe(self.on_frame, bus_types=["CAN", "CANFD"],
                               iface_filter="vcan0")
        self._frames.run()


if __name__ == "__main__":
    EchoCanFrame().run()
