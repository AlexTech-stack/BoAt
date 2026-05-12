#pragma once

#include <cstddef>
#include <functional>
#include <mutex>
#include <string>
#include <unordered_map>
#include <vector>

#include <grpcpp/grpcpp.h>

#include "boat/v1/bus.grpc.pb.h"
#include "rpc_audit_log.h"

namespace boat::gateway {

/* Always-on signal bus — nodes publish named typed values and subscribe
   to them independently of any simulation lifecycle. */
class BusServiceImpl final : public boat::v1::BusService::Service {
 public:
  explicit BusServiceImpl(RpcAuditLog& log) : audit_log_(log) {}
  grpc::Status Publish(grpc::ServerContext* context,
                       const boat::v1::BusPublishRequest* request,
                       boat::v1::BusPublishResponse* response) override;

  grpc::Status Subscribe(grpc::ServerContext* context,
                         const boat::v1::BusSubscribeRequest* request,
                         grpc::ServerWriter<boat::v1::BusSignal>* writer) override;

 private:
  using SubId   = std::size_t;
  using Callback = std::function<void(const boat::v1::BusSignal&)>;

  struct Subscription {
    std::vector<std::string> names;  // empty = all signals
    Callback cb;
  };

  SubId Subscribe(std::vector<std::string> names, Callback cb);
  void  Unsubscribe(SubId id);
  void  Dispatch(const boat::v1::BusSignal& signal);

  RpcAuditLog& audit_log_;
  std::mutex subs_mutex_;
  std::unordered_map<SubId, Subscription> subscriptions_;
  SubId next_id_{0};
};

}  // namespace boat::gateway
