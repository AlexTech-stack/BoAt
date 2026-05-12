add_library(boat_core STATIC
  scheduler/sim_clock.cpp
  scheduler/tick_scheduler.cpp
  signal/signal_router.cpp
  event/event_bus.cpp
  plugin/plugin_manager.cpp
  state/sim_state_machine.cpp
  determinism/determinism_engine.cpp
  fault/fault_injector.cpp
  scenario/scenario_loader.cpp
)

target_include_directories(boat_core PUBLIC
  "${CMAKE_CURRENT_SOURCE_DIR}"
  "${CMAKE_SOURCE_DIR}"
  "${CMAKE_SOURCE_DIR}/sdk/cpp/include"
)
target_compile_features(boat_core PUBLIC cxx_std_20)
target_link_libraries(boat_core PRIVATE ${CMAKE_DL_LIBS})
