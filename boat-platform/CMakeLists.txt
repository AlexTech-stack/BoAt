# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

cmake_minimum_required(VERSION 3.24)

project(boat_platform VERSION 0.1.1 LANGUAGES C CXX)

# The gateway is Linux-only by design: SocketCAN/vcan, AF_PACKET, timerfd and
# dlopen'd .so plugins have no Windows/macOS equivalent here, and no port is
# planned. Fail at configure time with a clear message rather than ~hundreds
# of objects later on a missing <linux/can.h>. The Python clients are
# OS-agnostic and are not built by this project.
if(NOT CMAKE_SYSTEM_NAME STREQUAL "Linux")
  message(FATAL_ERROR
    "BoAt's gateway builds on Linux only (CMAKE_SYSTEM_NAME is "
    "'${CMAKE_SYSTEM_NAME}'). Run the gateway on a Linux host and use the "
    "OS-agnostic Python SDK / CLI / Admin GUI from this machine instead. "
    "See boat-platform/README.md#supported-platforms.")
endif()

list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/cmake")

include(CTest)
include(GNUInstallDirs)
include(BoAtPlugin)
include(BoAtTest)
include(BoAtProto)
# NOTE: include(Packaging) is deliberately at the BOTTOM of this file, not here.
# See cmake/Packaging.cmake for why -- CPack's config is written to the shared
# top-level binary dir and the last include(CPack) wins, so ours has to run
# after every dependency's.
include(FetchContent)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

option(BOAT_ENABLE_PROTO "Enable protobuf generation checks" ON)
option(BOAT_BUILD_TESTS "Build test targets" ON)
option(BOAT_HIL_ENABLED "Enable HIL hardware tests" OFF)

# iceoryx_hoofs requires sys/acl.h (libacl1-dev). If it is not installed
# system-wide, download and extract the package into the build tree.
if(NOT EXISTS "/usr/include/sys/acl.h")
  set(_acl_vendor "${CMAKE_BINARY_DIR}/vendor/libacl")
  if(NOT EXISTS "${_acl_vendor}/usr/include/sys/acl.h")
    find_program(_apt_get apt-get REQUIRED)
    message(STATUS "sys/acl.h not found — downloading libacl1-dev into build tree")
    # MAKE_DIRECTORY first: execute_process fails outright if WORKING_DIRECTORY
    # does not exist, which is every clean checkout. This fallback had therefore
    # never worked on a fresh machine -- and because the result was checked but
    # never reported, configure succeeded and the build died ~1700 objects later
    # on `iceoryx_platform/acl.hpp:20: fatal error: sys/acl.h`.
    file(MAKE_DIRECTORY "${_acl_vendor}")
    execute_process(
      COMMAND ${_apt_get} download libacl1-dev
      WORKING_DIRECTORY "${_acl_vendor}"
      RESULT_VARIABLE _acl_dl_result
      OUTPUT_VARIABLE _acl_dl_output
      ERROR_VARIABLE  _acl_dl_output)
    if(_acl_dl_result EQUAL 0)
      file(GLOB _acl_deb "${_acl_vendor}/libacl1-dev*.deb")
      execute_process(COMMAND dpkg-deb -x "${_acl_deb}" "${_acl_vendor}")
    else()
      message(WARNING "apt-get download libacl1-dev failed: ${_acl_dl_output}")
    endif()
  endif()
  if(EXISTS "${_acl_vendor}/usr/include/sys/acl.h")
    include_directories(SYSTEM "${_acl_vendor}/usr/include")
    link_directories("${_acl_vendor}/usr/lib/x86_64-linux-gnu")
  else()
    # Fail here rather than thousands of compile steps later with a missing
    # header that says nothing about the cause.
    message(FATAL_ERROR
      "iceoryx_hoofs needs sys/acl.h and it could not be found or vendored.\n"
      "Install it:  sudo apt-get install -y libacl1-dev")
  endif()
endif()

