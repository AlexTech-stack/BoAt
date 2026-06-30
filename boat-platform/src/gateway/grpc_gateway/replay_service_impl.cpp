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

boat::replay::ReplaySpeed ReplayServiceImpl::ProtoSpeedToInternal(boat::v1::ReplaySpeed proto_speed) const {
  switch (proto_speed) {
    case boat::v1::REPLAY_SPEED_ACCELERATED:
      return boat::replay::ReplaySpeed::ACCELERATED;
    case boat::v1::REPLAY_SPEED_STEP_BY_STEP:
      return boat::replay::ReplaySpeed::STEP_BY_STEP;
    case boat::v1::REPLAY_SPEED_REAL_TIME:
    default:
      return boat::replay::ReplaySpeed::REAL_TIME;
  }
}

grpc::Status ReplayServiceImpl::StartReplay(grpc::ServerContext*, const boat::v1::StartReplayRequest* request,
                                            boat::v1::ReplayControlResponse* response) {
  try {
    const std::string replay_id = BuildReplaySessionKey(*request);
    if (replay_id.empty()) {
      return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT, "trace_id must be non-empty");
    }
    boat::replay::ReplayConfig config{
        .trace_id = request->trace_id(),
        .speed = ProtoSpeedToInternal(request->speed()),
        .speed_multiplier = request->speed_multiplier() > 0.0 ? request->speed_multiplier() : 1.0,
        .eth_iface = request->eth_iface(),
        .mac_map = {request->mac_map().begin(), request->mac_map().end()},
    };
    ctx_.replay_controller.Start(config);
    {
      std::lock_guard<std::mutex> lock(replay_mutex_);
      active_replays_[replay_id] = config;
    }
    response->set_accepted(true);
    response->set_replay_id(replay_id);
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
    response->set_replay_id(request->replay_id());
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

  const auto handle = ctx_.sim.event_bus().Subscribe(boat::replay::kReplayBusEventType, [&](const boat::core::BusEvent& event) {
    boat::v1::ReplayEvent replay_event;
    replay_event.set_replay_id(request->replay_id());
    replay_event.set_tick(event.tick);
    if (const auto* s = std::get_if<std::string>(&event.payload)) {
      replay_event.set_payload(*s);
    } else {
      replay_event.set_payload("replay-event");
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
      ctx_.sim.event_bus().Unsubscribe(handle);
      return grpc::Status(grpc::StatusCode::FAILED_PRECONDITION, ctx_.replay_controller.LastError());
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(50));
  }

  ctx_.sim.event_bus().Unsubscribe(handle);
  return grpc::Status::OK;
}

grpc::Status ReplayServiceImpl::PauseReplay(grpc::ServerContext*, const boat::v1::PauseReplayRequest* request,
                                            boat::v1::ReplayControlResponse* response) {
  try {
    std::lock_guard<std::mutex> lock(replay_mutex_);
    const auto it = active_replays_.find(request->replay_id());
    if (it == active_replays_.end()) {
      return grpc::Status(grpc::StatusCode::NOT_FOUND, "replay not found");
    }
    ctx_.replay_controller.Pause();
    response->set_accepted(true);
    response->set_replay_id(request->replay_id());
    return grpc::Status::OK;
  } catch (const std::exception& ex) {
    return MapReplayException(ex);
  } catch (...) {
    return grpc::Status(grpc::StatusCode::INTERNAL, "unexpected pause replay error");
  }
}

grpc::Status ReplayServiceImpl::ResumeReplay(grpc::ServerContext*, const boat::v1::ResumeReplayRequest* request,
                                             boat::v1::ReplayControlResponse* response) {
  try {
    std::lock_guard<std::mutex> lock(replay_mutex_);
    const auto it = active_replays_.find(request->replay_id());
    if (it == active_replays_.end()) {
      return grpc::Status(grpc::StatusCode::NOT_FOUND, "replay not found");
    }
    ctx_.replay_controller.Resume();
    response->set_accepted(true);
    response->set_replay_id(request->replay_id());
    return grpc::Status::OK;
  } catch (const std::exception& ex) {
    return MapReplayException(ex);
  } catch (...) {
    return grpc::Status(grpc::StatusCode::INTERNAL, "unexpected resume replay error");
  }
}

grpc::Status ReplayServiceImpl::StopReplay(grpc::ServerContext*, const boat::v1::StopReplayRequest* request,
                                           boat::v1::ReplayControlResponse* response) {
  try {
    std::lock_guard<std::mutex> lock(replay_mutex_);
    const auto it = active_replays_.find(request->replay_id());
    if (it == active_replays_.end()) {
      return grpc::Status(grpc::StatusCode::NOT_FOUND, "replay not found");
    }
    ctx_.replay_controller.Stop();
    active_replays_.erase(it);
    response->set_accepted(true);
    response->set_replay_id(request->replay_id());
    return grpc::Status::OK;
  } catch (const std::exception& ex) {
    return MapReplayException(ex);
  } catch (...) {
    return grpc::Status(grpc::StatusCode::INTERNAL, "unexpected stop replay error");
  }
}

grpc::Status ReplayServiceImpl::ImportTraceData(grpc::ServerContext*, const boat::v1::ImportTraceDataRequest* request,
                                                boat::v1::ReplayControlResponse* response) {
  try {
    if (request->trace_id().empty()) {
      return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT, "trace_id must be non-empty");
    }
    if (request->data().empty()) {
      return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT, "trace data must be non-empty");
    }

    const std::string storage_path = "/tmp/" + request->trace_id() + ".trace";

    boat::store::TraceRecord meta;
    meta.id = request->trace_id();
    meta.simulation_id = "";
    meta.format = boat::store::TraceRecord::Format::BINARY;
    meta.storage_path = storage_path;

    std::span<const std::uint8_t> data_span(
        reinterpret_cast<const std::uint8_t*>(request->data().data()),
        request->data().size());

    ctx_.trace_store.WriteTrace(meta, data_span);

    response->set_accepted(true);
    response->set_replay_id(request->trace_id());
    return grpc::Status::OK;
  } catch (const std::exception& ex) {
    return MapReplayException(ex);
  } catch (...) {
    return grpc::Status(grpc::StatusCode::INTERNAL, "unexpected import trace error");
  }
}

