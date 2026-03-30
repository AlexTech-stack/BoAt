#include "replay_service_impl.h"

#include <atomic>
#include <chrono>
#include <exception>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

namespace boat::gateway {
namespace {

std::string BuildReplaySessionKey(const boat::v1::StartReplayRequest& request) {
  if (request.trace_id().empty()) {
    return {};
  }
  if (request.simulation_id().empty()) {
    return "trace:" + request.trace_id();
  }
  return "trace:" + request.trace_id() + "|simulation:" + request.simulation_id();
}

grpc::Status MapReplayException(const std::exception& ex) {
  const std::string message = ex.what();
  if (message.find("not found") != std::string::npos || message.find("missing") != std::string::npos) {
    return grpc::Status(grpc::StatusCode::NOT_FOUND, message);
  }
  if (message.find("invalid") != std::string::npos || message.find("out of bounds") != std::string::npos) {
    return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT, message);
  }
  if (message.find("paused") != std::string::npos || message.find("stopped") != std::string::npos) {
    return grpc::Status(grpc::StatusCode::FAILED_PRECONDITION, message);
  }
  return grpc::Status(grpc::StatusCode::INTERNAL, message);
}

}  // namespace

ReplayServiceImpl::ReplayServiceImpl(GatewayContext& ctx) : ctx_(ctx) {}

grpc::Status ReplayServiceImpl::StartReplay(grpc::ServerContext*, const boat::v1::StartReplayRequest* request,
                                            boat::v1::ReplayControlResponse* response) {
  try {
    const std::string replay_id = BuildReplaySessionKey(*request);
    if (replay_id.empty()) {
      return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT, "trace_id must be non-empty");
    }
    boat::replay::ReplayConfig config{
        .trace_id = request->trace_id(),
        .speed = boat::replay::ReplaySpeed::REAL_TIME,
    };
    ctx_.replay_controller.Start(config);
    {
      std::lock_guard<std::mutex> lock(replay_mutex_);
      active_replays_.clear();
      active_replays_[replay_id] = config;
    }
    response->set_accepted(true);
    return grpc::Status::OK;
  } catch (const std::exception& ex) {
    return MapReplayException(ex);
  } catch (...) {
    return grpc::Status(grpc::StatusCode::INTERNAL, "unexpected start replay error");
  }
}

grpc::Status ReplayServiceImpl::SeekReplay(grpc::ServerContext*, const boat::v1::SeekReplayRequest* request,
                                           boat::v1::ReplayControlResponse* response) {
  try {
    std::lock_guard<std::mutex> lock(replay_mutex_);
    const auto it = active_replays_.find(request->replay_id());
    if (it == active_replays_.end()) {
      return grpc::Status(grpc::StatusCode::NOT_FOUND, "replay not found");
    }
    ctx_.replay_controller.Seek(request->tick());
    response->set_accepted(true);
    return grpc::Status::OK;
  } catch (const std::exception& ex) {
    return MapReplayException(ex);
  } catch (...) {
    return grpc::Status(grpc::StatusCode::INTERNAL, "unexpected seek replay error");
  }
}

grpc::Status ReplayServiceImpl::StreamReplay(grpc::ServerContext* context, const boat::v1::StreamReplayRequest* request,
                                             grpc::ServerWriter<boat::v1::ReplayEvent>* writer) {
  {
    std::lock_guard<std::mutex> lock(replay_mutex_);
    if (active_replays_.find(request->replay_id()) == active_replays_.end()) {
      return grpc::Status(grpc::StatusCode::NOT_FOUND, "replay not found");
    }
  }

  std::mutex events_mutex;
  std::vector<boat::v1::ReplayEvent> pending;

  const auto handle = ctx_.event_bus.Subscribe(boat::replay::kReplayBusEventType, [&](const boat::core::BusEvent& event) {
    boat::v1::ReplayEvent replay_event;
    replay_event.set_replay_id(request->replay_id());
    replay_event.set_tick(event.tick);
    if (event.payload.has_value()) {
      try {
        replay_event.set_payload(std::any_cast<std::string>(event.payload));
      } catch (const std::bad_any_cast&) {
        replay_event.set_payload("replay-event");
      }
    }
    std::lock_guard<std::mutex> lock(events_mutex);
    pending.push_back(std::move(replay_event));
  });

  while (!context->IsCancelled()) {
    std::vector<boat::v1::ReplayEvent> local;
    {
      std::lock_guard<std::mutex> lock(events_mutex);
      local.swap(pending);
    }
    for (const auto& event : local) {
      if (!writer->Write(event)) {
        break;
      }
    }
    if (ctx_.replay_controller.HasError()) {
      ctx_.event_bus.Unsubscribe(handle);
      return grpc::Status(grpc::StatusCode::FAILED_PRECONDITION, ctx_.replay_controller.LastError());
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(50));
  }

  ctx_.event_bus.Unsubscribe(handle);
  return grpc::Status::OK;
}

}  // namespace boat::gateway
