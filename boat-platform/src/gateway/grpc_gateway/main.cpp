#include <atomic>
#include <csignal>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <memory>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

#include <arpa/inet.h>

#include <grpcpp/grpcpp.h>

#include "bus_service_impl.h"
#include "can_bus_registry.h"
#include "can_service_impl.h"
#include "debug_service_impl.h"
#include "ethernet_bus_registry.h"
#include "ethernet_service_impl.h"
#include "rpc_audit_interceptor.h"
#include "rpc_audit_log.h"
#include "fault_service_impl.h"
#include "gateway_context.h"
#include "hil/virtual/virtual_can_driver.h"
#include "hil/can/physical_can_driver.h"
#include "hil/ethernet/virtual_ethernet_driver.h"
#include "hil/ethernet/raw_socket_ethernet_driver.h"
#include "pdu/pdu_router.h"
#include "pdu/tick_timer.h"
#include "pdu_service_impl.h"
#include "metrics_service_impl.h"
#include "plugin/plugin_manager.h"
#include "plugin_service_impl.h"
#include "replay_engine/replay_engine.h"
#include "replay_service_impl.h"
#include "scenario/scenario_loader.h"
#include "scenario_service_impl.h"
#include "simulation/simulation_context.h"
#include "signal/signal_bus.h"
#include "signal_service_impl.h"
#include "simulation_service_impl.h"
#include "event_store/event_store.h"
#include "trace_store/trace_store.h"
#include "trace_service_impl.h"

namespace {
std::shared_ptr<grpc::Server> g_server;
boat::core::TickScheduler* g_scheduler = nullptr;
std::atomic<bool> g_node_tick_running{false};
constexpr std::uint64_t kGatewayDeterminismSeed = 777;

std::array<std::uint8_t, 6> ReadInterfaceMac(const std::string& iface) {
  std::array<std::uint8_t, 6> mac{};
  std::ifstream f("/sys/class/net/" + iface + "/address");
  if (!f.is_open()) {
    mac[5] = 0x01;
    return mac;
  }
  std::string line;
  std::getline(f, line);
  if (line.size() < 17) {
    mac[5] = 0x01;
    return mac;
  }
  for (int i = 0; i < 6; ++i) {
    mac[i] = static_cast<std::uint8_t>(
        std::stoul(line.substr(i * 3, 2), nullptr, 16));
  }
  return mac;
}

void HandleSignal(int) {
  if (g_server) {
    g_server->Shutdown();
  }
  if (g_scheduler != nullptr) {
    g_scheduler->Stop();
  }
}
}  // namespace

