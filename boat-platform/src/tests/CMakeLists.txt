# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

include(BoAtTest)

FetchContent_Declare(
  Catch2
  GIT_REPOSITORY https://github.com/catchorg/Catch2.git
  GIT_TAG v3.6.0
)
FetchContent_MakeAvailable(Catch2)
include(Catch)

add_executable(boat_unit_tick_scheduler unit/test_tick_scheduler.cpp)
target_include_directories(boat_unit_tick_scheduler PRIVATE ${CMAKE_SOURCE_DIR}/src/hil)
target_link_libraries(boat_unit_tick_scheduler PRIVATE boat_core boat_hil Catch2::Catch2WithMain)
boat_discover_tests(boat_unit_tick_scheduler)

add_executable(boat_unit_signal_router unit/test_signal_router.cpp)
target_link_libraries(boat_unit_signal_router PRIVATE boat_core Catch2::Catch2WithMain)
boat_discover_tests(boat_unit_signal_router)

add_executable(boat_unit_event_bus unit/test_event_bus.cpp)
target_link_libraries(boat_unit_event_bus PRIVATE boat_core Catch2::Catch2WithMain)
boat_discover_tests(boat_unit_event_bus)

add_executable(boat_unit_plugin_manager unit/test_plugin_manager.cpp)
target_link_libraries(boat_unit_plugin_manager PRIVATE boat_core Catch2::Catch2WithMain)
if(TARGET pdu_router)
  add_dependencies(boat_unit_plugin_manager pdu_router)
  target_compile_definitions(boat_unit_plugin_manager PRIVATE
    PDU_ROUTER_SO="$<TARGET_FILE:pdu_router>")
endif()
if(TARGET can_tp)
  add_dependencies(boat_unit_plugin_manager can_tp)
  target_compile_definitions(boat_unit_plugin_manager PRIVATE
    CAN_TP_SO="$<TARGET_FILE:can_tp>")
endif()
boat_discover_tests(boat_unit_plugin_manager)

add_executable(boat_unit_signal_bus unit/test_signal_bus.cpp)
target_link_libraries(boat_unit_signal_bus PRIVATE boat_core Catch2::Catch2WithMain)
boat_discover_tests(boat_unit_signal_bus)

add_executable(boat_unit_sim_state_machine unit/test_sim_state_machine.cpp)
target_link_libraries(boat_unit_sim_state_machine PRIVATE boat_core Catch2::Catch2WithMain)
boat_discover_tests(boat_unit_sim_state_machine)

add_executable(boat_unit_determinism_engine unit/test_determinism_engine.cpp)
target_link_libraries(boat_unit_determinism_engine PRIVATE boat_core Catch2::Catch2WithMain)
boat_discover_tests(boat_unit_determinism_engine)

add_executable(boat_unit_fault_injector unit/test_fault_injector.cpp)
target_link_libraries(boat_unit_fault_injector PRIVATE boat_core Catch2::Catch2WithMain)
boat_discover_tests(boat_unit_fault_injector)

add_executable(boat_unit_frame unit/test_frame.cpp)
target_link_libraries(boat_unit_frame PRIVATE boat_core Catch2::Catch2WithMain)
boat_discover_tests(boat_unit_frame)

add_executable(
  boat_unit_frame_sink
  unit/test_frame_sink.cpp
  ../gateway/grpc_gateway/frame_sink.cpp
)
target_include_directories(boat_unit_frame_sink PRIVATE
  ${CMAKE_SOURCE_DIR}/src
  ${CMAKE_SOURCE_DIR}/src/hil
  ${CMAKE_SOURCE_DIR}/src/gateway/grpc_gateway
)
target_link_libraries(boat_unit_frame_sink PRIVATE boat_core boat_hil Catch2::Catch2WithMain)
boat_discover_tests(boat_unit_frame_sink)

add_executable(boat_unit_trace_id unit/test_trace_id.cpp)
target_include_directories(boat_unit_trace_id PRIVATE ${CMAKE_SOURCE_DIR}/src)
target_link_libraries(boat_unit_trace_id PRIVATE boat_store Catch2::Catch2WithMain)
boat_discover_tests(boat_unit_trace_id)

add_executable(boat_unit_trace_store unit/test_trace_store.cpp)
target_include_directories(boat_unit_trace_store PRIVATE ${CMAKE_SOURCE_DIR}/src)
target_link_libraries(boat_unit_trace_store PRIVATE boat_store Catch2::Catch2WithMain)
boat_discover_tests(boat_unit_trace_store)

