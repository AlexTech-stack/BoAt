cmake_minimum_required(VERSION 3.24)

project(boat_platform VERSION 0.1.0 LANGUAGES CXX)

list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/cmake")

include(CTest)
include(GNUInstallDirs)
include(BoAtPlugin)
include(BoAtProto)
include(Packaging)
include(FetchContent)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

option(BOAT_ENABLE_PROTO "Enable protobuf generation checks" ON)
option(BOAT_BUILD_TESTS "Build test targets" ON)

set(gRPC_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(gRPC_BUILD_CSHARP_EXT OFF CACHE BOOL "" FORCE)
set(IOX2_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)

FetchContent_Declare(
  spdlog
  GIT_REPOSITORY https://github.com/gabime/spdlog.git
  GIT_TAG v1.14.1
)

FetchContent_Declare(
  protobuf
  GIT_REPOSITORY https://github.com/protocolbuffers/protobuf.git
  GIT_TAG v27.3
)

FetchContent_Declare(
  gRPC
  GIT_REPOSITORY https://github.com/grpc/grpc.git
  GIT_TAG v1.65.0
)

FetchContent_Declare(
  iceoryx2
  GIT_REPOSITORY https://github.com/eclipse-iceoryx/iceoryx2.git
  GIT_TAG v0.4.1
)

FetchContent_MakeAvailable(spdlog protobuf gRPC iceoryx2)

if(BOAT_ENABLE_PROTO)
  boat_register_proto_directory("${CMAKE_CURRENT_SOURCE_DIR}/proto/boat/v1")
endif()

add_subdirectory(core)
add_subdirectory(gateway)
add_subdirectory(store)
add_subdirectory(ipc)

if(BOAT_BUILD_TESTS AND BUILD_TESTING)
  add_subdirectory(tests)
endif()
