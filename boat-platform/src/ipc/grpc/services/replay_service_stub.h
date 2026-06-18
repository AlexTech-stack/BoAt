#pragma once

#include <grpcpp/grpcpp.h>

#include "boat/v1/replay.grpc.pb.h"
#include "core/event/event_bus.h"
#include "core/plugin/plugin_manager.h"
#include "core/signal/signal_router.h"
#include "core/state/sim_state_machine.h"

namespace boat::ipc {

class ReplayServiceStub final : public boat::v1::ReplayService::Service {
 public:
  ReplayServiceStub(boat::core::EventBus& event_bus, boat::core::SimStateMachine& sim_state_machine,
                    boat::core::PluginManager& plugin_manager, boat::core::SignalRouter& signal_router);

  grpc::Status StartReplay(grpc::ServerContext* context, const boat::v1::StartReplayRequest* request,
                           boat::v1::ReplayControlResponse* response) override;
  grpc::Status SeekReplay(grpc::ServerContext* context, const boat::v1::SeekReplayRequest* request,
                          boat::v1::ReplayControlResponse* response) override;
  grpc::Status StreamReplay(grpc::ServerContext* context, const boat::v1::StreamReplayRequest* request,
                            grpc::ServerWriter<boat::v1::ReplayEvent>* writer) override;
  grpc::Status PauseReplay(grpc::ServerContext* context, const boat::v1::PauseReplayRequest* request,
                           boat::v1::ReplayControlResponse* response) override;
  grpc::Status ResumeReplay(grpc::ServerContext* context, const boat::v1::ResumeReplayRequest* request,
                            boat::v1::ReplayControlResponse* response) override;
  grpc::Status StopReplay(grpc::ServerContext* context, const boat::v1::StopReplayRequest* request,
                          boat::v1::ReplayControlResponse* response) override;
  grpc::Status ImportTraceData(grpc::ServerContext* context, const boat::v1::ImportTraceDataRequest* request,
                               boat::v1::ReplayControlResponse* response) override;
  grpc::Status StartReplayFromEvents(grpc::ServerContext* context,
                                     const boat::v1::StartReplayFromEventsRequest* request,
                                     boat::v1::ReplayControlResponse* response) override;

 private:
  boat::core::EventBus& event_bus_;
  boat::core::SimStateMachine& sim_state_machine_;
  boat::core::PluginManager& plugin_manager_;
  boat::core::SignalRouter& signal_router_;
};

}  // namespace boat::ipc
