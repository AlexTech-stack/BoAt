// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#include "simulation_service_impl.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <exception>
#include <memory>
#include <random>
#include <stdexcept>
#include <string>
#include <thread>
#include <variant>
#include <vector>

#include "can_bus_registry.h"
#include "core/signal/signal_router.h"
#include "ethernet_bus_registry.h"

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

SimulationServiceImpl::SimulationServiceImpl(
    boat::core::SimulationContext& sim,
    boat::hil::CanBusRegistry& can_registry,
    boat::hil::EthernetBusRegistry& eth_registry)
    : sim_(sim), can_registry_(can_registry), eth_registry_(eth_registry) {}

void SimulationServiceImpl::CleanupRuntime() {
  // Serialises with an in-flight on_tick: SetOnTickHook takes the same mutex
  // RunPipeline holds while calling the hook.
  sim_.scheduler().SetOnTickHook(nullptr);
  if (can_rx_sub_id_.has_value()) {
    can_registry_.Unsubscribe(*can_rx_sub_id_);
    can_rx_sub_id_.reset();
  }
  if (eth_rx_sub_id_.has_value()) {
    eth_registry_.Unsubscribe(*eth_rx_sub_id_);
    eth_rx_sub_id_.reset();
  }
  sim_.plugin_manager().ShutdownAll();
}

