add_library(boat_hil STATIC
  can/socket_can_driver.cpp
  virtual/virtual_can_driver.cpp
  hil_bridge.cpp
)
target_include_directories(boat_hil PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}")
target_compile_features(boat_hil PUBLIC cxx_std_20)
target_link_libraries(boat_hil PUBLIC boat_core)
