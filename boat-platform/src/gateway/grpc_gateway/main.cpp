#include <atomic>
#include <csignal>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

#include <grpcpp/grpcpp.h>

#include "bus_service_impl.h"
#include "can_bus_registry.h"
#include "can_service_impl.h"
#include "debug_service_impl.h"
#include "ethernet_bus_registry.h"
#include "ethernet_service_impl.h"
#include "rpc_audit_interceptor.h"
#include "rpc_audit_log.h"
#include "determinism/determinism_engine.h"
#include "event/event_bus.h"
#include "fault/fault_injector.h"
#include "fault_service_impl.h"
#include "gateway_context.h"
#include "hil/virtual/virtual_can_driver.h"
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
#include "scheduler/sim_clock.h"
#include "scheduler/tick_scheduler.h"
#include "signal/signal_bus.h"
#include "signal/signal_router.h"
#include "signal_service_impl.h"
#include "simulation_service_impl.h"
#include "state/sim_state_machine.h"
#include "event_store/event_store.h"
#include "trace_store/trace_store.h"
#include "trace_service_impl.h"

namespace {
std::shared_ptr<grpc::Server> g_server;
boat::core::TickScheduler* g_scheduler = nullptr;
std::atomic<bool> g_node_tick_running{false};
constexpr std::uint64_t kGatewayDeterminismSeed = 777;

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
  boat::core::SimClock clock(0);
  // Gateway bootstrap uses a fixed seed so startup behavior is deterministic across environments.
  boat::core::DeterminismEngine determinism(kGatewayDeterminismSeed);
  boat::core::EventBus event_bus;
  boat::core::SignalRouter signal_router;
  boat::core::SignalBus signal_bus;
  boat::core::FaultInjector fault_injector(determinism);
  boat::core::SimStateMachine state_machine;
  boat::core::PluginManager plugin_manager;
  boat::core::TickScheduler scheduler(clock, event_bus, determinism);
  boat::store::SqliteEventStore event_store("boat_events.db");
  boat::store::FlatFileTraceStore trace_store("boat_traces.db");
  boat::replay::ReplayController replay_controller(trace_store, event_store, event_bus);
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
  boat::hil::CanBusRegistry can_registry;
  {
    const char* env = std::getenv("BOAT_CAN_INTERFACES");
    const std::string ifaces_str = env ? env : "vcan0";
    std::istringstream ss(ifaces_str);
    std::string iface;
    while (std::getline(ss, iface, ',')) {
      if (iface.empty()) continue;
      auto driver = std::make_shared<boat::hil::VirtualCanDriver>(iface);
      if (can_registry.Add(iface, std::move(driver), event_bus)) {
        // opened successfully — logged implicitly
      }
      // silently skip interfaces that fail to open (vcan may not exist in all envs)
    }
  }

  // Node manager: loads permanent always-on plugins from BOAT_NODE_PLUGINS
  // (comma-separated .so paths). These are wired to the CAN/Ethernet bus at
  // startup and run independently of any simulation lifecycle.
  boat::core::PluginManager node_manager;
  {
    node_manager.SetCanPublisher([&can_registry](const BoatCanFrame& f) {
      boat::hil::CanFrame frame{};
      frame.can_id = f.can_id;
      frame.dlc    = f.dlc;
      std::memcpy(frame.data, f.data, f.dlc);
      can_registry.SendFrameAll(frame);
    });
    // Dispatch incoming CAN frames to all loaded nodes.
    can_registry.Subscribe("", [&node_manager](const boat::hil::CanFrame& f,
                                               const std::string& iface) {
      BoatCanFrame bf{};
      bf.can_id = f.can_id;
      bf.dlc    = f.dlc;
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
    node_manager.SetBusPublisher([&signal_bus](const char* name, double value) {
      signal_bus.Publish(name, value);
    });
    // Load node plugins from BOAT_NODE_PLUGINS env var.
    {
      const char* nodes_env = std::getenv("BOAT_NODE_PLUGINS");
      if (nodes_env != nullptr) {
        std::istringstream ss(nodes_env);
        std::string so_path;
        while (std::getline(ss, so_path, ',')) {
          if (so_path.empty()) continue;
          try {
            node_manager.Load(so_path, "{}");
          } catch (const std::exception& ex) {
            (void)ex;
          }
        }
      }
    }
  }

  // PduRouter must be created before the node tick thread so it can be
  // captured for OnTick() calls and PDU publisher wiring.
  boat::hil::PduRouter pdu_router(can_registry, eth_registry);

  // Wire the PDU publisher so plugins (e.g. CanTp) can deliver reassembled
  // I-PDUs into the PduRouter.
  node_manager.SetPduPublisher([&pdu_router](const BoatPduFrame& f) {
    if (f.payload == nullptr) return;
    std::vector<uint8_t> payload(f.payload, f.payload + f.payload_len);
    pdu_router.SendPdu(f.pdu_id, payload);
  });

  // Start a background tick thread for node plugins and PDU transmission engine.
  // The tick interval sets the minimum achievable PDU cycle time.
  //   BOAT_NODE_TICK_MS=N   — set tick in ms (default 10, min 1, >=1ms range uses SleepTickTimer)
  //   BOAT_NODE_TICK_US=N   — set tick in μs (high-precision mode, uses TimerfdTickTimer)
  //   BOAT_NODE_TICK_US takes precedence over BOAT_NODE_TICK_MS when both are set.
  {
    using namespace std::chrono_literals;
    std::chrono::nanoseconds tick_ns = 10ms;  // default

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
      .sim_state_machine = state_machine,
      .sim_clock = clock,
      .tick_scheduler = scheduler,
      .event_bus = event_bus,
      .signal_router = signal_router,
      .plugin_manager = plugin_manager,
      .fault_injector = fault_injector,
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
  boat::gateway::SimulationServiceImpl simulation_impl(ctx);
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
  g_scheduler = &scheduler;
  std::signal(SIGINT, HandleSignal);
  std::signal(SIGTERM, HandleSignal);

  if (g_server) {
    g_server->Wait();
  }
  scheduler.Stop();
  g_node_tick_running.store(false, std::memory_order_release);
  node_manager.ShutdownAll();
  eth_registry.StopAll();
  return 0;
}
