// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include <memory>
#include <string>

#include "can/socket_can_driver.h"
#include "hal/hal_driver.h"

namespace boat::hil {

class PhysicalCanDriver : public IHalDriver {
 public:
  explicit PhysicalCanDriver(std::string iface);

  bool Open() override;
  bool ReadFrame(CanFrame& out_frame) override;
  bool WriteFrame(const CanFrame& frame) override;
  void Close() override;
  CanInterfaceInfo GetInfo() const override;

 private:
  static bool IsPhysicalInterface(const std::string& iface);
  bool ReadInterfaceInfo();

  std::string iface_;
  SocketCanDriver driver_;
  CanInterfaceInfo info_{};
};

}  // namespace boat::hil
