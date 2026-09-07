// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#include "scheduler/tick_scheduler.h"

#include "determinism/determinism_engine.h"

namespace boat::core {

TickScheduler::TickScheduler(SimClock& clock, EventBus& event_bus,
                             DeterminismEngine& determinism, std::size_t /*thread_count*/)
    : clock_(clock), event_bus_(event_bus), determinism_(determinism) {}

TickScheduler::~TickScheduler() { Stop(); }

void TickScheduler::SetOnTickHook(std::function<void(std::uint64_t)> hook) {
  std::lock_guard<std::mutex> lock(on_tick_hook_mutex_);
  on_tick_hook_ = std::move(hook);
}

void TickScheduler::Start() {
  if (running_.exchange(true, std::memory_order_acq_rel)) return;
  paused_.store(false, std::memory_order_release);
}

void TickScheduler::Pause() { paused_.store(true, std::memory_order_release); }

void TickScheduler::Resume() { paused_.store(false, std::memory_order_release); }

void TickScheduler::Stop() {
  if (!running_.exchange(false, std::memory_order_acq_rel)) return;
  paused_.store(false, std::memory_order_release);
}

void TickScheduler::Step(std::uint64_t n) {
  for (std::uint64_t i = 0; i < n; ++i) AdvanceTick();
}

void TickScheduler::TickIfRunning() {
  if (!running_.load(std::memory_order_acquire)) return;
  if (paused_.load(std::memory_order_acquire)) return;
  AdvanceTick();
}

void TickScheduler::AdvanceTick() {
  std::lock_guard<std::mutex> lock(tick_mutex_);
  RunPipeline(clock_.tick() + 1);
}

void TickScheduler::RunPipeline(std::uint64_t tick) {
  // ── Deterministic Tick Pipeline ──────────────────────────────────────
  //
  // Every simulation tick executes these steps in strict order.  New
  // features MUST fit into one of these steps rather than introducing
  // new execution phases outside the pipeline.
  //
  //   1. determinism_.BeforeTick(tick)
  //      Reseed the PRNG for this tick so the same (seed, tick) pair
  //      yields the same random stream on every run.
  //
  //   2. event_bus_.Dispatch()
  //      Deliver everything queued by the previous tick.  Subscribers
  //      observe a stable snapshot: nothing published during this
  //      dispatch is delivered until the next tick.
  //
  //   3. on_tick_hook_(tick)
  //      Plugin ticks.  Plugins read the state dispatched in step 2 and
  //      may publish signals, CAN frames, ETH frames, PDU frames, or
  //      bus-signal values.  Those outputs are queued for the next tick's
  //      Dispatch() — they do NOT take effect immediately.
  //
  //   4. clock_.Step()
  //      Advance the simulation tick counter.  This is the only place
  //      tick is incremented.
  //
  // The pipeline is identical whether the tick came from the TickAuthority
  // phase (TickIfRunning) or from a manual Step(); tick_mutex_ serialises
  // the two so they cannot both claim the same tick number.
  // ──────────────────────────────────────────────────────────────────────
  determinism_.BeforeTick(tick);
  event_bus_.Dispatch();
  clock_.Step();
  {
    std::lock_guard<std::mutex> lock(on_tick_hook_mutex_);
    if (on_tick_hook_) on_tick_hook_(tick);
  }
}

}  // namespace boat::core
