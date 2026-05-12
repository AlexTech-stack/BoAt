#pragma once

#include "rpc_audit_log.h"
#include "core/event/event_bus.h"
#include "core/fault/fault_injector.h"
#include "core/plugin/plugin_manager.h"
#include "core/scenario/scenario_loader.h"
#include "core/scheduler/sim_clock.h"
#include "core/scheduler/tick_scheduler.h"
#include "core/signal/signal_router.h"
#include "core/state/sim_state_machine.h"
#include "event_store/event_store.h"
#include "replay_engine/replay_engine.h"
#include "trace_store/trace_store.h"

// Forward-declare to avoid pulling the entire HIL header tree into every
// translation unit that includes gateway_context.h.
namespace boat::hil {
class CanBusRegistry;
class EthernetBusRegistry;
}  // namespace boat::hil

namespace boat::gateway {

struct GatewayContext {
  boat::core::SimStateMachine& sim_state_machine;
  boat::core::SimClock& sim_clock;
  boat::core::TickScheduler& tick_scheduler;
  boat::core::EventBus& event_bus;
  boat::core::SignalRouter& signal_router;
  boat::core::PluginManager& plugin_manager;
  boat::core::FaultInjector& fault_injector;
  boat::core::ScenarioLoader& scenario_loader;
  boat::store::SqliteEventStore& event_store;
  boat::store::FlatFileTraceStore& trace_store;
  boat::replay::ReplayController& replay_controller;
  boat::hil::CanBusRegistry& can_bus_registry;
  boat::hil::EthernetBusRegistry& ethernet_bus_registry;
  RpcAuditLog& audit_log;
};

}  // namespace boat::gateway
