#include "simulation_service_impl.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <exception>
#include <random>
#include <stdexcept>
#include <string>
#include <thread>
#include <variant>
#include <vector>

namespace boat::gateway {
namespace {

std::string GenerateId() {
  static std::mt19937_64 rng(std::random_device{}());
  static std::uniform_int_distribution<std::uint64_t> dist;
  return "sim-" + std::to_string(dist(rng));
}

std::size_t ParseToken(const std::string& token) {
  if (token.empty()) {
    return 0;
  }
  return static_cast<std::size_t>(std::stoull(token));
}

grpc::Status MapSimulationException(const std::exception& ex) {
  const std::string message = ex.what();
  if (message.find("not found") != std::string::npos || message.find("missing") != std::string::npos) {
    return grpc::Status(grpc::StatusCode::NOT_FOUND, message);
  }
  if (message.find("invalid") != std::string::npos || message.find("unexpected token") != std::string::npos) {
    return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT, message);
  }
  return grpc::Status(grpc::StatusCode::INTERNAL, message);
}

}  // namespace

SimulationServiceImpl::SimulationServiceImpl(GatewayContext& ctx) : ctx_(ctx) {}

grpc::Status SimulationServiceImpl::CreateSimulation(grpc::ServerContext*, const boat::v1::CreateSimulationRequest* request,
                                                     boat::v1::SimulationResponse* response) {
  try {
    const auto scenario_key = std::string("scenario.") + request->scenario_id();
    const auto stored = config_store_.Get(scenario_key);
    if (!stored.has_value()) {
      return grpc::Status(grpc::StatusCode::NOT_FOUND, "scenario not found");
    }
    if (!std::holds_alternative<std::string>(*stored)) {
      return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT, "stored scenario payload is not text");
    }
    const auto scenario = boat::core::ScenarioLoader::LoadFromJson(std::get<std::string>(*stored));
    const std::string simulation_id = GenerateId();
    {
      std::lock_guard<std::mutex> lock(simulations_mutex_);
      simulations_.emplace(simulation_id, scenario);
    }
    FillSimulation(simulation_id, scenario, response->mutable_simulation());
    return grpc::Status::OK;
  } catch (const std::exception& ex) {
    return MapSimulationException(ex);
  } catch (...) {
    return grpc::Status(grpc::StatusCode::INTERNAL, "unexpected create simulation error");
  }
}

grpc::Status SimulationServiceImpl::StartSimulation(grpc::ServerContext*, const boat::v1::StartSimulationRequest* request,
                                                    boat::v1::SimulationResponse* response) {
  try {
    boat::core::ScenarioDef scenario;
    {
      std::lock_guard<std::mutex> lock(simulations_mutex_);
      const auto it = simulations_.find(request->simulation_id());
      if (it == simulations_.end()) {
        return grpc::Status(grpc::StatusCode::NOT_FOUND, "simulation not found");
      }
      scenario = it->second;
    }
    if (!ctx_.sim_state_machine.Transition(boat::core::SimState::RUNNING)) {
      return grpc::Status(grpc::StatusCode::FAILED_PRECONDITION, "invalid state transition to RUNNING");
    }
    ctx_.tick_scheduler.Start();
    FillSimulation(request->simulation_id(), scenario, response->mutable_simulation());
    return grpc::Status::OK;
  } catch (const std::exception& ex) {
    return MapSimulationException(ex);
  } catch (...) {
    return grpc::Status(grpc::StatusCode::INTERNAL, "unexpected start simulation error");
  }
}