int main() {
  boat::gateway::RpcAuditLog audit_log;
  // Gateway bootstrap uses a fixed seed so startup behavior is deterministic across environments.
  boat::core::SimulationContext sim(kGatewayDeterminismSeed);
  boat::core::SignalBus signal_bus;
  boat::store::SqliteEventStore event_store("boat_events.db");
  boat::store::FlatFileTraceStore trace_store("boat_traces.db");
  boat::replay::ReplayController replay_controller(trace_store, event_store, sim.event_bus());
  boat::core::ScenarioLoader scenario_loader;

  // Build Ethernet bus registry from BOAT_ETH_INTERFACES (comma-separated).
  // Each entry may be:
  //   name                        → auto-assign multicast addr/port by index
  //   name:mcast_addr:port        → explicit e.g. "veth0:239.255.0.1:51000"
  boat::hil::EthernetBusRegistry eth_registry;
  {
    const char* env = std::getenv("BOAT_ETH_INTERFACES");
    if (env != nullptr) {
      std::istringstream ss(env);
      std::string entry;
      std::size_t index = 0;
      while (std::getline(ss, entry, ',')) {
        if (entry.empty()) continue;
        // Parse "name" or "name:mcast_addr:port"
        // "raw:<ifname>" → physical NIC via AF_PACKET; else virtual multicast.
        if (entry.rfind("raw:", 0) == 0) {
          const std::string name = entry.substr(4);
          auto driver = std::make_unique<boat::hil::RawSocketEthernetDriver>(name);
          if (!eth_registry.Add(name, std::move(driver))) {
            std::fprintf(stderr, "[Gateway] Failed to open raw Ethernet interface '%s' "
                         "(check permissions / interface name)\n", name.c_str());
          } else {
            std::fprintf(stderr, "[Gateway] Registered raw Ethernet interface '%s'\n",
                         name.c_str());
          }
        } else {
          std::istringstream es(entry);
          std::string name, mcast, port_str;
          std::getline(es, name, ':');
          std::getline(es, mcast, ':');
          std::getline(es, port_str);
          if (mcast.empty() || port_str.empty()) {
            auto driver = boat::hil::VirtualEthernetDriver::FromIndex(name, index);
            eth_registry.Add(name, std::move(driver));
          } else {
            const auto port = static_cast<std::uint16_t>(std::stoul(port_str));
            auto driver = std::make_unique<boat::hil::VirtualEthernetDriver>(
                name, mcast, port);
            eth_registry.Add(name, std::move(driver));
          }
        }
        ++index;
      }
    }
  }

  // Build CAN bus registry from BOAT_CAN_INTERFACES (comma-separated, default "vcan0").
  // Interfaces named "vcan*" use VirtualCanDriver; all others use PhysicalCanDriver
  // (which reads sysfs for driver metadata and works with any SocketCAN-compatible
  // hardware, including PEAK PCAN, Kvaser, gs_usb, etc.).
  boat::hil::CanBusRegistry can_registry;
  {
    const char* env = std::getenv("BOAT_CAN_INTERFACES");
    const std::string ifaces_str = env ? env : "vcan0";
    std::istringstream ss(ifaces_str);
    std::string iface;
    while (std::getline(ss, iface, ',')) {
      if (iface.empty()) continue;

      std::shared_ptr<boat::hil::IHalDriver> driver;
      if (iface.size() >= 4 && iface.compare(0, 4, "vcan") == 0) {
        driver = std::make_shared<boat::hil::VirtualCanDriver>(iface);
      } else {
        driver = std::make_shared<boat::hil::PhysicalCanDriver>(iface);
      }

      // Capture info before driver is moved into the registry.
      auto info = driver->GetInfo();
      if (can_registry.Add(iface, std::move(driver), sim.event_bus())) {
        std::fprintf(stderr, "[Gateway] Registered CAN interface '%s' "
                     "(driver=%s, fd=%s, state=%s)\n",
                     iface.c_str(),
                     info.driver_name.c_str(),
                     info.fd_support ? "yes" : "no",
                     info.state.c_str());
      } else {
        std::fprintf(stderr, "[Gateway] Failed to open CAN interface '%s' "
                     "(check interface name / permissions)\n", iface.c_str());
      }
    }
  }

  // Node manager: loads permanent always-on plugins from BOAT_NODE_PLUGINS
  // (comma-separated .so paths). These are wired to the CAN/Ethernet bus at
  // startup and run independently of any simulation lifecycle.
  boat::core::PluginManager node_manager;
  {
    node_manager.SetCanPublisher([&can_registry](const BoatCanFrame& f,
                                                 const std::string& plugin_iface) {
      boat::hil::CanFrame frame{};
      frame.can_id = f.can_id;
      frame.dlc    = f.dlc;
      std::memcpy(frame.data, f.data, f.dlc);
      if (!plugin_iface.empty()) {
        can_registry.SendFrame(plugin_iface, frame);
      } else {
        can_registry.SendFrameAll(frame);
      }
    });
    // Dispatch incoming CAN frames to all loaded nodes.
    can_registry.Subscribe("", [&node_manager](const boat::hil::CanFrame& f,
                                               const std::string& iface) {
      BoatCanFrame bf{};
      bf.can_id = f.can_id;
      bf.dlc    = f.dlc;
      bf.flags  = f.flags;
      std::memcpy(bf.data, f.data, f.dlc);
      node_manager.DispatchCanFrame(bf, iface);
    });
    // Dispatch incoming Ethernet frames to all loaded nodes.
    eth_registry.Subscribe("", 0, [&node_manager](const boat::hil::EthernetFrame& f,
                                                  const std::string& iface) {
      BoatEthFrame bf{};
      std::memcpy(bf.dst_mac, f.dst_mac, 6);
      std::memcpy(bf.src_mac, f.src_mac, 6);
      bf.ethertype   = f.ethertype;
      bf.payload     = const_cast<uint8_t*>(f.payload.data());
      bf.payload_len = f.payload.size();
      node_manager.DispatchEthFrame(bf, iface);
    });
    // Wire the always-on bus-signal publisher for node plugins.
    node_manager.SetEthPublisher([&eth_registry](const BoatEthFrame& f) {
      boat::hil::EthernetFrame ef{};
      std::memcpy(ef.dst_mac, f.dst_mac, 6);
      std::memcpy(ef.src_mac, f.src_mac, 6);
      ef.ethertype   = f.ethertype;
      ef.payload.assign(f.payload, f.payload + f.payload_len);
      eth_registry.SendFrameAll(ef);
    });
    node_manager.SetBusPublisher([&signal_bus](const char* name, double value) {
      signal_bus.Publish(name, value);
    });
    // Load node plugins from BOAT_NODE_PLUGINS env var.
    // Entries are separated by comma.  Each entry may optionally specify a
    // JSON config separated by '?':
    //   ./can_tp.so?{"iface":"can0"},./can_tp.so?{"iface":"can1"}
    {
      const char* nodes_env = std::getenv("BOAT_NODE_PLUGINS");
      if (nodes_env != nullptr) {
        std::istringstream ss(nodes_env);
        std::string entry;
        while (std::getline(ss, entry, ',')) {
          if (entry.empty()) continue;
          auto qpos = entry.find('?');
          std::string so_path  = entry.substr(0, qpos);
          std::string config   = (qpos != std::string::npos)
                                    ? entry.substr(qpos + 1) : "{}";
          try {
            node_manager.Load(so_path, config);
            std::fprintf(stderr, "[Gateway] Loaded plugin '%s'\n",
                         so_path.c_str());
          } catch (const std::exception& ex) {
            std::fprintf(stderr, "[Gateway] Failed to load plugin '%s': %s\n",
                         so_path.c_str(), ex.what());
          }
        }
      }
    }
  }

  // PduRouter must be created before the node tick thread so it can be
  // captured for OnTick() calls and PDU publisher wiring.
  boat::hil::PduRouter pdu_router(can_registry, eth_registry);

  // Register replay forwarder: bridges replayed events to CAN/Ethernet/PDU bus registries
  replay_controller.SetEventForwarder(
      [&can_registry, &eth_registry, &pdu_router, &replay_controller](
          std::uint32_t event_type, std::uint64_t tick,
          const std::vector<std::uint8_t>& payload) {
        if (event_type >= boat::replay::kReplayEthEventBase &&
            event_type < boat::replay::kReplayEthEventBase + 0x10000) {
          const auto& cfg = replay_controller.GetActiveConfig();
          if (cfg.eth_iface.empty()) {
            return;
          }
          const auto src_mac = ReadInterfaceMac(cfg.eth_iface);
          static bool mac_logged = false;
          if (!mac_logged) {
            mac_logged = true;
            std::fprintf(stderr, "[Replay] iface=%s src_mac=%02x:%02x:%02x:%02x:%02x:%02x\n",
                         cfg.eth_iface.c_str(),
                         src_mac[0], src_mac[1], src_mac[2],
                         src_mac[3], src_mac[4], src_mac[5]);
          }
          uint16_t ethertype = static_cast<uint16_t>(event_type & 0xFFFF);
          int af = (ethertype == 0x86DD) ? AF_INET6 : AF_INET;
          int ip_len = (ethertype == 0x86DD) ? 16 : 4;
          int src_off = (ethertype == 0x86DD) ? 8 : 12;
          int dst_off = src_off + ip_len;
          boat::hil::EthernetFrame eth_frame{};
          if (!cfg.mac_map.empty()) {
            char ip_buf[INET6_ADDRSTRLEN];
            inet_ntop(af, payload.data() + src_off, ip_buf, sizeof(ip_buf));
            auto it = cfg.mac_map.find(ip_buf);
            if (it != cfg.mac_map.end()) {
              unsigned int m[6];
              if (std::sscanf(it->second.c_str(), "%02x:%02x:%02x:%02x:%02x:%02x",
                              &m[0], &m[1], &m[2], &m[3], &m[4], &m[5]) == 6) {
                for (int i = 0; i < 6; ++i) eth_frame.src_mac[i] = static_cast<uint8_t>(m[i]);
              } else {
                std::memcpy(eth_frame.src_mac, src_mac.data(), 6);
              }
            } else {
              std::memcpy(eth_frame.src_mac, src_mac.data(), 6);
            }
            inet_ntop(af, payload.data() + dst_off, ip_buf, sizeof(ip_buf));
            it = cfg.mac_map.find(ip_buf);
            if (it != cfg.mac_map.end()) {
              unsigned int m[6];
              if (std::sscanf(it->second.c_str(), "%02x:%02x:%02x:%02x:%02x:%02x",
                              &m[0], &m[1], &m[2], &m[3], &m[4], &m[5]) == 6) {
                for (int i = 0; i < 6; ++i) eth_frame.dst_mac[i] = static_cast<uint8_t>(m[i]);
              } else {
                std::memset(eth_frame.dst_mac, 0xFF, 6);
              }
            } else {
              std::memset(eth_frame.dst_mac, 0xFF, 6);
            }
          } else {
            std::memcpy(eth_frame.src_mac, src_mac.data(), 6);
            std::memset(eth_frame.dst_mac, 0xFF, 6);
          }
          eth_frame.ethertype = ethertype;
          eth_frame.payload.assign(payload.begin(), payload.end());
          eth_registry.SendFrame(cfg.eth_iface, eth_frame);
        } else if (event_type >= boat::replay::kReplayPduEventBase &&
                   event_type < boat::replay::kReplayPduEventBase + 0x10000) {
          const std::uint32_t pdu_id = event_type & 0xFFFF;
          pdu_router.SendPdu(pdu_id, payload);
        } else if (event_type <= 0x1FFFFFFF) {
          boat::hil::CanFrame can_frame{};
          can_frame.can_id = event_type;
          const std::size_t copy_len = std::min(payload.size(), sizeof(can_frame.data));
          if (copy_len > 0) {
            can_frame.dlc = static_cast<std::uint8_t>(copy_len);
            std::memcpy(can_frame.data, payload.data(), copy_len);
          }
          can_registry.SendFrameAll(can_frame);
        }
      });

  // Wire the PDU publisher so plugins (e.g. CanTp) can deliver reassembled
  // I-PDUs into the PduRouter.
  node_manager.SetPduPublisher([&pdu_router](const BoatPduFrame& f) {
    if (f.payload == nullptr) return;
    std::vector<uint8_t> payload(f.payload, f.payload + f.payload_len);
    pdu_router.SendPdu(f.pdu_id, payload);
  });

  // Start a background tick thread for node plugins and PDU transmission engine.
  // The tick interval sets the minimum achievable PDU cycle time.
  //   BOAT_NODE_TICK_MS=N   — set tick in ms (default 1)
  //   BOAT_NODE_TICK_US=N   — set tick in μs (overrides MS when set)
  //   Both use TimerfdTickTimer (Linux timerfd, absolute-time scheduling).
  {
    using namespace std::chrono_literals;
    std::chrono::nanoseconds tick_ns = 1ms;  // default

    const char* us_env = std::getenv("BOAT_NODE_TICK_US");
    if (us_env != nullptr) {
      char* end = nullptr;
      auto val = std::strtoul(us_env, &end, 10);
      if (end != us_env && val > 0) {
        tick_ns = std::chrono::microseconds(val);
      }
    } else {
      const char* ms_env = std::getenv("BOAT_NODE_TICK_MS");
      if (ms_env != nullptr) {
        char* end = nullptr;
        auto val = std::strtoul(ms_env, &end, 10);
        if (end != ms_env && val > 0) {
          tick_ns = std::chrono::milliseconds(val);
        }
      }
    }

    auto timer = boat::hil::TickTimer::Create(tick_ns);
    g_node_tick_running.store(true, std::memory_order_release);
    std::thread([&node_manager, &pdu_router, timer = std::move(timer)]() {
      std::uint64_t tick = 0;
      while (g_node_tick_running.load(std::memory_order_acquire)) {
        if (!timer->WaitForNextTick()) break;
        node_manager.TickAll(tick++);
        // Drive the PDU transmission engine with monotonic ms for schedule timing
        const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
            timer->Elapsed()).count();
        pdu_router.OnTick(static_cast<std::uint64_t>(elapsed));
      }
    }).detach();
  }

  boat::gateway::GatewayContext ctx{
      .sim = sim,
      .signal_bus = signal_bus,
      .scenario_loader = scenario_loader,
      .event_store = event_store,
      .trace_store = trace_store,
      .replay_controller = replay_controller,
      .can_bus_registry = can_registry,
      .ethernet_bus_registry = eth_registry,
      .pdu_router = pdu_router,
      .audit_log = audit_log,
  };

  boat::gateway::BusServiceImpl      bus_impl(audit_log, signal_bus);
  boat::gateway::EthernetServiceImpl ethernet_impl(ctx);
  boat::gateway::SimulationServiceImpl simulation_impl(sim, can_registry, eth_registry);
  boat::gateway::SignalServiceImpl signal_impl(ctx);
  boat::gateway::ScenarioServiceImpl scenario_impl(ctx);
  boat::gateway::ReplayServiceImpl replay_impl(ctx);
  boat::gateway::PluginServiceImpl plugin_impl(ctx);
  boat::gateway::MetricsServiceImpl metrics_impl(ctx);
  boat::gateway::TraceServiceImpl trace_impl(ctx);
  boat::gateway::FaultServiceImpl fault_impl(ctx);
  boat::gateway::CanServiceImpl can_impl(ctx);
  boat::gateway::PduServiceImpl pdu_impl(ctx);
  boat::gateway::DebugServiceImpl debug_impl(audit_log);

  grpc::ServerBuilder builder;
  builder.AddListeningPort("0.0.0.0:50051", grpc::InsecureServerCredentials());

  // Register the audit interceptor — captures every RPC call automatically.
  std::vector<std::unique_ptr<grpc::experimental::ServerInterceptorFactoryInterface>>
      interceptors;
  interceptors.push_back(
      std::make_unique<boat::gateway::RpcAuditInterceptorFactory>(audit_log));
  builder.experimental().SetInterceptorCreators(std::move(interceptors));
  builder.RegisterService(&bus_impl);
  builder.RegisterService(&ethernet_impl);
  builder.RegisterService(&simulation_impl);
  builder.RegisterService(&signal_impl);
  builder.RegisterService(&scenario_impl);
  builder.RegisterService(&replay_impl);
  builder.RegisterService(&plugin_impl);
  builder.RegisterService(&metrics_impl);
  builder.RegisterService(&trace_impl);
  builder.RegisterService(&fault_impl);
  builder.RegisterService(&can_impl);
  builder.RegisterService(&pdu_impl);
  builder.RegisterService(&debug_impl);

  g_server = builder.BuildAndStart();
  g_scheduler = &sim.scheduler();
  std::signal(SIGINT, HandleSignal);
  std::signal(SIGTERM, HandleSignal);

  if (g_server) {
    g_server->Wait();
  }
  sim.scheduler().Stop();
  g_node_tick_running.store(false, std::memory_order_release);
  node_manager.ShutdownAll();
  eth_registry.StopAll();
  return 0;
}
