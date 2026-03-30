#include <cstdio>
#include <cstdlib>

#include "virtual/virtual_can_driver.h"

int main() {
  const char* enabled = std::getenv("BOAT_HIL_ENABLED");
  if (enabled == nullptr || *enabled == '\0') {
    std::puts("HIL smoke: SKIP (BOAT_HIL_ENABLED not set)");
    return 0;
  }

  const char* iface_env = std::getenv("BOAT_VCAN_IFACE");
  const char* iface = (iface_env != nullptr && *iface_env != '\0') ? iface_env : "vcan0";
  boat::hil::VirtualCanDriver tx_driver(iface);
  boat::hil::VirtualCanDriver rx_driver(iface);

  if (!tx_driver.Open()) {
    std::fprintf(stderr, "HIL smoke: FAIL (tx Open failed on %s)\n", iface);
    return 1;
  }
  if (!rx_driver.Open()) {
    std::fprintf(stderr, "HIL smoke: FAIL (rx Open failed on %s)\n", iface);
    tx_driver.Close();
    return 1;
  }

  boat::hil::CanFrame frame {};
  frame.can_id = 0x123;
  frame.dlc = 4;
  frame.data[0] = 0xDE;
  frame.data[1] = 0xAD;
  frame.data[2] = 0xBE;
  frame.data[3] = 0xEF;
  frame.timestamp_ns = 0;
  if (!tx_driver.WriteFrame(frame)) {
    std::fprintf(stderr, "HIL smoke: FAIL (WriteFrame failed)\n");
    tx_driver.Close();
    rx_driver.Close();
    return 1;
  }

  boat::hil::CanFrame received {};
  if (!rx_driver.ReadFrame(received)) {
    std::fprintf(stderr, "HIL smoke: FAIL (ReadFrame failed)\n");
    tx_driver.Close();
    rx_driver.Close();
    return 1;
  }
  if (received.can_id != 0x123) {
    std::fprintf(stderr, "HIL smoke: FAIL (unexpected can_id: 0x%X)\n", received.can_id);
    tx_driver.Close();
    rx_driver.Close();
    return 1;
  }

  tx_driver.Close();
  rx_driver.Close();
  std::puts("HIL smoke: PASS");
  return 0;
}
