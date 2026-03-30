function(boat_register_proto_directory proto_dir)
  if(NOT EXISTS "${proto_dir}")
    message(FATAL_ERROR "Proto directory does not exist: ${proto_dir}")
  endif()

  file(GLOB proto_files "${proto_dir}/*.proto")
  if(proto_files STREQUAL "")
    message(FATAL_ERROR "No .proto files found in: ${proto_dir}")
  endif()

  add_custom_target(boat_proto_contracts ALL
    COMMAND ${CMAKE_COMMAND} -E echo "Verified proto contracts in ${proto_dir}"
    DEPENDS ${proto_files}
    COMMENT "Checking proto contract availability"
    VERBATIM
  )
endfunction()
