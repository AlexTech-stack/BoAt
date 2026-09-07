// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include <atomic>
#include <chrono>
#include <cstdint>
#include <memory>
#include <mutex>

namespace boat::hil {

/* Which clock a tick timer runs against.
 *
 *   kRealTime — ticks track wall time. Required whenever the gateway drives
 *               physical hardware: a 10 ms cyclic frame has to actually leave
 *               the adapter every 10 ms.
 *   kVirtual  — ticks advance logically, as fast as the caller can consume
 *               them. Waiting is a no-op, so a run's tick boundaries no longer
 *               depend on host scheduling. For SIL and CI only; a virtual
 *               clock driving real hardware is meaningless.
 *
 * Selected by BOAT_TIME_SOURCE=realtime|virtual, defaulting to realtime. */
enum class TimeSource {
  kRealTime,
  kVirtual,
};

/* Abstract tick timer: blocks until the next tick boundary.
 *
 * Backends:
 *   TimerfdTickTimer  — Linux timerfd (μs–ms range, drift-free). Default.
 *   VirtualTickTimer  — logical clock, never blocks (BOAT_TIME_SOURCE=virtual)
 * The portable SleepTickTimer is provided as a non-Linux fallback but is
 * never selected by the factory — this codebase is Linux-only.
 */
class TickTimer {
 public:
  virtual ~TickTimer() = default;

  /* Initialise the timer.  Returns true on success. */
  virtual bool Init(std::chrono::nanoseconds interval) = 0;

  /* Block until the next tick.  Returns false if stopped. */
  virtual bool WaitForNextTick() = 0;

  /* Block until an absolute time point (drift-free).  Returns false if
   * stopped.  Timers implement via timerfd+TFD_TIMER_ABSTIME. */
  virtual bool WaitUntil(std::chrono::steady_clock::time_point deadline) = 0;

  /* Stop the timer (may interrupt a blocked WaitForNextTick). */
  virtual void Stop() = 0;

  /* Current tick count (monotonic). */
  virtual uint64_t TickCount() const = 0;

  /* Elapsed nanoseconds since Init(). */
  virtual std::chrono::nanoseconds Elapsed() const = 0;

  /* Factory honouring BOAT_TIME_SOURCE (realtime by default). */
  static std::unique_ptr<TickTimer> Create(std::chrono::nanoseconds interval);

  /* Factory with an explicit source, ignoring the environment. Use this on
     paths that must never run virtual regardless of how the process was
     configured. */
  static std::unique_ptr<TickTimer> Create(std::chrono::nanoseconds interval,
                                           TimeSource source);

  /* Parse BOAT_TIME_SOURCE. Unset, empty, or unrecognised yields kRealTime --
     an unreadable value must never silently make a HIL run virtual. */
  static TimeSource TimeSourceFromEnv();
};

/* Portable fallback using std::this_thread::sleep_for/sleep_until.
 * Never selected by TickTimer::Create on Linux — retained so the
 * class hierarchy compiles on non-Linux platforms. */
class SleepTickTimer final : public TickTimer {
 public:
  bool Init(std::chrono::nanoseconds interval) override;
  bool WaitForNextTick() override;
  bool WaitUntil(std::chrono::steady_clock::time_point deadline) override;
  void Stop() override;
  uint64_t TickCount() const override { return tick_count_; }
  std::chrono::nanoseconds Elapsed() const override;

 private:
  std::chrono::nanoseconds  interval_{};
  uint64_t                  tick_count_{0};
  std::chrono::steady_clock::time_point start_;
  bool                      running_{false};
};

/* Sole backend on Linux: Linux timerfd with absolute-time scheduling.
 * Selected by TickTimer::Create for all intervals.  Drift-free. */
class TimerfdTickTimer final : public TickTimer {
 public:
  ~TimerfdTickTimer() override { Stop(); }

  bool Init(std::chrono::nanoseconds interval) override;
  bool WaitForNextTick() override;
  bool WaitUntil(std::chrono::steady_clock::time_point deadline) override;
  void Stop() override;
  uint64_t TickCount() const override { return tick_count_; }
  std::chrono::nanoseconds Elapsed() const override;

 private:
  int                       fd_{-1};
  std::chrono::nanoseconds  interval_{};
  uint64_t                  tick_count_{0};
  std::chrono::steady_clock::time_point start_;
};

/* Logical clock: advances on demand instead of waiting.
 *
 * WaitForNextTick() moves virtual now forward by one interval and returns
 * immediately; WaitUntil(deadline) jumps virtual now to the deadline. Both
 * preserve the ordering a real timer would produce, because callers always
 * pass monotonically non-decreasing deadlines -- what they no longer preserve
 * is any relationship to wall time, which is the entire point.
 *
 * Virtual now is anchored to a real steady_clock reading taken in Init(), so
 * a caller that computes deadlines from its own steady_clock::now() (as
 * ReplayController does) still lands on sensible offsets.
 *
 * Thread-safe: Stop() may be called from another thread while a wait is in
 * flight, matching the TimerfdTickTimer contract. */
class VirtualTickTimer final : public TickTimer {
 public:
  bool Init(std::chrono::nanoseconds interval) override;
  bool WaitForNextTick() override;
  bool WaitUntil(std::chrono::steady_clock::time_point deadline) override;
  void Stop() override;
  uint64_t TickCount() const override;
  std::chrono::nanoseconds Elapsed() const override;

 private:
  mutable std::mutex        mutex_;
  std::atomic<bool>         stopped_{false};
  bool                      initialised_{false};
  std::chrono::nanoseconds  interval_{};
  uint64_t                  tick_count_{0};
  std::chrono::steady_clock::time_point start_{};
  std::chrono::steady_clock::time_point virtual_now_{};
};

}  // namespace boat::hil
