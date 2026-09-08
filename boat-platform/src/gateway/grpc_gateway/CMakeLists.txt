# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

add_executable(
  boat_gateway
  main.cpp
  bus_service_impl.cpp
  can_service_impl.cpp
  debug_service_impl.cpp
  ethernet_service_impl.cpp
  pdu_service_impl.cpp
  can_tp_service_impl.cpp
  simulation_service_impl.cpp
  signal_service_impl.cpp
  scenario_service_impl.cpp
  replay_service_impl.cpp
  plugin_service_impl.cpp
  node_plugin_service_impl.cpp
  metrics_service_impl.cpp
  trace_service_impl.cpp
  fault_service_impl.cpp
  frame_service_impl.cpp
  frame_sink.cpp
  effective_config.cpp
)
target_include_directories(boat_gateway PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}
  ${BOAT_PROTO_GENERATED_DIR}
  ${CMAKE_SOURCE_DIR}/src/hil
)
target_link_libraries(
  boat_gateway
  PRIVATE
    boat_core
    boat_hil
    boat_ipc
    boat_store
    boat_replay
    grpc++
    protobuf::libprotobuf
    spdlog::spdlog
)
target_compile_features(boat_gateway PRIVATE cxx_std_20)
# Stamped into the effective-config artifact so a committed run configuration
# says which gateway produced it.
target_compile_definitions(boat_gateway PRIVATE BOAT_VERSION="${PROJECT_VERSION}")

install(TARGETS boat_gateway RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})
