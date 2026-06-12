#!/usr/bin/env bash
# Example: CanTp — Large PDU transfer over CAN using ISO 15765-2
# Sends a 255-byte payload segmented across multiple CAN frames.
#
# Prerequisites:
#   sudo modprobe vcan
#   sudo ip link add vcan0 type vcan && sudo ip link set vcan0 up

set -euo pipefail

echo "=== CanTp (ISO 15765-2) Example ==="

# Build the CanTp plugin
echo ""
echo "1. Building CanTp plugin..."
cd "$(git rev-parse --show-toplevel)/boat-platform"
cmake --build --preset debug --target can_tp 2>/dev/null || \
  cmake --build --preset debug 2>/dev/null

PLUGIN_PATH="./build/debug/src/plugins/can_tp/can_tp.so"

echo ""
echo "2. Start gateway with CanTp plugin"
echo "   BOAT_NODE_PLUGINS=$PLUGIN_PATH BOAT_CAN_INTERFACES=vcan0"
echo "   (Start in a separate terminal)"

echo ""
echo "3. Send a large PDU via CanTp CLI"
echo "   boat can-tp send --nsdu-id 0x7E0 --data 0123456789ABCDEF00112233..."

echo ""
echo "   This segments the 255-byte payload into:"
echo "   - 1 First Frame (FF) with 8-byte header"
echo "   - N Consecutive Frames (CF) with remaining data"
echo "   - Flow Control (FC) frames manage the receiver's buffer"

echo ""
echo "4. Programmatic API (C):"
echo "   #include <boat/can_tp.h>"
echo ""
echo "   CanTpConfig cfg = {"
echo "       .nsdu_id = 0x7E0,"
echo "       .rx_buffer_size = 4095,"
echo "       .can_dlc = 8,"
echo "   };"
echo "   can_tp_configure(plugin_ctx, &cfg);"
echo ""
echo "   uint8_t data[255] = { ... };"
echo "   can_tp_send(plugin_ctx, 0x7E0, data, 255);"
echo ""

echo "=== Done ==="
