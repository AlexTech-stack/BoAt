// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include <grpcpp/grpcpp.h>

#include "boat/v1/debug.grpc.pb.h"
#include "effective_config.h"
#include "rpc_audit_log.h"

namespace boat::gateway {

class DebugServiceImpl final : public boat::v1::DebugService::Service {
 public:
  DebugServiceImpl(RpcAuditLog& log, const EffectiveConfig& config)
      : log_(log), config_(config) {}

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
};

}  // namespace boat::gateway
