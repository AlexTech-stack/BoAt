# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

include_guard(GLOBAL)

# ── boat_discover_tests ────────────────────────────────────────────────────────
#
# catch_discover_tests() plus the one property every BoAt test needs:
# SKIP_RETURN_CODE. Catch2 exits 4 when every test case in the run was skipped,
# which CTest otherwise reports as a plain failure -- so the HIL tests, which
# skip by design unless BOAT_HIL_ENABLED is set, showed up as red entries in an
# otherwise green run. Use this instead of catch_discover_tests() directly, so a
# future SKIP() is reported as a skip rather than becoming a mystery failure.
#
# Extra arguments are appended inside the PROPERTIES list, so pass bare
# key/value pairs (e.g. `boat_discover_tests(my_test TIMEOUT 300)`), never
# another PROPERTIES keyword -- a second one would be read as a property name.
#
function(boat_discover_tests target_name)
  catch_discover_tests(${target_name} PROPERTIES SKIP_RETURN_CODE 4 ${ARGN})
endfunction()

# ── add_boat_test ──────────────────────────────────────────────────────────────
#
# Creates a Catch2 test executable with the test harness.
#
# Usage:
#   add_boat_test(boat_test_<name> test_<name>.cpp)
#
# Links: boat_core boat_hil Catch2::Catch2WithMain
# Sets include dirs for: ${CMAKE_SOURCE_DIR}, ${CMAKE_SOURCE_DIR}/src,
#   ${CMAKE_SOURCE_DIR}/src/hil, ${CMAKE_SOURCE_DIR}/sdk/cpp/include
#
function(add_boat_test target_name)
  add_executable(${target_name} ${ARGN})
  target_compile_features(${target_name} PRIVATE cxx_std_20)
  target_include_directories(${target_name} PRIVATE
    ${CMAKE_SOURCE_DIR}
    ${CMAKE_SOURCE_DIR}/src
    ${CMAKE_SOURCE_DIR}/src/hil
    ${CMAKE_SOURCE_DIR}/sdk/cpp/include
  )
  target_link_libraries(${target_name} PRIVATE
    boat_core
    boat_hil
    Catch2::Catch2WithMain
  )
  boat_discover_tests(${target_name})
endfunction()
