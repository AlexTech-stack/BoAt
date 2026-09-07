// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include <atomic>
#include <chrono>
#include <cstdint>
#include <functional>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "pdu/tick_timer.h"

namespace boat::hil {

/* The single tick authority.
 *
 * Owns one clock and runs a fixed, ordered list of phases on every tick, each
 * to completion, on its own thread. The ordering is the whole point: anything
 * that observes a tick sees the same events in the same sequence, so a run's
 * behaviour stops depending on which of several independent threads happened
 * to wake first.
 *
 * The gateway historically ran the node plugins and the replay engine on two
 * separate wall-clock threads that never agreed with each other. Registering
 * both as phases here is what makes "which tick did this frame land in" a
 * property of the trace rather than of host scheduling.
 *
 * Lives in boat_hil rather than boat_core only because it owns a TickTimer,
 * which does; boat_core cannot link boat_hil without a cycle.
 *
 * Phases are opaque callbacks, so this class knows nothing about plugins or
 * replay and both can depend on it.
 */
class TickAuthority {
 public:
  using Phase = std::function<void(std::uint64_t tick)>;

  TickAuthority() = default;
  ~TickAuthority();

  TickAuthority(const TickAuthority&)            = delete;
  TickAuthority& operator=(const TickAuthority&) = delete;

  /* Register a phase. Phases run in registration order. Call before Start();
     registering afterwards is ignored, since the phase list must not change
     under a running tick. */
  void AddPhase(std::string name, Phase fn);

  /* Begin ticking on a background thread. `interval` sizes the tick, `source`
     picks wall-clock or logical time. Returns false if already running. */
  bool Start(std::chrono::nanoseconds interval, TimeSource source);

  /* Stop the tick thread and join it. Safe to call more than once, and safe
     from a thread other than the one that called Start(). */
  void Stop();

  /* Run exactly `n` ticks synchronously on the calling thread, ignoring the
     clock entirely. The deterministic path: no timer, no waiting, no other
     thread involved. Does nothing while the background thread is running. */
  void StepFor(std::uint64_t n);

  [[nodiscard]] bool IsRunning() const { return running_.load(std::memory_order_acquire); }

  /* The tick most recently completed by every phase. Starts at 0 before the
     first tick, so the first completed tick reports 1. */
  [[nodiscard]] std::uint64_t CurrentTick() const {
    return current_tick_.load(std::memory_order_acquire);
  }

  /* How many times a phase threw. A phase that throws is caught and skipped
     for that tick rather than being allowed to stop the clock -- one
     misbehaving plugin must not strand every other consumer of the tick --
     but the count is exposed so the failure isn't silent. */
  [[nodiscard]] std::uint64_t PhaseErrorCount() const {
    return phase_errors_.load(std::memory_order_acquire);
  }

  [[nodiscard]] std::vector<std::string> PhaseNames() const;

  /* Monotonic nanoseconds on this authority's clock -- real time when running
     a timerfd backend, virtual time when running the logical one. This is what
     the gateway hands plugins as their time source, so a plugin never has to
     read a system clock or infer elapsed time from a tick count. Before Start()
     (and under StepFor) it is derived from the completed tick count. */
  [[nodiscard]] std::uint64_t NowNs() const;

 private:
  void Loop(TickTimer* timer);
  void RunPhases(std::uint64_t tick);

  struct Entry {
    std::string name;
    Phase       fn;
  };

  mutable std::mutex        mutex_;
  std::vector<Entry>        phases_;
  std::thread               thread_;
  std::unique_ptr<TickTimer> timer_;
  std::chrono::nanoseconds  interval_{std::chrono::milliseconds(1)};
  std::atomic<bool>         running_{false};
  std::atomic<std::uint64_t> current_tick_{0};
  std::atomic<std::uint64_t> phase_errors_{0};
};

}  // namespace boat::hil
