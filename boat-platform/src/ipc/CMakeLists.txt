add_library(boat_ipc STATIC
  ipc_channel_selector.cpp
  ipc_payload_dispatch.cpp
  shm/shm_publisher.cpp
  shm/shm_subscriber.cpp
  uds/uds_server.cpp
  uds/uds_client.cpp
  uds/uds_framing.cpp
  grpc/grpc_server.cpp
  grpc/services/simulation_service_stub.cpp
  grpc/services/signal_service_stub.cpp
  grpc/services/scenario_service_stub.cpp
  grpc/services/replay_service_stub.cpp
  grpc/services/plugin_service_stub.cpp
  grpc/services/metrics_service_stub.cpp
  grpc/services/trace_service_stub.cpp
  grpc/services/fault_service_stub.cpp
)

target_include_directories(boat_ipc PUBLIC
  "${CMAKE_CURRENT_SOURCE_DIR}"
  "${BOAT_PROTO_GENERATED_DIR}"
)

target_link_libraries(boat_ipc PUBLIC
  boat_core
  grpc++
  grpc
  protobuf::libprotobuf
  spdlog::spdlog
  iceoryx2-cxx::static-lib-cxx
)

if(TARGET boat_proto_generated)
  target_link_libraries(boat_ipc PUBLIC boat_proto_generated)
endif()

target_compile_features(boat_ipc PUBLIC cxx_std_20)
if(TARGET boat_proto_contracts)
  add_dependencies(boat_ipc boat_proto_contracts)
endif()
