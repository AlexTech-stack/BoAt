// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#include "fault_service_impl.h"

#include <algorithm>
#include <charconv>
#include <cmath>
#include <limits>
#include <optional>
#include <random>
#include <system_error>

namespace boat::gateway {
namespace {
std::optional<std::size_t> ParseToken(const std::string& token) {
  if (token.empty()) {
    return 0;
  }
  std::size_t value = 0;
  const char* begin = token.data();
  const char* end = begin + token.size();
  const auto [ptr, error] = std::from_chars(begin, end, value);
  if (error != std::errc() || ptr != end) {
    return std::nullopt;
  }
  return value;
}

std::string GenerateFaultId() {
  static std::mt19937_64 rng(std::random_device{}());
  static std::uniform_int_distribution<std::uint64_t> dist;
  return "fault-" + std::to_string(dist(rng));
}
}  // namespace

FaultServiceImpl::FaultServiceImpl(GatewayContext& ctx) : ctx_(ctx) {}

grpc::Status FaultServiceImpl::InjectFault(grpc::ServerContext*, const boat::v1::InjectFaultRequest* request,
                                           boat::v1::InjectFaultResponse* response) {
  if (request->target().empty()) {
    return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT, "target must not be empty");
  }
  if (request->type() == boat::v1::FAULT_TYPE_UNSPECIFIED) {
    return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT, "fault type must be specified");
  }
  switch (request->type()) {
    case boat::v1::FAULT_TYPE_SIGNAL_CORRUPTION:
    case boat::v1::FAULT_TYPE_PACKET_DROP:
    case boat::v1::FAULT_TYPE_DELAY:
    case boat::v1::FAULT_TYPE_SIGNAL_STUCK:
    case boat::v1::FAULT_TYPE_SIGNAL_INVERT:
      break;
    default:
      return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT, "unknown fault type");
  }
  if (!std::isfinite(request->magnitude())) {
    return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT, "magnitude must be finite");
  }
  if (request->end_tick() != 0 && request->end_tick() < request->tick()) {
    return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT,
                        "end_tick must be greater than or equal to tick");
  }
  if (request->type() == boat::v1::FAULT_TYPE_DELAY) {
    if (request->delay_ticks() == 0) {
      return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT,
                          "delay_ticks must be greater than zero for a delay fault");
    }
    if (request->delay_ticks() >
        std::numeric_limits<std::uint64_t>::max() - request->tick()) {
      return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT, "tick + delay_ticks overflows");
    }
  }
  const std::uint64_t end_tick =
      request->end_tick() == 0 ? request->tick() : request->end_tick();
  boat::core::FaultSpec spec{
      .signal_id = static_cast<std::uint64_t>(std::hash<std::string>{}(request->target())),
      .start_tick = request->tick(),
      .end_tick = end_tick,
      .type = ToCoreFaultType(request->type()),
      .magnitude = request->magnitude(),
      .delay_ticks = request->delay_ticks(),
  };
  ctx_.sim.fault_injector().Schedule(spec);

  boat::v1::FaultEvent event;
  event.set_fault_id(GenerateFaultId());
  event.set_simulation_id(request->simulation_id());
  event.set_target(request->target());
  event.set_type(request->type());
  event.set_tick(request->tick());
  event.set_end_tick(end_tick);
  event.set_magnitude(request->magnitude());
  event.set_delay_ticks(request->delay_ticks());
  {
    std::lock_guard<std::mutex> lock(faults_mutex_);
    faults_.push_back(event);
  }
  *response->mutable_fault() = event;
  return grpc::Status::OK;
}

grpc::Status FaultServiceImpl::ListFaults(grpc::ServerContext*, const boat::v1::ListFaultsRequest* request,
                                          boat::v1::ListFaultsResponse* response) {
  std::vector<boat::v1::FaultEvent> filtered;
  {
    std::lock_guard<std::mutex> lock(faults_mutex_);
    for (const auto& fault : faults_) {
      if (request->simulation_id().empty() || fault.simulation_id() == request->simulation_id()) {
        filtered.push_back(fault);
      }
    }
  }
  const auto offset = ParseToken(request->page().page_token());
  if (!offset.has_value()) {
    return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT, "invalid page_token");
  }
  const std::size_t page_size = request->page().page_size() == 0 ? filtered.size() : request->page().page_size();
  const std::size_t end = std::min(filtered.size(), *offset + page_size);
  for (std::size_t i = *offset; i < end; ++i) {
    *response->add_faults() = filtered[i];
  }
  response->mutable_page()->set_total_size(static_cast<std::uint32_t>(filtered.size()));
  if (end < filtered.size()) {
    response->mutable_page()->set_next_page_token(std::to_string(end));
  }
  return grpc::Status::OK;
}

boat::core::FaultType FaultServiceImpl::ToCoreFaultType(boat::v1::FaultType type) {
  switch (type) {
    case boat::v1::FAULT_TYPE_SIGNAL_CORRUPTION:
      return boat::core::FaultType::NOISE;
    case boat::v1::FAULT_TYPE_PACKET_DROP:
      return boat::core::FaultType::DROPOUT;
    case boat::v1::FAULT_TYPE_DELAY:
      return boat::core::FaultType::DELAY;
    case boat::v1::FAULT_TYPE_SIGNAL_STUCK:
      return boat::core::FaultType::STUCK;
    case boat::v1::FAULT_TYPE_SIGNAL_INVERT:
      return boat::core::FaultType::INVERT;
    case boat::v1::FAULT_TYPE_UNSPECIFIED:
      break;
  }
  return boat::core::FaultType::NOISE;
}

}  // namespace boat::gateway