grpc::Status SimulationServiceImpl::PauseSimulation(grpc::ServerContext*, const boat::v1::PauseSimulationRequest* request,
                                                    boat::v1::SimulationResponse* response) {
  try {
    boat::core::ScenarioDef scenario;
    {
      std::lock_guard<std::mutex> lock(simulations_mutex_);
      const auto it = simulations_.find(request->simulation_id());
      if (it == simulations_.end()) {
        return grpc::Status(grpc::StatusCode::NOT_FOUND, "simulation not found");
      }
      scenario = it->second;
    }
    if (!ctx_.sim_state_machine.Transition(boat::core::SimState::PAUSED)) {
      return grpc::Status(grpc::StatusCode::FAILED_PRECONDITION, "invalid state transition to PAUSED");
    }
    ctx_.tick_scheduler.Pause();
    FillSimulation(request->simulation_id(), scenario, response->mutable_simulation());
    return grpc::Status::OK;
  } catch (const std::exception& ex) {
    return MapSimulationException(ex);
  } catch (...) {
    return grpc::Status(grpc::StatusCode::INTERNAL, "unexpected pause simulation error");
  }
}

grpc::Status SimulationServiceImpl::StepSimulation(grpc::ServerContext*, const boat::v1::StepSimulationRequest* request,
                                                   boat::v1::SimulationResponse* response) {
  try {
    boat::core::ScenarioDef scenario;
    {
      std::lock_guard<std::mutex> lock(simulations_mutex_);
      const auto it = simulations_.find(request->simulation_id());
      if (it == simulations_.end()) {
        return grpc::Status(grpc::StatusCode::NOT_FOUND, "simulation not found");
      }
      scenario = it->second;
    }
    if (ctx_.sim_state_machine.Current() != boat::core::SimState::PAUSED) {
      return grpc::Status(grpc::StatusCode::FAILED_PRECONDITION, "simulation must be paused");
    }
    ctx_.tick_scheduler.Step(request->ticks());
    FillSimulation(request->simulation_id(), scenario, response->mutable_simulation());
    return grpc::Status::OK;
  } catch (const std::exception& ex) {
    return MapSimulationException(ex);
  } catch (...) {
    return grpc::Status(grpc::StatusCode::INTERNAL, "unexpected step simulation error");
  }
}

grpc::Status SimulationServiceImpl::ResetSimulation(grpc::ServerContext*, const boat::v1::ResetSimulationRequest* request,
                                                    boat::v1::SimulationResponse* response) {
  try {
    boat::core::ScenarioDef scenario;
    {
      std::lock_guard<std::mutex> lock(simulations_mutex_);
      const auto it = simulations_.find(request->simulation_id());
      if (it == simulations_.end()) {
        return grpc::Status(grpc::StatusCode::NOT_FOUND, "simulation not found");
      }
      scenario = it->second;
    }
    ctx_.tick_scheduler.Stop();
    if (!ctx_.sim_state_machine.Transition(boat::core::SimState::IDLE)) {
      return grpc::Status(grpc::StatusCode::FAILED_PRECONDITION, "invalid state transition to IDLE");
    }
    FillSimulation(request->simulation_id(), scenario, response->mutable_simulation());
    return grpc::Status::OK;
  } catch (const std::exception& ex) {
    return MapSimulationException(ex);
  } catch (...) {
    return grpc::Status(grpc::StatusCode::INTERNAL, "unexpected reset simulation error");
  }
}

grpc::Status SimulationServiceImpl::StopSimulation(grpc::ServerContext*, const boat::v1::StopSimulationRequest* request,
                                                   boat::v1::SimulationResponse* response) {
  try {
    boat::core::ScenarioDef scenario;
    {
      std::lock_guard<std::mutex> lock(simulations_mutex_);
      const auto it = simulations_.find(request->simulation_id());
      if (it == simulations_.end()) {
        return grpc::Status(grpc::StatusCode::NOT_FOUND, "simulation not found");
      }
      scenario = it->second;
    }
    ctx_.tick_scheduler.Stop();
    if (!ctx_.sim_state_machine.Transition(boat::core::SimState::STOPPED)) {
      return grpc::Status(grpc::StatusCode::FAILED_PRECONDITION, "invalid state transition to STOPPED");
    }
    FillSimulation(request->simulation_id(), scenario, response->mutable_simulation());
    return grpc::Status::OK;
  } catch (const std::exception& ex) {
    return MapSimulationException(ex);
  } catch (...) {
    return grpc::Status(grpc::StatusCode::INTERNAL, "unexpected stop simulation error");
  }
}

