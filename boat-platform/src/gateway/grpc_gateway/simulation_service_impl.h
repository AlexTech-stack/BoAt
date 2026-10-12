// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include <cstddef>
#include <mutex>
#include <optional>
#include <string>
#include <unordered_map>

#include <grpcpp/grpcpp.h>

#include "boat/v1/simulation.grpc.pb.h"
#include "core/scenario/scenario_loader.h"
#include "core/simulation/simulation_context.h"
#include "core/state/sim_state_machine.h"
#include "store/config_store/config_store.h"

namespace boat::hil {
class CanBusRegistry;
class EthernetBusRegistry;
}  // namespace boat::hil

namespace boat::gateway {

class SimulationServiceImpl final : public boat::v1::SimulationService::Service {
 public:
  SimulationServiceImpl(boat::core::SimulationContext& sim,
                        boat::hil::CanBusRegistry& can_registry,
                        boat::hil::EthernetBusRegistry& eth_registry);

  grpc::Status CreateSimulation(grpc::ServerContext* context, const boat::v1::CreateSimulationRequest* request,
                                boat::v1::SimulationResponse* response) override;
  grpc::Status StartSimulation(grpc::ServerContext* context, const boat::v1::StartSimulationRequest* request,
                               boat::v1::SimulationResponse* response) override;
  grpc::Status PauseSimulation(grpc::ServerContext* context, const boat::v1::PauseSimulationRequest* request,
                               boat::v1::SimulationResponse* response) override;
  grpc::Status StepSimulation(grpc::ServerContext* context, const boat::v1::StepSimulationRequest* request,
                              boat::v1::SimulationResponse* response) override;
  grpc::Status ResetSimulation(grpc::ServerContext* context, const boat::v1::ResetSimulationRequest* request,
                               boat::v1::SimulationResponse* response) override;
  grpc::Status StopSimulation(grpc::ServerContext* context, const boat::v1::StopSimulationRequest* request,
                              boat::v1::SimulationResponse* response) override;
  grpc::Status GetSimulationState(grpc::ServerContext* context, const boat::v1::GetSimulationStateRequest* request,
                                  boat::v1::SimulationResponse* response) override;
  grpc::Status WatchSimulation(grpc::ServerContext* context, const boat::v1::GetSimulationStateRequest* request,
                               grpc::ServerWriter<boat::v1::SimulationResponse>* writer) override;
  grpc::Status ListSimulations(grpc::ServerContext* context, const boat::v1::ListSimulationsRequest* request,
                               boat::v1::ListSimulationsResponse* response) override;

 private:
  static boat::v1::SimulationState ToProtoState(boat::core::SimState state);
  void FillSimulation(const std::string& simulation_id, const boat::core::ScenarioDef& scenario,
                      boat::v1::Simulation* out) const;

  /* The cleanup half of StopSimulation: stop ticking this simulation's plugin
     manager hook, drop the registry RX subscriptions, shut the loaded
     scenario plugins down. StopSimulation and PrepareFreshRun share it so the
     two teardown paths cannot drift apart. */
  void CleanupRuntime();

  /* CleanupRuntime plus the rewind: clock/engine reset, fault schedules and
     queued events cleared. Called by ResetSimulation, and by CreateSimulation
     when no session is live, so a definition created after a previous run
     reports tick 0 instead of inheriting its counter. */
  void PrepareFreshRun();

  boat::core::SimulationContext& sim_;
  boat::hil::CanBusRegistry& can_registry_;
  boat::hil::EthernetBusRegistry& eth_registry_;
  std::unordered_map<std::string, boat::core::ScenarioDef> simulations_;
  mutable std::mutex simulations_mutex_;
  boat::store::SqliteTomlConfigStore config_store_{"boat_config.db"};
  std::optional<std::size_t> can_rx_sub_id_;
  std::optional<std::size_t> eth_rx_sub_id_;

  /* The one simulation the gateway can actually be running. Every RPC drives
     the same gateway-wide state, so `simulation_id` only selects a stored
     scenario definition; this records which definition a running session
     belongs to and keeps a second one from starting over it. Empty = none. */
  std::mutex active_mutex_;
  std::string active_simulation_id_;
};

}  // namespace boat::gateway