set(gRPC_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(gRPC_BUILD_CSHARP_EXT OFF CACHE BOOL "" FORCE)
set(IOX2_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(protobuf_BUILD_TESTS OFF CACHE BOOL "" FORCE)
# gRPC_BUILD_TESTS does not reach its bundled third_party/re2, whose own
# RE2_BUILD_TESTING defaults to ON. include(CTest) above enables testing
# globally, so re2's add_test() calls land in *our* CTest project -- while
# EXCLUDE_FROM_ALL on the gRPC subdirectory means their binaries are never
# built. The result was 22 phantom "Not Run" failures in `ctest`.
set(RE2_BUILD_TESTING OFF CACHE BOOL "" FORCE)
# Tell gRPC to use find_package(protobuf) instead of its bundled third_party copy.
# Combined with OVERRIDE_FIND_PACKAGE on the protobuf FetchContent below, this
# redirects gRPC to the already-fetched protobuf and prevents duplicate gtest targets.
set(gRPC_PROTOBUF_PROVIDER "package" CACHE STRING "" FORCE)

FetchContent_Declare(
  spdlog
  GIT_REPOSITORY https://github.com/gabime/spdlog.git
  GIT_TAG v1.14.1
)

FetchContent_Declare(
  protobuf
  GIT_REPOSITORY https://github.com/protocolbuffers/protobuf.git
  GIT_TAG v31.1
  OVERRIDE_FIND_PACKAGE
)

FetchContent_Declare(
  gRPC
  GIT_REPOSITORY https://github.com/grpc/grpc.git
  GIT_TAG v1.75.0
)

FetchContent_Declare(
  iceoryx2
  GIT_REPOSITORY https://github.com/eclipse-iceoryx/iceoryx2.git
  GIT_TAG v0.4.1
)

FetchContent_Declare(
  sqlite3
  URL https://www.sqlite.org/2024/sqlite-amalgamation-3460000.zip
)

FetchContent_Declare(
  tomlplusplus
  GIT_REPOSITORY https://github.com/marzer/tomlplusplus.git
  GIT_TAG v3.4.0
)

# protobuf must be available before gRPC so that gRPC's find_package(protobuf)
# is redirected to our FetchContent copy via OVERRIDE_FIND_PACKAGE.
FetchContent_MakeAvailable(spdlog protobuf tomlplusplus)

# The gRPC and iceoryx2 patches below edit files *inside* FetchContent's git
# checkouts. ExternalProject's update step stashes local edits, checks out the
# pinned tag, then pops the stash -- under every GIT_REMOTE_UPDATE_STRATEGY, so
# PATCH_COMMAND does not escape it either. That works while the tag is
# unchanged, but the first tag bump that touches a patched file makes the pop
# conflict, and every existing build dir then fails to configure with "Failed
# to unstash changes in .../grpc-src" (the v1.65 -> v1.75 bump did exactly
# that). So reset each patched file to its committed state *before*
# FetchContent updates the checkout, and let the code below re-apply the patch.
find_package(Git REQUIRED)
function(boat_unpatch_dep name)
  string(TOUPPER "${name}" _upper)
  if(FETCHCONTENT_SOURCE_DIR_${_upper})
    return()  # A user-supplied tree: no update step runs, and it isn't ours to reset.
  endif()
  string(TOLOWER "${name}" _lower)
  set(_src "${FETCHCONTENT_BASE_DIR}/${_lower}-src")
  foreach(_rel IN LISTS ARGN)
    if(NOT EXISTS "${_src}/${_rel}")
      continue()  # Fresh build dir: nothing checked out yet, nothing to reset.
    endif()
    get_filename_component(_dir "${_src}/${_rel}" DIRECTORY)
    get_filename_component(_file "${_rel}" NAME)
    # `git -C <dir>` resolves the repo that owns <dir>, so a file inside a
    # submodule (gRPC's third_party/zlib) is reset in that submodule.
    execute_process(
      COMMAND "${GIT_EXECUTABLE}" -C "${_dir}" checkout -- "${_file}"
      RESULT_VARIABLE _rc ERROR_VARIABLE _err)
    if(NOT _rc EQUAL 0)
      message(WARNING "Could not reset patched dependency file ${_src}/${_rel} "
                      "before updating it: ${_err}")
    endif()
  endforeach()
endfunction()

# cmake/protobuf.cmake is no longer patched (see below), but build dirs last
# configured against gRPC v1.65 still carry that edit and must have it reset
# before they can update.
boat_unpatch_dep(gRPC cmake/protobuf.cmake third_party/zlib/CMakeLists.txt)
boat_unpatch_dep(iceoryx2 iceoryx2-ffi/c/CMakeLists.txt)

# gRPC <= v1.65 needed cmake/protobuf.cmake patched: it read the well-known
# .proto include dir off protobuf::libprotoc, which has none. v1.75 reads it
# off ${_gRPC_PROTOBUF_LIBRARIES} instead, so the patch's search string no
# longer exists and it had silently become a no-op -- removed rather than kept
# as dead code.
FetchContent_GetProperties(grpc)
if(NOT grpc_POPULATED)
  FetchContent_Populate(grpc)
endif()

# gRPC's bundled third_party/zlib registers its example programs as CTest tests
# unconditionally -- there is no option to disable them, unlike re2's
# RE2_BUILD_TESTING above. Under EXCLUDE_FROM_ALL their binaries are never
# built, so they surfaced as 2 phantom "Not Run" failures in `ctest`. Strip the
# add_test() calls; the executables themselves are harmless. The file is reset
# to upstream by boat_unpatch_dep() above, so this applies to a clean copy.
set(_zlib_cmake "${grpc_SOURCE_DIR}/third_party/zlib/CMakeLists.txt")
if(EXISTS "${_zlib_cmake}")
  file(READ "${_zlib_cmake}" _zlib_content)
  string(REPLACE "add_test(example example)" "" _zlib_content "${_zlib_content}")
  string(REPLACE "add_test(example64 example64)" "" _zlib_content "${_zlib_content}")
  file(WRITE "${_zlib_cmake}" "${_zlib_content}")
endif()

add_subdirectory("${grpc_SOURCE_DIR}" "${grpc_BINARY_DIR}" EXCLUDE_FROM_ALL)

# iceoryx2 hardcodes WORKING_DIRECTORY ${CMAKE_SOURCE_DIR} in its cargo build
# step, which resolves to our project root when used via FetchContent instead of
# the iceoryx2 workspace root where Cargo.toml lives. Patch before add_subdirectory.
FetchContent_GetProperties(iceoryx2)
if(NOT iceoryx2_POPULATED)
  FetchContent_Populate(iceoryx2)
endif()
set(_iox2_c_cmake "${iceoryx2_SOURCE_DIR}/iceoryx2-ffi/c/CMakeLists.txt")
file(READ "${_iox2_c_cmake}" _iox2_content)
# Fix 1: wrong WORKING_DIRECTORY when used via FetchContent
string(REPLACE
    "WORKING_DIRECTORY \${CMAKE_SOURCE_DIR}"
    "WORKING_DIRECTORY \${CMAKE_CURRENT_SOURCE_DIR}/../.."
    _iox2_content "${_iox2_content}")
# Fix 2: bindgen can't find stddef.h without the clang package — point it at the
# GCC built-in headers that are already present on the system.
execute_process(
    COMMAND ${CMAKE_C_COMPILER} -print-search-dirs
    OUTPUT_VARIABLE _gcc_search_dirs OUTPUT_STRIP_TRAILING_WHITESPACE)
string(REGEX MATCH "install: ([^\n]+)" _ "${_gcc_search_dirs}")
set(_gcc_install_dir "${CMAKE_MATCH_1}")
set(_gcc_include_dir "${_gcc_install_dir}include")
# Find cargo — it may live in ~/.cargo/bin which is not in the system PATH
find_program(_cargo_exe cargo HINTS "$ENV{HOME}/.cargo/bin" "/root/.cargo/bin")
if(NOT _cargo_exe)
  message(FATAL_ERROR "cargo not found; install Rust via rustup first")
endif()
message(STATUS "Found cargo: ${_cargo_exe}")
if(EXISTS "${_gcc_include_dir}/stddef.h")
    string(REPLACE
        "COMMAND cargo build"
        "COMMAND \${CMAKE_COMMAND} -E env \"BINDGEN_EXTRA_CLANG_ARGS=-I${_gcc_include_dir}\" ${_cargo_exe} build"
        _iox2_content "${_iox2_content}")
else()
    string(REPLACE
        "COMMAND cargo build"
        "COMMAND ${_cargo_exe} build"
        _iox2_content "${_iox2_content}")
endif()
file(WRITE "${_iox2_c_cmake}" "${_iox2_content}")
add_subdirectory("${iceoryx2_SOURCE_DIR}" "${iceoryx2_BINARY_DIR}" EXCLUDE_FROM_ALL)

FetchContent_GetProperties(sqlite3)
if(NOT sqlite3_POPULATED)
  FetchContent_Populate(sqlite3)
endif()

add_library(sqlite3 STATIC "${sqlite3_SOURCE_DIR}/sqlite3.c")
target_include_directories(sqlite3 PUBLIC "${sqlite3_SOURCE_DIR}")
target_compile_definitions(sqlite3 PUBLIC SQLITE_THREADSAFE=1 SQLITE_ENABLE_WAL)

if(NOT TARGET tomlplusplus::tomlplusplus)
  add_library(tomlplusplus INTERFACE)
  target_include_directories(tomlplusplus INTERFACE "${tomlplusplus_SOURCE_DIR}/include")
  add_library(tomlplusplus::tomlplusplus ALIAS tomlplusplus)
endif()

if(BOAT_ENABLE_PROTO)
  boat_register_proto_directory("${CMAKE_CURRENT_SOURCE_DIR}/proto/boat/v1")
endif()

add_subdirectory(src/core)
add_subdirectory(sdk/cpp)
add_subdirectory(src/plugins)
add_subdirectory(src/gateway)
add_subdirectory(src/store)
add_subdirectory(src/replay)
add_subdirectory(src/ipc)
add_subdirectory(src/hil)

if(BOAT_BUILD_TESTS AND BUILD_TESTING)
  add_subdirectory(src/tests)
endif()

# Last, on purpose: must run after every add_subdirectory() above so that
# BoAt's CPack configuration is the one written to CPackConfig.cmake rather
# than a fetched dependency's. See cmake/Packaging.cmake.
include(Packaging)