add_executable(boat_unit_scenario_json unit/test_scenario_json.cpp)
target_include_directories(boat_unit_scenario_json PRIVATE ${CMAKE_SOURCE_DIR}/src)
target_link_libraries(boat_unit_scenario_json PRIVATE boat_core Catch2::Catch2WithMain)
boat_discover_tests(boat_unit_scenario_json)

add_executable(boat_unit_sqlite_event_store unit/test_sqlite_event_store.cpp)
target_link_libraries(boat_unit_sqlite_event_store PRIVATE boat_core boat_store Catch2::Catch2WithMain)
boat_discover_tests(boat_unit_sqlite_event_store)

add_executable(boat_unit_async_event_store unit/test_async_event_store.cpp)
target_link_libraries(boat_unit_async_event_store PRIVATE boat_core boat_store Catch2::Catch2WithMain)
boat_discover_tests(boat_unit_async_event_store)

add_executable(boat_unit_effective_config
  unit/test_effective_config.cpp
  ${CMAKE_SOURCE_DIR}/src/gateway/grpc_gateway/effective_config.cpp)
target_include_directories(boat_unit_effective_config PRIVATE
  ${CMAKE_SOURCE_DIR}/src/gateway/grpc_gateway)
target_link_libraries(boat_unit_effective_config PRIVATE Catch2::Catch2WithMain)
boat_discover_tests(boat_unit_effective_config)

add_executable(boat_unit_subscriber_queue unit/test_subscriber_queue.cpp)
target_include_directories(boat_unit_subscriber_queue PRIVATE
  ${CMAKE_SOURCE_DIR}/src/gateway/grpc_gateway)
target_link_libraries(boat_unit_subscriber_queue PRIVATE Catch2::Catch2WithMain)
boat_discover_tests(boat_unit_subscriber_queue)

add_executable(boat_unit_ipc_channel_selector unit/test_ipc_channel_selector.cpp)
target_link_libraries(boat_unit_ipc_channel_selector PRIVATE boat_ipc Catch2::Catch2WithMain)
boat_discover_tests(boat_unit_ipc_channel_selector)

add_executable(boat_unit_replay_engine unit/test_replay_engine.cpp)
target_include_directories(boat_unit_replay_engine PRIVATE ${CMAKE_SOURCE_DIR}/src)
target_link_libraries(boat_unit_replay_engine PRIVATE boat_core boat_store boat_replay Catch2::Catch2WithMain)
boat_discover_tests(boat_unit_replay_engine)

add_executable(
  boat_integration_gateway
  integration/test_gateway.cpp
  ../gateway/grpc_gateway/pdu_service_impl.cpp
  ../gateway/grpc_gateway/simulation_service_impl.cpp
  ../gateway/grpc_gateway/scenario_service_impl.cpp
  ../gateway/grpc_gateway/signal_service_impl.cpp
  ../gateway/grpc_gateway/plugin_service_impl.cpp
  ../gateway/grpc_gateway/node_plugin_service_impl.cpp
  ../gateway/grpc_gateway/can_tp_service_impl.cpp
  ../gateway/grpc_gateway/frame_service_impl.cpp
  ../gateway/grpc_gateway/frame_sink.cpp
  ../gateway/grpc_gateway/bus_service_impl.cpp
  ../gateway/grpc_gateway/debug_service_impl.cpp
  ../gateway/grpc_gateway/effective_config.cpp
  ../gateway/grpc_gateway/fault_service_impl.cpp
  ../gateway/grpc_gateway/metrics_service_impl.cpp
)
target_include_directories(boat_integration_gateway PRIVATE
  ${CMAKE_SOURCE_DIR}/src
  ${CMAKE_SOURCE_DIR}/src/hil
  ${BOAT_PROTO_GENERATED_DIR}
)
target_link_libraries(
  boat_integration_gateway
  PRIVATE
    boat_core
    boat_hil
    boat_store
    boat_replay
    boat_proto_generated
    grpc++
    protobuf::libprotobuf
    Catch2::Catch2WithMain
)
if(TARGET pdu_router)
  add_dependencies(boat_integration_gateway pdu_router)
  target_compile_definitions(boat_integration_gateway PRIVATE
    PDU_ROUTER_SO="$<TARGET_FILE:pdu_router>")
endif()
if(TARGET can_tp)
  add_dependencies(boat_integration_gateway can_tp)
  target_compile_definitions(boat_integration_gateway PRIVATE
    CAN_TP_SO="$<TARGET_FILE:can_tp>")
endif()
boat_discover_tests(boat_integration_gateway)