void SimulationServiceImpl::PrepareFreshRun() {
  CleanupRuntime();
  sim_.scheduler().Reset();
  sim_.fault_injector().Clear();
  // Events published but not yet dispatched belong to the run that just
  // ended; the first tick of the fresh run must not deliver them.
  sim_.event_bus().Clear();
}

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
    // A definition created while nothing is live starts from tick 0. The clock
    // is gateway-scoped, so without this a "fresh" simulation inherited the
    // previous run's counter (observed: tick 11911 before it had ever been
    // started). While a session is RUNNING or PAUSED the engine is left alone
    // -- the new id is a definition staged for later, documented in
    // simulation.proto.
    {
      const auto state = sim_.state_machine().Current();
      if (state == boat::core::SimState::IDLE || state == boat::core::SimState::STOPPED) {
        PrepareFreshRun();
      }
    }
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
    {
      // This gateway hosts one live session; `simulation_id` selects a stored
      // scenario, not an instance. Refuse to start a different definition over
      // a running one instead of silently driving the shared state.
      std::lock_guard<std::mutex> lock(active_mutex_);
      if (!active_simulation_id_.empty() &&
          active_simulation_id_ != request->simulation_id()) {
        return grpc::Status(
            grpc::StatusCode::FAILED_PRECONDITION,
            "simulation '" + active_simulation_id_ +
                "' is already active; stop or reset it before starting another");
      }
    }
    {
      const auto current = sim_.state_machine().Current();
      if (current == boat::core::SimState::RUNNING) {
        // Already running — idempotent, nothing to do.
        FillSimulation(request->simulation_id(), scenario, response->mutable_simulation());
        return grpc::Status::OK;
      }
      // Allow STOPPED → IDLE → RUNNING so callers don't need an explicit reset.
      if (current == boat::core::SimState::STOPPED) {
        sim_.state_machine().Transition(boat::core::SimState::IDLE);
      }
      if (!sim_.state_machine().Transition(boat::core::SimState::RUNNING)) {
        return grpc::Status(grpc::StatusCode::FAILED_PRECONDITION, "invalid state transition to RUNNING");
      }
    }
    // Load plugins declared in the scenario (idempotent: unload first if present).
    sim_.plugin_manager().ShutdownAll();
    sim_.plugin_manager().SetPublisher(
        [this](const char* signal_id, std::uint64_t tick, double value) {
          boat::core::SignalEvent ev{};
          ev.signal_id = static_cast<std::uint64_t>(std::hash<std::string>{}(signal_id));
          ev.tick      = tick;
          ev.value     = value;
          sim_.signal_router().Publish(ev);
        });
    // v8: Wire unified frame publisher for simulation-scoped plugins.
    sim_.plugin_manager().SetFramePublisher(
        [this](const BoatFrame& f) {
          switch (f.bus_type) {
            case BOAT_BUS_CAN:
            case BOAT_BUS_CANFD: {
              const bool is_fd = f.bus_type == BOAT_BUS_CANFD;
              const bool is_rtr = (f.meta.can.flags & boat::hil::kCanFlagRtr) != 0;
              const std::size_t payload_len = f.payload == nullptr ? 0 : f.payload_len;
              if (f.payload == nullptr && f.payload_len != 0) {
                std::fprintf(stderr, "[Simulation] CAN frame has null payload\n");
                break;
              }
              if (is_fd && is_rtr) {
                std::fprintf(stderr, "[Simulation] rejecting FD+RTR frame\n");
                break;
              }
              if (is_rtr) {
                if (payload_len != 0 || f.meta.can.dlc > 8) {
                  std::fprintf(stderr, "[Simulation] rejecting RTR frame with payload/DLC\n");
                  break;
                }
              } else if (f.meta.can.dlc != payload_len ||
                         payload_len > (is_fd ? 64U : 8U)) {
                std::fprintf(stderr,
                             "[Simulation] rejecting CAN frame with dlc=%u payload=%zu\n",
                             static_cast<unsigned>(f.meta.can.dlc), payload_len);
                break;
              }

              boat::hil::CanFrame cf{};
              cf.can_id = f.meta.can.can_id;
              cf.dlc    = f.meta.can.dlc;
              cf.flags  = f.meta.can.flags;
              if (is_fd) {
                cf.flags |= boat::hil::kCanFdFlagFdf;
              } else {
                cf.flags &= static_cast<std::uint8_t>(~boat::hil::kCanFdFlagFdf);
              }
              if (!is_rtr && payload_len > 0) std::memcpy(cf.data, f.payload, payload_len);
              if (f.iface && f.iface[0])
                (void)can_registry_.SendFrame(f.iface, cf);
              else
                (void)can_registry_.SendFrameAll(cf);
              break;
            }
            case BOAT_BUS_ETHERNET: {
              if (f.payload == nullptr && f.payload_len != 0) {
                std::fprintf(stderr, "[Simulation] Ethernet frame has null payload\n");
                break;
              }
              if (f.payload_len > 1500) {
                std::fprintf(stderr, "[Simulation] rejecting oversize Ethernet payload\n");
                break;
              }
              boat::hil::EthernetFrame ef{};
              std::memcpy(ef.dst_mac, f.meta.eth.dst_mac, 6);
              std::memcpy(ef.src_mac, f.meta.eth.src_mac, 6);
              ef.ethertype = f.meta.eth.ethertype;
              ef.vlan_id   = f.meta.eth.vlan_id;
              if (f.payload && f.payload_len > 0)
                ef.payload.assign(f.payload, f.payload + f.payload_len);
              if (f.iface && f.iface[0])
                (void)eth_registry_.SendFrame(f.iface, ef);
              else
                (void)eth_registry_.SendFrameAll(ef);
              break;
            }
            default:
              break;
          }
        });
    // v8: Subscribe to all frames and dispatch via unified DispatchFrame.
    if (can_rx_sub_id_.has_value()) {
      can_registry_.Unsubscribe(*can_rx_sub_id_);
    }
    can_rx_sub_id_ = can_registry_.Subscribe(
        "",  // all interfaces
        [this](const boat::hil::CanFrame& f, const std::string& iface) {
          (void)iface;
          const bool is_rtr = (f.flags & boat::hil::kCanFlagRtr) != 0;
          std::vector<uint8_t> payload;
          if (!is_rtr) {
            payload.assign(f.data, f.data + f.dlc);
          }
          const bool is_fd = (f.flags & boat::hil::kCanFdFlagFdf) != 0;
          auto core_frame = boat::core::Frame::FromCan("", f.can_id, f.dlc, f.flags,
                                                        std::move(payload), is_fd,
                                                        /*preserve_dlc=*/is_rtr);
          BoatFrame abi{};
          core_frame.ToAbi(&abi);
          sim_.plugin_manager().DispatchFrame(abi);
        });
    if (eth_rx_sub_id_.has_value()) {
      eth_registry_.Unsubscribe(*eth_rx_sub_id_);
    }
    eth_rx_sub_id_ = eth_registry_.Subscribe(
        "", 0,
        [this](const boat::hil::EthernetFrame& f, const std::string& iface) {
          (void)iface;
          auto core_frame = boat::core::Frame::FromEthernet(
              "", const_cast<uint8_t*>(f.dst_mac), const_cast<uint8_t*>(f.src_mac),
              f.ethertype, f.vlan_id,
              nullptr, 0, nullptr, f.payload);
          BoatFrame abi{};
          core_frame.ToAbi(&abi);
          sim_.plugin_manager().DispatchFrame(abi);
        });
    for (const auto& plugin_ref : scenario.plugins) {
      try {
        sim_.plugin_manager().Load(plugin_ref.so_path, plugin_ref.config_json);
      } catch (const std::exception& ex) {
        return grpc::Status(grpc::StatusCode::INTERNAL,
                            std::string("failed to load plugin: ") + ex.what());
      }
    }

    // Wire plugin ticks into the scheduler loop.
    sim_.scheduler().SetOnTickHook(
        [this](std::uint64_t tick) { sim_.plugin_manager().TickAll(tick); });

    // The scenario's own seed decides this run's random stream. The engine is
    // gateway-scoped and used to keep whatever seed the gateway started with,
    // so a scenario's `"seed": N` was parsed and then silently ignored
    // (issue #53): two scenarios asking for different streams got the same one.
    sim_.determinism().SetSeed(scenario.seed);

    sim_.scheduler().Start();   // no-op if already running (e.g. PAUSED→RUNNING)
    sim_.scheduler().Resume();  // clears paused_ so the tick phase starts ticking
    {
      std::lock_guard<std::mutex> lock(active_mutex_);
      active_simulation_id_ = request->simulation_id();
    }
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
    if (!sim_.state_machine().Transition(boat::core::SimState::PAUSED)) {
      return grpc::Status(grpc::StatusCode::FAILED_PRECONDITION, "invalid state transition to PAUSED");
    }
    sim_.scheduler().Pause();
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
    if (sim_.state_machine().Current() != boat::core::SimState::PAUSED) {
      return grpc::Status(grpc::StatusCode::FAILED_PRECONDITION, "simulation must be paused");
    }
    sim_.scheduler().Step(request->ticks());
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
    // Clean slate: same teardown StopSimulation does, plus the rewind. Tick 0,
    // no leftover registry subscriptions, no plugins, no queued events, no
    // scheduled faults. Valid from RUNNING/PAUSED directly (state machine
    // gained the edge), and from STOPPED/ERROR; IDLE is already where we want
    // to be, so the reset is an idempotent success.
    PrepareFreshRun();
    // A reset run is a rerun of this scenario, so it replays this scenario's
    // seed rather than the gateway default (issue #53).
    sim_.determinism().SetSeed(scenario.seed);
    if (sim_.state_machine().Current() != boat::core::SimState::IDLE &&
        !sim_.state_machine().Transition(boat::core::SimState::IDLE)) {
      return grpc::Status(grpc::StatusCode::FAILED_PRECONDITION, "invalid state transition to IDLE");
    }
    {
      std::lock_guard<std::mutex> lock(active_mutex_);
      active_simulation_id_.clear();
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
    if (sim_.state_machine().Current() == boat::core::SimState::STOPPED) {
      // Already stopped — idempotent.
      FillSimulation(request->simulation_id(), scenario, response->mutable_simulation());
      return grpc::Status::OK;
    }
    CleanupRuntime();
    sim_.scheduler().Stop();
    {
      std::lock_guard<std::mutex> lock(active_mutex_);
      active_simulation_id_.clear();
    }
    if (!sim_.state_machine().Transition(boat::core::SimState::STOPPED)) {
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
  auto changed = std::make_shared<std::atomic<bool>>(true);
  const auto observer_token = sim_.state_machine().OnTransition([changed](boat::core::SimState, boat::core::SimState) {
    changed->store(true, std::memory_order_release);
  });
  const auto unregister = [&]() { (void)sim_.state_machine().RemoveObserver(observer_token); };

  while (!context->IsCancelled()) {
    if (changed->exchange(false, std::memory_order_acq_rel)) {
      boat::v1::SimulationResponse response;
      GetSimulationState(nullptr, request, &response);
      if (!writer->Write(response)) {
        unregister();
        break;
      }
      const auto state = sim_.state_machine().Current();
      if (state == boat::core::SimState::STOPPED || state == boat::core::SimState::ERROR) {
        unregister();
        break;
      }
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(50));
  }
  unregister();
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
  out->set_state(ToProtoState(sim_.state_machine().Current()));
  out->set_tick(sim_.clock().tick());
}

}  // namespace boat::gateway
