#include <catch2/catch_test_macros.hpp>

#include "plugin/plugin_manager.h"

TEST_CASE("PluginManager safe behavior with no plugins", "[unit][plugin_manager]") {
  boat::core::PluginManager manager;

  SECTION("List is empty on initialization") { REQUIRE(manager.List().empty()); }

  SECTION("Unload unknown name is safe") {
    manager.Unload("does-not-exist");
    REQUIRE(manager.List().empty());
  }

  SECTION("TickAll with zero plugins is a no-op") {
    manager.TickAll(123);
    REQUIRE(manager.List().empty());
  }
}