add_executable(boat_integration_ipc_control_transport integration/test_ipc_control_transport.cpp)
target_include_directories(boat_integration_ipc_control_transport PRIVATE ${CMAKE_SOURCE_DIR}/src ${BOAT_PROTO_GENERATED_DIR})
target_link_libraries(
  boat_integration_ipc_control_transport
  PRIVATE
    boat_ipc
    boat_proto_generated
    Catch2::Catch2WithMain
)
boat_discover_tests(boat_integration_ipc_control_transport)

add_executable(
  boat_integration_replay_service
  integration/test_replay_service.cpp
  ../gateway/grpc_gateway/replay_service_impl.cpp
)
target_include_directories(boat_integration_replay_service PRIVATE
  ${CMAKE_SOURCE_DIR}/src
  ${CMAKE_SOURCE_DIR}/src/hil
  ${BOAT_PROTO_GENERATED_DIR}
)
target_link_libraries(
  boat_integration_replay_service
  PRIVATE
    boat_core
    boat_hil
    boat_store
    boat_replay
    boat_proto_generated
    grpc++
    protobuf::libprotobuf
    Catch2::Catch2WithMain
)
boat_discover_tests(boat_integration_replay_service)

# Trace mapping lifetimes across concurrent GetTrace/StreamTrace/StopReplay
# RPCs (issue #63) -- needs both service impls against one real trace store.
add_executable(
  boat_integration_trace_service
  integration/test_trace_service.cpp
  ../gateway/grpc_gateway/trace_service_impl.cpp
  ../gateway/grpc_gateway/replay_service_impl.cpp
)
target_include_directories(boat_integration_trace_service PRIVATE
  ${CMAKE_SOURCE_DIR}/src
  ${CMAKE_SOURCE_DIR}/src/hil
  ${BOAT_PROTO_GENERATED_DIR}
)
target_link_libraries(
  boat_integration_trace_service
  PRIVATE
    boat_core
    boat_hil
    boat_store
    boat_replay
    boat_proto_generated
    grpc++
    protobuf::libprotobuf
    Catch2::Catch2WithMain
)
boat_discover_tests(boat_integration_trace_service)

# A plugin reporting the previous ABI version, so the loader's rejection path
# can be tested rather than assumed.
add_library(stale_abi_plugin MODULE fixtures/stale_abi_plugin.cpp)
target_include_directories(stale_abi_plugin PRIVATE ${CMAKE_SOURCE_DIR}/sdk/cpp/include)
set_target_properties(stale_abi_plugin PROPERTIES PREFIX "" SUFFIX ".so")

# Records the iface/flags of frames delivered to a simulation-scoped plugin,
# for the #70 regression test.
add_library(frame_meta_plugin MODULE fixtures/frame_meta_plugin.cpp)
target_include_directories(frame_meta_plugin PRIVATE ${CMAKE_SOURCE_DIR}/sdk/cpp/include)
set_target_properties(frame_meta_plugin PROPERTIES PREFIX "" SUFFIX ".so")
add_dependencies(boat_integration_gateway frame_meta_plugin)
target_compile_definitions(boat_integration_gateway PRIVATE
  FRAME_META_PLUGIN_SO="$<TARGET_FILE:frame_meta_plugin>")

add_executable(boat_unit_can_tp_tick unit/test_can_tp_tick.cpp)
target_include_directories(boat_unit_can_tp_tick PRIVATE
  ${CMAKE_SOURCE_DIR}/src ${CMAKE_SOURCE_DIR}/sdk/cpp/include)
target_link_libraries(boat_unit_can_tp_tick PRIVATE boat_core Catch2::Catch2WithMain)
if(TARGET can_tp)
  add_dependencies(boat_unit_can_tp_tick can_tp)
  target_compile_definitions(boat_unit_can_tp_tick PRIVATE
    CAN_TP_SO="$<TARGET_FILE:can_tp>")
endif()
boat_discover_tests(boat_unit_can_tp_tick)

add_executable(boat_unit_plugin_time_source unit/test_plugin_time_source.cpp)
target_include_directories(boat_unit_plugin_time_source PRIVATE
  ${CMAKE_SOURCE_DIR}/src ${CMAKE_SOURCE_DIR}/src/hil)
target_link_libraries(boat_unit_plugin_time_source PRIVATE boat_core boat_hil Catch2::Catch2WithMain)
if(TARGET probe)
  add_dependencies(boat_unit_plugin_time_source probe)
  target_compile_definitions(boat_unit_plugin_time_source PRIVATE
    PROBE_SO="$<TARGET_FILE:probe>")
