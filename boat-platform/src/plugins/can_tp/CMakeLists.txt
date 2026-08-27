# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

add_boat_plugin(can_tp can_tp_plugin.cpp)
target_include_directories(can_tp PRIVATE ${CMAKE_SOURCE_DIR}/src)
target_link_libraries(can_tp PRIVATE boat_plugin_sdk)
