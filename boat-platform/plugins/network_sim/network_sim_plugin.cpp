#include "network_sim_plugin.h"

#include <cstdio>
#include <string>

namespace {

double parse_double_value(const std::string& json, const char* key, double fallback) {
  const std::string needle = std::string("\"") + key + "\"";
  const std::size_t key_pos = json.find(needle);
  if (key_pos == std::string::npos) {
    return fallback;
  }
  const std::size_t colon_pos = json.find(':', key_pos + needle.size());
  if (colon_pos == std::string::npos) {
    return fallback;
  }

  double parsed = fallback;
  if (std::sscanf(json.c_str() + static_cast<long long>(colon_pos + 1), " %lf", &parsed) == 1) {
    return parsed;
  }
  return fallback;
}

NetworkProtocol parse_protocol(const std::string& json) {
  if (json.find("\"protocol\":\"LIN\"") != std::string::npos ||
      json.find("\"protocol\": \"LIN\"") != std::string::npos) {
    return NetworkProtocol::LIN;
  }
  if (json.find("\"protocol\":\"ETHERNET\"") != std::string::npos ||
      json.find("\"protocol\": \"ETHERNET\"") != std::string::npos) {
    return NetworkProtocol::ETHERNET;
  }
  return NetworkProtocol::CAN;
}

int network_initialize(void* ctx, const char* config_json) {
  auto* plugin = static_cast<NetworkSimPlugin*>(ctx);
  if (plugin == nullptr) {
    return -1;
  }
  plugin->protocol = NetworkProtocol::CAN;
  plugin->bus_load_percent = 25.0;
  plugin->frame_count = 0;

  const std::string config = config_json == nullptr ? std::string{} : std::string(config_json);
  plugin->protocol = parse_protocol(config);
  plugin->bus_load_percent = parse_double_value(config, "bus_load_percent", plugin->bus_load_percent);
  return 0;
}

void network_on_tick(void* ctx, uint64_t tick) {
  (void)tick;
  auto* plugin = static_cast<NetworkSimPlugin*>(ctx);
  if (plugin == nullptr) {
    return;
  }
  plugin->frame_count += 1;
  const double simulated_load = static_cast<double>(plugin->frame_count) * plugin->bus_load_percent;
  std::fprintf(stderr, "[network_sim] frame_count=%llu simulated_load=%.2f\n",
               static_cast<unsigned long long>(plugin->frame_count), simulated_load);
}

void network_shutdown(void* ctx) {
  auto* plugin = static_cast<NetworkSimPlugin*>(ctx);
  if (plugin == nullptr) {
    return;
  }
  plugin->protocol = NetworkProtocol::CAN;
  plugin->bus_load_percent = 0.0;
  plugin->frame_count = 0;
}

BoatPluginVTable kNetworkSimVTable = {
    &network_initialize,
    &network_on_tick,
    &network_shutdown,
};

}  // namespace

extern "C" BoatPlugin* boat_plugin_create() {
  auto* plugin_state = new NetworkSimPlugin{};
  auto* plugin = new BoatPlugin{};
  plugin->vtable = &kNetworkSimVTable;
  plugin->ctx = plugin_state;
  return plugin;
}

extern "C" void boat_plugin_destroy(BoatPlugin* plugin) {
  if (plugin == nullptr) {
    return;
  }
  if (plugin->vtable != nullptr && plugin->vtable->shutdown != nullptr) {
    plugin->vtable->shutdown(plugin->ctx);
  }
  delete static_cast<NetworkSimPlugin*>(plugin->ctx);
  delete plugin;
}

extern "C" uint32_t boat_plugin_abi_version() { return BOAT_PLUGIN_ABI_VERSION; }