endif()
add_dependencies(boat_unit_plugin_time_source stale_abi_plugin)
target_compile_definitions(boat_unit_plugin_time_source PRIVATE
  STALE_ABI_PLUGIN_SO="$<TARGET_FILE:stale_abi_plugin>")
boat_discover_tests(boat_unit_plugin_time_source)

# Plugin crash attribution (#19). crash_plugin crashes on demand (test-only,
# no install rule); boat_crash_host loads it with the gateway's fatal-signal
# handler installed, and is the process the integration test expects to die.
add_library(crash_plugin MODULE fixtures/crash_plugin.cpp)
target_include_directories(crash_plugin PRIVATE ${CMAKE_SOURCE_DIR}/sdk/cpp/include)
find_package(Threads REQUIRED)
target_link_libraries(crash_plugin PRIVATE Threads::Threads)
set_target_properties(crash_plugin PROPERTIES PREFIX "" SUFFIX ".so")

add_executable(boat_crash_host fixtures/crash_host.cpp)
target_link_libraries(boat_crash_host PRIVATE boat_core)

add_executable(boat_unit_plugin_crash_attribution unit/test_plugin_crash_attribution.cpp)
target_link_libraries(boat_unit_plugin_crash_attribution PRIVATE
  boat_core ${CMAKE_DL_LIBS} Catch2::Catch2WithMain)
add_dependencies(boat_unit_plugin_crash_attribution crash_plugin stale_abi_plugin)
target_compile_definitions(boat_unit_plugin_crash_attribution PRIVATE
  CRASH_PLUGIN_SO="$<TARGET_FILE:crash_plugin>"
  STALE_ABI_PLUGIN_SO="$<TARGET_FILE:stale_abi_plugin>")
boat_discover_tests(boat_unit_plugin_crash_attribution)

add_executable(boat_integration_plugin_crash integration/test_plugin_crash.cpp)
target_link_libraries(boat_integration_plugin_crash PRIVATE Catch2::Catch2WithMain)
add_dependencies(boat_integration_plugin_crash crash_plugin boat_crash_host)
target_compile_definitions(boat_integration_plugin_crash PRIVATE
  CRASH_PLUGIN_SO="$<TARGET_FILE:crash_plugin>"
  CRASH_HOST_BIN="$<TARGET_FILE:boat_crash_host>")
if(TARGET boat_gateway)
  add_dependencies(boat_integration_plugin_crash boat_gateway)
  target_compile_definitions(boat_integration_plugin_crash PRIVATE
    BOAT_GATEWAY_BIN="$<TARGET_FILE:boat_gateway>")
endif()
boat_discover_tests(boat_integration_plugin_crash)

add_executable(boat_unit_tick_authority unit/test_tick_authority.cpp)
target_include_directories(boat_unit_tick_authority PRIVATE ${CMAKE_SOURCE_DIR}/src/hil)
target_link_libraries(boat_unit_tick_authority PRIVATE boat_hil boat_core Catch2::Catch2WithMain)
boat_discover_tests(boat_unit_tick_authority)

add_executable(boat_unit_tick_timer unit/test_tick_timer.cpp)
target_include_directories(boat_unit_tick_timer PRIVATE ${CMAKE_SOURCE_DIR}/src/hil)
target_link_libraries(boat_unit_tick_timer PRIVATE boat_hil boat_core Catch2::Catch2WithMain)
boat_discover_tests(boat_unit_tick_timer)

add_executable(boat_unit_pdu_tick_units unit/test_pdu_tick_units.cpp)
target_include_directories(boat_unit_pdu_tick_units PRIVATE
  ${CMAKE_SOURCE_DIR}/src
  ${CMAKE_SOURCE_DIR}/src/hil
)
target_link_libraries(boat_unit_pdu_tick_units PRIVATE boat_core boat_hil Catch2::Catch2WithMain)
if(TARGET pdu_router)
  add_dependencies(boat_unit_pdu_tick_units pdu_router)
  target_compile_definitions(boat_unit_pdu_tick_units PRIVATE
    PDU_ROUTER_SO="$<TARGET_FILE:pdu_router>")
endif()
boat_discover_tests(boat_unit_pdu_tick_units)

add_executable(boat_determinism_seed determinism/test_seed.cpp)
target_link_libraries(boat_determinism_seed PRIVATE boat_core Catch2::Catch2WithMain)
boat_discover_tests(boat_determinism_seed)

