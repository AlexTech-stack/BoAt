#pragma once

#include <grpcpp/grpcpp.h>

#include "boat/v1/scenario.grpc.pb.h"
#include "core/event/event_bus.h"
#include "core/plugin/plugin_manager.h"
#include "core/signal/signal_router.h"
#include "core/state/sim_state_machine.h"

namespace boat::ipc {

class ScenarioServiceStub final : public boat::v1::ScenarioService::Service {
 public:
  ScenarioServiceStub(boat::core::EventBus& event_bus, boat::core::SimStateMachine& sim_state_machine,
                      boat::core::PluginManager& plugin_manager, boat::core::SignalRouter& signal_router);

  grpc::Status CreateScenario(grpc::ServerContext* context, const boat::v1::CreateScenarioRequest* request,
                              boat::v1::ScenarioResponse* response) override;
  grpc::Status GetScenario(grpc::ServerContext* context, const boat::v1::GetScenarioRequest* request,
                           boat::v1::ScenarioResponse* response) override;
  grpc::Status ListScenarios(grpc::ServerContext* context, const boat::v1::ListScenariosRequest* request,
                             boat::v1::ListScenariosResponse* response) override;
  grpc::Status ValidateScenario(grpc::ServerContext* context, const boat::v1::ValidateScenarioRequest* request,
                                boat::v1::ValidateScenarioResponse* response) override;
  grpc::Status DeleteScenario(grpc::ServerContext* context, const boat::v1::DeleteScenarioRequest* request,
                              boat::v1::DeleteScenarioResponse* response) override;

 private:
  boat::core::EventBus& event_bus_;
  boat::core::SimStateMachine& sim_state_machine_;
  boat::core::PluginManager& plugin_manager_;
  boat::core::SignalRouter& signal_router_;
};

}  // namespace boat::ipc
