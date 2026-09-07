// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <mutex>

#include "event/event_bus.h"
#include "scheduler/sim_clock.h"

namespace boat::core {

class DeterminismEngine;

/* Runs the deterministic tick pipeline for a simulation.
 *
 * TickScheduler owns no thread and no clock. The gateway's TickAuthority
 * (`src/hil/tick_authority.h`) calls TickIfRunning() as one of its ordered
 * phases, so scenario-scoped plugins advance on the same clock as the
 * always-on node plugins and replay delivery rather than on a coordinator
 * thread of their own that agreed with neither.
 *
 * Start()/Pause()/Resume()/Stop() set state that decides whether that phase
 * does anything; Step() advances ticks directly, whatever that state is, which
 * is what `boat sim step` needs.
 */
class TickScheduler {
 public:
  /* `thread_count` is accepted and ignored. It used to size a worker pool that
     existed only so ExecuteTick could enqueue an empty lambda and wait for it,
     which is not a barrier against anything -- nothing else ever enqueued.
     The parameter is kept so SimulationContext and existing callers compile
     unchanged. */
  TickScheduler(SimClock& clock, EventBus& event_bus, DeterminismEngine& determinism,
                std::size_t thread_count = 1);
  ~TickScheduler();

  void Start();
  void Pause();
  void Resume();
  void Stop();

  /* Advance exactly n ticks on the calling thread, regardless of running or
     paused state. Serialised against the tick phase, so stepping a running
     simulation cannot interleave two ticks onto the same tick number. */
  void Step(std::uint64_t n = 1);

  /* Advance one tick if this simulation is running and not paused. Called
     once per tick by the TickAuthority phase; a no-op when no simulation is
     active, which is the common case. */
  void TickIfRunning();

  [[nodiscard]] bool IsRunning() const { return running_.load(std::memory_order_acquire); }
  [[nodiscard]] bool IsPaused() const { return paused_.load(std::memory_order_acquire); }

  /* Optional hook called after every tick (including manual steps).
     Safe to call from any thread. */
  void SetOnTickHook(std::function<void(std::uint64_t)> hook);

 private:
  void AdvanceTick();
  void RunPipeline(std::uint64_t tick);

  SimClock& clock_;
  EventBus& event_bus_;
  DeterminismEngine& determinism_;

  std::atomic<bool> running_{false};
  std::atomic<bool> paused_{false};

  /* Held across the whole pipeline so the tick number is read, used and
     advanced atomically. Without it a manual Step() racing the tick phase can
     compute the same tick twice, and DeterminismEngine::BeforeTick throws on a
     non-increasing tick. */
  std::mutex tick_mutex_;

  std::function<void(std::uint64_t)> on_tick_hook_;
  std::mutex on_tick_hook_mutex_;
};

}  // namespace boat::core
