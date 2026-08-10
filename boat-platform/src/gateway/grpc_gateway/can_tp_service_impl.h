#pragma once

#include <grpcpp/grpcpp.h>

#include <string>
#include <utility>
#include <vector>

#include "boat/v1/can_tp.grpc.pb.h"
#include "core/can_tp_interface.h"
#include "gateway_context.h"

namespace boat::gateway {

class CanTpServiceImpl final : public boat::v1::CanTpService::Service {
 public:
  explicit CanTpServiceImpl(GatewayContext& ctx);

  grpc::Status Configure(grpc::ServerContext* context,
                         const boat::v1::ConfigureRequest* request,
                         boat::v1::ConfigureResponse* response) override;

  grpc::Status Send(grpc::ServerContext* context,
                    const boat::v1::SendRequest* request,
                    boat::v1::SendResponse* response) override;

  grpc::Status ListSessions(grpc::ServerContext* context,
                            const boat::v1::ListSessionsRequest* request,
                            boat::v1::ListSessionsResponse* response) override;

  grpc::Status RemoveSession(grpc::ServerContext* context,
                             const boat::v1::RemoveSessionRequest* request,
                             boat::v1::RemoveSessionResponse* response) override;

  grpc::Status Subscribe(grpc::ServerContext* context,
                        const boat::v1::SubscribeRequest* request,
                        grpc::ServerWriter<boat::v1::CanTpRxEvent>* writer) override;

  grpc::Status SubscribeErrors(grpc::ServerContext* context,
                        const boat::v1::SubscribeRequest* request,
                        grpc::ServerWriter<boat::v1::CanTpErrorEvent>* writer) override;

 private:
  GatewayContext& ctx_;
  // Resolves which loaded CanTp instance to use. iface non-empty ->
  // FindService("can_tp:" + iface) directly. iface empty -> prefix-scan
  // ListServices() for "can_tp:*" entries: 0 -> not found, 1 -> use it
  // (unchanged behavior for the common single-instance case), >1 ->
  // ambiguous (status set on out-params, ICanTp* is null).
  boat::core::ICanTp* GetCanTp(const std::string& iface, grpc::StatusCode* status_out,
                                std::string* message_out);
  // Every loaded CanTp instance, paired with its iface. Used by
  // ListSessions when no iface filter is given.
  std::vector<std::pair<std::string, boat::core::ICanTp*>> GetAllCanTp();
};

}  // namespace boat::gateway