grpc::Status SimulationServiceImpl::GetSimulationState(grpc::ServerContext*,
                                                       const boat::v1::GetSimulationStateRequest* request,
                                                       boat::v1::SimulationResponse* response) {
  try {
    boat::core::ScenarioDef scenario;
    {
      std::lock_guard<std::mutex> lock(simulations_mutex_);
      const auto it = simulations_.find(request->simulation_id());
      if (it == simulations_.end()) {
        return grpc::Status(grpc::StatusCode::NOT_FOUND, "simulation not found");
      }
      scenario = it->second;
    }
    FillSimulation(request->simulation_id(), scenario, response->mutable_simulation());
    return grpc::Status::OK;
  } catch (const std::exception& ex) {
    return MapSimulationException(ex);
  } catch (...) {
    return grpc::Status(grpc::StatusCode::INTERNAL, "unexpected get simulation state error");
  }
}

grpc::Status SimulationServiceImpl::WatchSimulation(grpc::ServerContext* context,
                                                    const boat::v1::GetSimulationStateRequest* request,
                                                    grpc::ServerWriter<boat::v1::SimulationResponse>* writer) {
  std::atomic<bool> changed{true};
  ctx_.sim_state_machine.OnTransition([&changed](boat::core::SimState, boat::core::SimState) { changed = true; });

  while (!context->IsCancelled()) {
    if (changed.exchange(false)) {
      boat::v1::SimulationResponse response;
      GetSimulationState(nullptr, request, &response);
      if (!writer->Write(response)) {
        break;
      }
      const auto state = ctx_.sim_state_machine.Current();
      if (state == boat::core::SimState::STOPPED || state == boat::core::SimState::ERROR) {
        break;
      }
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(50));
  }
  return grpc::Status::OK;
}

grpc::Status SimulationServiceImpl::ListSimulations(grpc::ServerContext*, const boat::v1::ListSimulationsRequest* request,
                                                    boat::v1::ListSimulationsResponse* response) {
  std::vector<std::pair<std::string, boat::core::ScenarioDef>> entries;
  {
    std::lock_guard<std::mutex> lock(simulations_mutex_);
    entries.insert(entries.end(), simulations_.begin(), simulations_.end());
  }
  const std::size_t offset = ParseToken(request->page().page_token());
  const std::size_t page_size = request->page().page_size() == 0 ? entries.size() : request->page().page_size();
  const std::size_t end = std::min(entries.size(), offset + page_size);
  for (std::size_t i = offset; i < end; ++i) {
    FillSimulation(entries[i].first, entries[i].second, response->add_simulations());
  }
  response->mutable_page()->set_total_size(static_cast<std::uint32_t>(entries.size()));
  if (end < entries.size()) {
    response->mutable_page()->set_next_page_token(std::to_string(end));
  }
  return grpc::Status::OK;
}

boat::v1::SimulationState SimulationServiceImpl::ToProtoState(boat::core::SimState state) {
  switch (state) {
    case boat::core::SimState::IDLE:
      return boat::v1::SIMULATION_STATE_IDLE;
    case boat::core::SimState::RUNNING:
      return boat::v1::SIMULATION_STATE_RUNNING;
    case boat::core::SimState::PAUSED:
      return boat::v1::SIMULATION_STATE_PAUSED;
    case boat::core::SimState::STOPPED:
      return boat::v1::SIMULATION_STATE_STOPPED;
    case boat::core::SimState::ERROR:
      return boat::v1::SIMULATION_STATE_ERROR;
  }
  return boat::v1::SIMULATION_STATE_UNSPECIFIED;
}

void SimulationServiceImpl::FillSimulation(const std::string& simulation_id, const boat::core::ScenarioDef& scenario,
                                           boat::v1::Simulation* out) const {
  out->set_simulation_id(simulation_id);
  out->set_scenario_id(scenario.id);
  out->set_state(ToProtoState(ctx_.sim_state_machine.Current()));
  out->set_tick(0);
}

}  // namespace boat::gateway
