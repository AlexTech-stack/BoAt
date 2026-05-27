#pragma once

#include <cstddef>
#include <mutex>
#include <optional>
#include <string>
#include <unordered_map>

#include <grpcpp/grpcpp.h>

#include "boat/v1/simulation.grpc.pb.h"
#include "core/scenario/scenario_loader.h"
#include "core/state/sim_state_machine.h"
#include "gateway_context.h"
#include "store/config_store/config_store.h"

namespace boat::gateway {

class SimulationServiceImpl final : public boat::v1::SimulationService::Service {
 public:
  explicit SimulationServiceImpl(GatewayContext& ctx);

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

  GatewayContext& ctx_;
  std::unordered_map<std::string, boat::core::ScenarioDef> simulations_;
  mutable std::mutex simulations_mutex_;
  boat::store::SqliteTomlConfigStore config_store_{"boat_config.db"};
  std::optional<std::size_t> can_rx_sub_id_;
};

}  // namespace boat::gateway
