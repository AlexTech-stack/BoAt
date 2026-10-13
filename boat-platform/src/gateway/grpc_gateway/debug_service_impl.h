// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include <cstddef>

#include <grpcpp/grpcpp.h>

#include "boat/v1/debug.grpc.pb.h"
#include "effective_config.h"
#include "rpc_audit_log.h"
#include "subscriber_queue.h"

namespace boat::gateway {

class DebugServiceImpl final : public boat::v1::DebugService::Service {
 public:
  DebugServiceImpl(RpcAuditLog& log, const EffectiveConfig& config,
                   StreamDropRegistry& stream_drops,
                   std::size_t stream_queue_capacity)
      : log_(log),
        config_(config),
        stream_drops_(stream_drops),
        stream_queue_capacity_(stream_queue_capacity) {}

  grpc::Status StreamEvents(
      grpc::ServerContext*                            context,
      const boat::v1::StreamRpcEventsRequest*         request,
      grpc::ServerWriter<boat::v1::RpcEvent>*         writer) override;

  grpc::Status GetEffectiveConfig(
      grpc::ServerContext*                            context,
      const boat::v1::GetEffectiveConfigRequest*      request,
      boat::v1::GetEffectiveConfigResponse*           response) override;

 private:
  RpcAuditLog& log_;
  const EffectiveConfig& config_;
  StreamDropRegistry& stream_drops_;
  std::size_t stream_queue_capacity_;
};

}  // namespace boat::gateway
