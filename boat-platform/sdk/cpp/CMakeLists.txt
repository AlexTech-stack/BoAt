# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

add_library(boat_plugin_sdk INTERFACE)

target_include_directories(boat_plugin_sdk
  INTERFACE
    "${CMAKE_CURRENT_SOURCE_DIR}/include"
)

# The installed ABI headers must be self-contained: plugin.h includes
# boat/frame.h, so shipping plugin.h alone left every out-of-tree plugin unable
# to compile against the package. test_harness.h is deliberately not installed
# -- it pulls in gateway-internal headers (core/, hil/, pdu/).
install(
  FILES
    "${CMAKE_CURRENT_SOURCE_DIR}/include/boat/plugin.h"
    "${CMAKE_CURRENT_SOURCE_DIR}/include/boat/frame.h"
  DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}/boat"
)
