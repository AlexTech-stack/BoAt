cmake_minimum_required(VERSION 3.24)

project(boat_platform VERSION 0.1.0 LANGUAGES CXX)

list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/cmake")

include(CTest)
include(GNUInstallDirs)
include(BoAtPlugin)
include(BoAtProto)
include(Packaging)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

option(BOAT_ENABLE_PROTO "Enable protobuf generation checks" ON)
option(BOAT_BUILD_TESTS "Build test targets" ON)

add_subdirectory(core)
add_subdirectory(gateway)
add_subdirectory(store)
add_subdirectory(ipc)

if(BOAT_ENABLE_PROTO)
  boat_register_proto_directory("${CMAKE_CURRENT_SOURCE_DIR}/proto/boat/v1")
endif()

if(BOAT_BUILD_TESTS AND BUILD_TESTING)
  add_subdirectory(tests)
endif()
