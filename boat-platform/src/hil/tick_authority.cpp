// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#include "tick_authority.h"

#include <exception>
#include <utility>

namespace boat::hil {

TickAuthority::~TickAuthority() { Stop(); }

void TickAuthority::AddPhase(std::string name, Phase fn) {
  if (!fn) return;
  if (running_.load(std::memory_order_acquire)) return;
  std::lock_guard<std::mutex> lock(mutex_);
  phases_.push_back(Entry{std::move(name), std::move(fn)});
}

std::vector<std::string> TickAuthority::PhaseNames() const {
  std::lock_guard<std::mutex> lock(mutex_);
  std::vector<std::string> names;
  names.reserve(phases_.size());
  for (const auto& e : phases_) names.push_back(e.name);
  return names;
}

void TickAuthority::RunPhases(std::uint64_t tick) {
  // Snapshot outside the lock so a phase may touch the authority (or block)
  // without deadlocking against AddPhase/PhaseNames -- same discipline
  // PluginManager::TickAll uses for its plugin list.
  std::vector<Entry> snapshot;
  {
    std::lock_guard<std::mutex> lock(mutex_);
    snapshot = phases_;
  }
  for (const auto& e : snapshot) {
    try {
      e.fn(tick);
    } catch (...) {
      // Swallowing here is deliberate: the clock must keep advancing for every
      // other phase even when one throws. PhaseErrorCount() makes it visible.
      phase_errors_.fetch_add(1, std::memory_order_release);
    }
  }
  current_tick_.store(tick, std::memory_order_release);
}

bool TickAuthority::Start(std::chrono::nanoseconds interval, TimeSource source) {
  bool expected = false;
  if (!running_.compare_exchange_strong(expected, true, std::memory_order_acq_rel)) {
    return false;  // already running
  }
  timer_ = TickTimer::Create(interval, source);
  thread_ = std::thread(&TickAuthority::Loop, this);
  return true;
}

void TickAuthority::Loop() {
  std::uint64_t tick = 0;
  while (running_.load(std::memory_order_acquire)) {
    if (!timer_->WaitForNextTick()) break;
    if (!running_.load(std::memory_order_acquire)) break;
    RunPhases(++tick);
  }
}

void TickAuthority::Stop() {
  if (!running_.exchange(false, std::memory_order_acq_rel)) {
    // Not running; still join a thread left over from a previous Start().
    if (thread_.joinable()) thread_.join();
    return;
  }
  if (timer_) timer_->Stop();
  if (thread_.joinable()) thread_.join();
  timer_.reset();
}

void TickAuthority::StepFor(std::uint64_t n) {
  if (running_.load(std::memory_order_acquire)) return;
  std::uint64_t tick = current_tick_.load(std::memory_order_acquire);
  for (std::uint64_t i = 0; i < n; ++i) RunPhases(++tick);
}

}  // namespace boat::hil
