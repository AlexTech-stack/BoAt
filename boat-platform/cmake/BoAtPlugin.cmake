function(add_boat_plugin target_name)
  add_library(${target_name} MODULE ${ARGN})
  target_compile_features(${target_name} PRIVATE cxx_std_20)
  set_target_properties(${target_name} PROPERTIES
    PREFIX ""
    OUTPUT_NAME "${target_name}"
  )
  install(TARGETS ${target_name}
    LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}/boat/plugins
  )

  # Optional config-schema sidecar (<target_name>.schema.json, next to this
  # CMakeLists.txt) -- describes this plugin's JSON config (the ?{...}
  # appended to its .so path) as {"key": {"type","default","help",...}},
  # for admin_gui's New/Edit Instance dialog to build one field per key.
  # A compiled .so has nothing to import/introspect the way a node
  # script's build_parser() does, so this is a static, hand-maintained
  # equivalent -- kept in sync with the plugin's own config parsing by
  # whoever edits it, same as a docstring. A plugin with no sidecar file
  # here just isn't offered per-key fields; admin_gui falls back to its
  # existing flat JSON config text field for it. Copied next to the built
  # .so (mirrors the install() rule for the .so itself, so wherever
  # BOAT_NODE_PLUGINS points, the schema travels with it) and installed
  # alongside it for packaged (cpack) deployments too.
  set(_schema_src "${CMAKE_CURRENT_SOURCE_DIR}/${target_name}.schema.json")
  if(EXISTS "${_schema_src}")
    add_custom_command(TARGET ${target_name} POST_BUILD
      COMMAND ${CMAKE_COMMAND} -E copy_if_different
              "${_schema_src}" "$<TARGET_FILE_DIR:${target_name}>/${target_name}.schema.json"
      COMMENT "Copying ${target_name}.schema.json next to ${target_name}"
    )
    install(FILES "${_schema_src}"
      DESTINATION ${CMAKE_INSTALL_LIBDIR}/boat/plugins
    )
  endif()
endfunction()
