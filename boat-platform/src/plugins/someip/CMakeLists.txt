# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

add_boat_plugin(someip someip_plugin.cpp)
target_link_libraries(someip PRIVATE boat_plugin_sdk)
