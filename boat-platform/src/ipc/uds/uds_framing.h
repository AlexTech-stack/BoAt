// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include <string>

namespace boat::ipc {

bool WriteFrame(int fd, const std::string& bytes);
bool ReadFrame(int fd, std::string& out);

}  // namespace boat::ipc
