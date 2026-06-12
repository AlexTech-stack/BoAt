#pragma once

#include <chrono>
#include <cstdint>
#include <memory>

namespace boat::hil {

/* Abstract tick timer: blocks until the next tick boundary.
 *
 * Two implementations:
 *   SleepTickTimer    — std::this_thread::sleep_for (1-10ms range)
 *   TimerfdTickTimer  — Linux timerfd (1μs-1ms range, high precision)
 */
class TickTimer {
 public:
  virtual ~TickTimer() = default;

  /* Initialise the timer.  Returns true on success. */
  virtual bool Init(std::chrono::nanoseconds interval) = 0;

  /* Block until the next tick.  Returns false if stopped. */
  virtual bool WaitForNextTick() = 0;

  /* Stop the timer (may interrupt a blocked WaitForNextTick). */
  virtual void Stop() = 0;

  /* Current tick count (monotonic). */
  virtual uint64_t TickCount() const = 0;

  /* Elapsed nanoseconds since Init(). */
  virtual std::chrono::nanoseconds Elapsed() const = 0;

  /* Factory: create the best backend for the given interval.
   * For interval < 1ms creates TimerfdTickTimer, else SleepTickTimer. */
  static std::unique_ptr<TickTimer> Create(std::chrono::nanoseconds interval);
};

/* Low-precision backend using std::this_thread::sleep_for.
 * Suitable for 1-10ms ticks.  Portable across POSIX systems. */
class SleepTickTimer final : public TickTimer {
 public:
  bool Init(std::chrono::nanoseconds interval) override;
  bool WaitForNextTick() override;
  void Stop() override;
  uint64_t TickCount() const override { return tick_count_; }
  std::chrono::nanoseconds Elapsed() const override;

 private:
  std::chrono::nanoseconds  interval_{};
  uint64_t                  tick_count_{0};
  std::chrono::steady_clock::time_point start_;
  bool                      running_{false};
};

/* High-precision backend using Linux timerfd.
 * Suitable for 1μs-1ms ticks.  Absolute-time scheduling, no drift.
 * Linux-only. */
class TimerfdTickTimer final : public TickTimer {
 public:
  ~TimerfdTickTimer() override { Stop(); }

  bool Init(std::chrono::nanoseconds interval) override;
  bool WaitForNextTick() override;
  void Stop() override;
  uint64_t TickCount() const override { return tick_count_; }
  std::chrono::nanoseconds Elapsed() const override;

 private:
  int                       fd_{-1};
  std::chrono::nanoseconds  interval_{};
  uint64_t                  tick_count_{0};
  std::chrono::steady_clock::time_point start_;
};

}  // namespace boat::hil
