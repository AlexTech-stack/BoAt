// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#include "debug_service_impl.h"

#include <chrono>
#include <deque>
#include <memory>
#include <mutex>
#include <thread>
#include <vector>

#include "subscriber_queue.h"

namespace boat::gateway {

grpc::Status DebugServiceImpl::StreamEvents(
    grpc::ServerContext*                        context,
    const boat::v1::StreamRpcEventsRequest*     request,
    grpc::ServerWriter<boat::v1::RpcEvent>*     writer) {

  const std::string filter = request->method_filter();

  /* Heap-owned and captured by value: RpcAuditLog::Push snapshots its callbacks
     and invokes them after releasing the lock, and Unsubscribe does not wait
     for one in flight -- a stack-local queue (and a by-reference `filter`) here
     was the same use-after-free the other streaming RPCs had. */
  auto shared = MakeSubscriberQueue<boat::v1::RpcEvent>(
      &stream_drops_, stream_metrics::kDebugEvents, stream_queue_capacity_);

  const auto sub_id = log_.Subscribe(
      [shared, filter](const RpcEvent& ev) {
        if (!filter.empty() &&
            ev.method.find(filter) == std::string::npos) {
          return;
        }
        boat::v1::RpcEvent proto;
        proto.set_timestamp_ns(ev.timestamp_ns);
        proto.set_method(ev.method);
        proto.set_peer(ev.peer);
        proto.set_event_type(ev.event_type);
        proto.set_call_type(ev.call_type);
        proto.set_msg_bytes(ev.msg_bytes);
        proto.set_duration_us(ev.duration_us);
        proto.set_status_code(ev.status_code);
        proto.set_status_message(ev.status_message);
        proto.set_summary(ev.summary);
        std::size_t dropped = 0;
        {
          std::lock_guard<std::mutex> lock(shared->mutex);
          dropped = shared->Push(std::move(proto));
        }
        shared->ReportDrop(dropped);
        shared->cv.notify_one();
      });

  while (!context->IsCancelled()) {
    std::deque<boat::v1::RpcEvent> pending;
    {
      std::unique_lock<std::mutex> lock(shared->mutex);
      shared->cv.wait_for(lock, std::chrono::milliseconds(50),
                          [&shared] { return !shared->queue.empty(); });
      pending.swap(shared->queue);
    }
    for (const auto& ev : pending) {
      if (!writer->Write(ev)) {
        log_.Unsubscribe(sub_id);
        return grpc::Status::OK;
      }
    }
  }

  log_.Unsubscribe(sub_id);
  return grpc::Status::OK;
}

grpc::Status DebugServiceImpl::GetEffectiveConfig(
    grpc::ServerContext* /*context*/,
    const boat::v1::GetEffectiveConfigRequest* /*request*/,
    boat::v1::GetEffectiveConfigResponse* response) {
  // Rendered on demand rather than cached: the document is small, this is not
  // a hot path, and rendering keeps one code path (ToJson) responsible for
  // the format that BOAT_CONFIG_DUMP also writes.
  response->set_config_json(config_.ToJson());
  return grpc::Status::OK;
}

}  // namespace boat::gateway
