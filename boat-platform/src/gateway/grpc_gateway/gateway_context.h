// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include "effective_config.h"
#include "rpc_audit_log.h"
#include "subscriber_queue.h"  // kDefaultStreamQueueCapacity + StreamDropRegistry
#include "core/plugin/plugin_manager.h"
#include "core/scenario/scenario_loader.h"
#include "core/signal/signal_bus.h"
#include "core/simulation/simulation_context.h"
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

class FrameSink;

struct GatewayContext {
  boat::core::SimulationContext& sim;

  boat::core::SignalBus& signal_bus;
  boat::core::ScenarioLoader& scenario_loader;
  boat::store::IEventStore& event_store;
  boat::store::FlatFileTraceStore& trace_store;
  boat::replay::ReplayController& replay_controller;
  boat::hil::CanBusRegistry& can_bus_registry;
  boat::hil::EthernetBusRegistry& ethernet_bus_registry;
  boat::core::PluginManager& plugin_manager;
  FrameSink& frame_sink;
  RpcAuditLog& audit_log;
  /* A reference, not a copy: a few fields (gRPC port, TLS) are resolved after
     this struct is built, and services only read it once an RPC arrives, by
     which time startup has finished and the value is final. */
  const EffectiveConfig& effective_config;

  /* Cumulative per-stream overflow counts, one entry per stream_metrics name.
     Handlers register their drops here so MetricsService can expose them
     (issue #21); value member so every GatewayContext has one without every
     construction site having to wire it in. */
  StreamDropRegistry stream_drops;

  /* Events one streaming subscriber may queue before the oldest are dropped.
     BOAT_STREAM_QUEUE_CAPACITY, resolved once in main into EffectiveConfig;
     defaulted so tests and any other construction site get the documented
     behavior without touching this field. */
  std::size_t stream_queue_capacity = kDefaultStreamQueueCapacity;
};

}  // namespace boat::gateway