# System-level counterpart to boat_determinism_seed: drives a real trace through
# ReplayController -> FrameSink -> CanBusRegistry -> driver, twice, and compares
# what reached the wire. Unlike the seed test it links the replay and HIL stacks,
# so it can actually observe the property README.md advertises.
add_executable(
  boat_determinism_replay
  determinism/test_replay_determinism.cpp
  ../gateway/grpc_gateway/frame_sink.cpp
)
target_include_directories(boat_determinism_replay PRIVATE
  ${CMAKE_SOURCE_DIR}/src
  ${CMAKE_SOURCE_DIR}/src/hil
  ${BOAT_PROTO_GENERATED_DIR}
)
target_link_libraries(
  boat_determinism_replay
  PRIVATE
    boat_core
    boat_hil
    boat_store
    boat_replay
    boat_proto_generated
    protobuf::libprotobuf
    Catch2::Catch2WithMain
)
boat_discover_tests(boat_determinism_replay)

add_executable(boat_unit_ethernet_bus_registry unit/test_ethernet_bus_registry.cpp)
target_include_directories(boat_unit_ethernet_bus_registry PRIVATE ${CMAKE_SOURCE_DIR}/src/hil)
target_link_libraries(boat_unit_ethernet_bus_registry PRIVATE boat_hil Catch2::Catch2WithMain)
boat_discover_tests(boat_unit_ethernet_bus_registry)

add_executable(boat_unit_pdu_router unit/test_pdu_router.cpp)
target_include_directories(boat_unit_pdu_router PRIVATE ${CMAKE_SOURCE_DIR}/src/hil)
target_link_libraries(boat_unit_pdu_router PRIVATE boat_hil Catch2::Catch2WithMain)
boat_discover_tests(boat_unit_pdu_router)

add_executable(boat_unit_can_health unit/test_can_health.cpp)
target_include_directories(boat_unit_can_health PRIVATE ${CMAKE_SOURCE_DIR}/src/hil)
target_link_libraries(boat_unit_can_health PRIVATE boat_hil Catch2::Catch2WithMain)
boat_discover_tests(boat_unit_can_health)

add_executable(boat_unit_can_bus_registry unit/test_can_bus_registry.cpp)
target_include_directories(boat_unit_can_bus_registry PRIVATE
  ${CMAKE_SOURCE_DIR}/src ${CMAKE_SOURCE_DIR}/src/hil)
target_link_libraries(boat_unit_can_bus_registry PRIVATE boat_hil boat_core Catch2::Catch2WithMain)
boat_discover_tests(boat_unit_can_bus_registry)

add_executable(boat_hil_smoke hil/test_hil.cpp)
target_link_libraries(boat_hil_smoke PRIVATE boat_hil Catch2::Catch2WithMain)
boat_discover_tests(boat_hil_smoke)

add_executable(boat_hil_ethernet hil/test_ethernet_hil.cpp)
target_include_directories(boat_hil_ethernet PRIVATE ${CMAKE_SOURCE_DIR}/src/hil)
target_link_libraries(boat_hil_ethernet PRIVATE boat_hil Catch2::Catch2WithMain)
boat_discover_tests(boat_hil_ethernet)

# ── TCP Plugin ───────────────────────────────────────────────────────────────

add_executable(boat_unit_tcp_options
  unit/test_tcp_options.cpp
  ../plugins/tcp/tcp_plugin.cpp
)
target_include_directories(boat_unit_tcp_options PRIVATE
    ${CMAKE_SOURCE_DIR}/src/plugins/tcp
    ${CMAKE_SOURCE_DIR}/sdk/cpp/include
)
target_compile_features(boat_unit_tcp_options PRIVATE cxx_std_20)
target_link_libraries(boat_unit_tcp_options PRIVATE Catch2::Catch2WithMain Threads::Threads)
boat_discover_tests(boat_unit_tcp_options)

add_executable(boat_unit_tcp_state
  unit/test_tcp_state.cpp
  ../plugins/tcp/tcp_plugin.cpp
)
target_include_directories(boat_unit_tcp_state PRIVATE
    ${CMAKE_SOURCE_DIR}/src/plugins/tcp
    ${CMAKE_SOURCE_DIR}/sdk/cpp/include
)
target_compile_features(boat_unit_tcp_state PRIVATE cxx_std_20)
target_link_libraries(boat_unit_tcp_state PRIVATE Catch2::Catch2WithMain Threads::Threads)
boat_discover_tests(boat_unit_tcp_state)

# ── Test Harness (P6) ────────────────────────────────────────────────────────

add_boat_test(boat_test_harness unit/test_harness_example.cpp)
