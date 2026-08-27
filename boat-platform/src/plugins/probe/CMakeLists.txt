# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

add_boat_plugin(probe probe_plugin.cpp)
target_link_libraries(probe PRIVATE boat_plugin_sdk)
