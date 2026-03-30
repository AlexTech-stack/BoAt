add_executable(boat_gateway main.cpp)
target_link_libraries(boat_gateway PRIVATE boat_core)
target_compile_features(boat_gateway PRIVATE cxx_std_20)

install(TARGETS boat_gateway RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})
