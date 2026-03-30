#pragma once

#include <cstdint>
#include <string>
#include <unordered_map>
#include <vector>

#include "boat/plugin.h"

namespace boat::core {

struct PluginHandle {
  void* dl_handle;
  BoatPlugin* plugin;
  std::string name;
  std::uint32_t abi_version;
  boat_plugin_destroy_fn destroy_fn;
};

class PluginManager {
 public:
  PluginHandle Load(const std::string& so_path, const std::string& config_json);
  void Unload(const std::string& name);
  void TickAll(std::uint64_t tick);
  void ShutdownAll();
  [[nodiscard]] std::vector<std::string> List() const;

 private:
  std::unordered_map<std::string, PluginHandle> plugins_;
};

}  // namespace boat::core
