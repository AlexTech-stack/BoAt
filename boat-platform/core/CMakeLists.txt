add_library(boat_core STATIC
  scheduler/sim_clock.cpp
)

target_include_directories(boat_core PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}")
target_compile_features(boat_core PUBLIC cxx_std_20)