grpc::Status ReplayServiceImpl::StartReplayFromEvents(grpc::ServerContext*,
                                                       const boat::v1::StartReplayFromEventsRequest* request,
                                                       boat::v1::ReplayControlResponse* response) {
  try {
    if (request->simulation_id().empty()) {
      return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT, "simulation_id must be non-empty");
    }

    boat::store::EventFilter filter;
    filter.simulation_id = request->simulation_id();
    if (!request->signal_id().empty()) {
      filter.signal_id = request->signal_id();
    }
    if (request->tick_min() > 0) {
      filter.tick_min = request->tick_min();
    }
    if (request->tick_max() > 0) {
      filter.tick_max = request->tick_max();
    }

    boat::replay::ReplayConfig cfg;
    cfg.speed = ProtoSpeedToInternal(request->speed());
    cfg.speed_multiplier = request->speed_multiplier() > 0.0 ? request->speed_multiplier() : 1.0;
    ctx_.replay_controller.StartFromEvents(filter, cfg);

    const std::string replay_id = "evtstore_replay_" + request->simulation_id();
    {
      std::lock_guard<std::mutex> lock(replay_mutex_);
      active_replays_[replay_id] = boat::replay::ReplayConfig{};
    }

    response->set_accepted(true);
    response->set_replay_id(replay_id);
    return grpc::Status::OK;
  } catch (const std::exception& ex) {
    return MapReplayException(ex);
  } catch (...) {
    return grpc::Status(grpc::StatusCode::INTERNAL, "unexpected replay from events error");
  }
}

}  // namespace boat::gateway
