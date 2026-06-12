#pragma once

#include <cstdint>
#include <functional>
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

/* Signature used inside the core layer to route a signal value to wherever
   the host (gateway, test harness, …) wants to deliver it. */
using SignalPublishFn =
    std::function<void(const char* signal_id, uint64_t tick, double value)>;

/* Signature for delivering a raw CAN frame from a plugin to the HIL layer. */
using CanPublishFn = std::function<void(const BoatCanFrame& frame)>;

/* Signature for delivering an Ethernet frame from a plugin to the HIL layer. */
using EthPublishFn = std::function<void(const BoatEthFrame& frame)>;

/* Signature for publishing a named value to the always-on signal bus. */
using BusPublishFn = std::function<void(const char* name, double value)>;

/* Signature for delivering a PDU frame from a plugin to the PduRouter. */
using PduPublishFn = std::function<void(const BoatPduFrame& frame)>;

class PluginManager {
 public:
  /* Set before loading plugins.  Every plugin that exposes set_publisher will
     receive a trampoline that delegates to this function. */
  void SetPublisher(SignalPublishFn fn);

  /* Set before loading plugins.  Every plugin that exposes set_can_publisher will
     receive a trampoline that delegates to this function. */
  void SetCanPublisher(CanPublishFn fn);

  /* Set before loading plugins.  Every plugin that exposes set_eth_publisher will
     receive a trampoline that delegates to this function. */
  void SetEthPublisher(EthPublishFn fn);

  /* Set before loading plugins.  Every plugin that exposes set_bus_publisher will
     receive a trampoline that delegates to this function. */
  void SetBusPublisher(BusPublishFn fn);

  /* Set before loading plugins.  Every plugin that exposes set_pdu_publisher will
     receive a trampoline that delegates to this function. */
  void SetPduPublisher(PduPublishFn fn);

  PluginHandle Load(const std::string& so_path, const std::string& config_json);
  void Unload(const std::string& name);
  void TickAll(std::uint64_t tick);
  /* Deliver an incoming CAN frame to every plugin that implements on_can_frame. */
  void DispatchCanFrame(const BoatCanFrame& frame, const std::string& iface);
  /* Deliver an incoming Ethernet frame to every plugin that implements on_eth_frame. */
  void DispatchEthFrame(const BoatEthFrame& frame, const std::string& iface);
  void ShutdownAll();
  [[nodiscard]] std::vector<std::string> List() const;

 private:
  std::unordered_map<std::string, PluginHandle> plugins_;
  SignalPublishFn publisher_fn_;
  CanPublishFn can_publisher_fn_;
  EthPublishFn eth_publisher_fn_;
  BusPublishFn bus_publisher_fn_;
  PduPublishFn pdu_publisher_fn_;
};

}  // namespace boat::core
