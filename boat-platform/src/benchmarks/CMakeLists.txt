# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

add_executable(
  boat_benchmark
  benchmark_main.cpp
  ../gateway/grpc_gateway/frame_sink.cpp
)

target_include_directories(boat_benchmark PRIVATE
  ${CMAKE_SOURCE_DIR}/src
  ${CMAKE_SOURCE_DIR}/src/hil
  ${CMAKE_SOURCE_DIR}/src/gateway/grpc_gateway
)

target_link_libraries(boat_benchmark PRIVATE
  boat_core
  boat_hil
  Threads::Threads
)
