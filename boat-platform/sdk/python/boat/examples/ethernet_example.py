# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

"""Example Ethernet node: custom-protocol-responder.

Listens on veth0 for frames with EtherType 0x88B5 (IEEE 802 experimental/local use).
Treats the payload as a UTF-8 command string. Responds with an acknowledgement frame
on the same interface and publishes the command text as an 'eth.command' bus signal.

FrameService has no server-side ethertype filter, so the EtherType check is
done here -- the one behavioural difference from the per-bus service this
replaced. Ethernet metadata lives under frame.eth.
"""
from __future__ import annotations

from boat.bus_node import BusNode
from boat.frame_node import FrameNode

ETHERTYPE = 0x88B5


class CustomProtocolResponder:
    def __init__(self) -> None:
        self._frames = FrameNode()
        self._bus = BusNode(node_id="eth-responder")

    def on_frame(self, frame) -> None:
        if frame.eth.ethertype != ETHERTYPE:
            return
        try:
            command = frame.payload.decode("utf-8").strip()
        except UnicodeDecodeError:
            return

        # Publish command text as a bus signal
        self._bus.publish("eth.command", command)

        # Send an ACK frame back: same ethertype, payload "ACK:<command>"
        ack_payload = f"ACK:{command}".encode("utf-8")
        self._frames.send_eth(
            frame.iface,
            dst_mac=frame.eth.src_mac,
            src_mac=frame.eth.dst_mac,
            ethertype=ETHERTYPE,
            payload=ack_payload,
        )

    def run(self) -> None:
        self._frames.subscribe(self.on_frame, bus_types=["ETHERNET"],
                               iface_filter="veth0")
        self._frames.run()


if __name__ == "__main__":
    CustomProtocolResponder().run()
