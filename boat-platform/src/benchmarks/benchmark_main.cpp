// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

/* Repeatable performance harness for the two project-plan NFRs:
 *
 *   - frame/event throughput: FrameSink -> CanBusRegistry -> driver
 *   - 1 ms tick interval jitter on a real TickAuthority
 *
 * This is intentionally a standalone executable, not a ctest case: CI runner
 * speed varies too much to make a throughput threshold a pass/fail gate. Run
 * it on the machine making the claim and record the numbers in
 * docs/performance-baseline.md.
 *
 * Usage:
 *   boat_benchmark [--frames N] [--ticks N] [--interval-us N]
 *                  [--load-threads N] [--iface vcan0]
 */

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <functional>
#include <memory>
#include <string>
#include <thread>
#include <vector>

#include "can_bus_registry.h"
#include "core/event/event_bus.h"
#include "core/frame.h"
#include "ethernet_bus_registry.h"
#include "gateway/grpc_gateway/frame_sink.h"
#include "hal/hal_driver.h"
#include "tick_authority.h"
#include "virtual/virtual_can_driver.h"

namespace {

using namespace std::chrono_literals;

class CountingCanDriver : public boat::hil::IHalDriver {
 public:
  bool Open() override { return true; }
  void Close() override {}
  bool ReadFrame(boat::hil::CanFrame&) override {
    std::this_thread::sleep_for(1ms);
    return false;
  }
  bool WriteFrame(const boat::hil::CanFrame&) override {
    writes.fetch_add(1, std::memory_order_relaxed);
    return true;
  }
  boat::hil::CanInterfaceInfo GetInfo() const override { return {}; }

  std::atomic<std::uint64_t> writes{0};
};

class CountingVirtualCanDriver : public boat::hil::VirtualCanDriver {
 public:
  explicit CountingVirtualCanDriver(std::string iface)
      : VirtualCanDriver(std::move(iface)) {}

  bool WriteFrame(const boat::hil::CanFrame& frame) override {
    if (!VirtualCanDriver::WriteFrame(frame)) return false;
    writes.fetch_add(1, std::memory_order_relaxed);
    return true;
  }

  std::atomic<std::uint64_t> writes{0};
};

struct FrameThroughput {
  std::string driver;
  std::uint64_t frames{0};
  double seconds{0.0};
  double frames_per_second{0.0};
};

FrameThroughput MeasureFrameThroughput(std::uint64_t frames,
                                       const std::string& iface) {
  boat::core::EventBus bus;
  boat::hil::CanBusRegistry can_registry;
  boat::hil::EthernetBusRegistry eth_registry;

  std::shared_ptr<boat::hil::IHalDriver> driver;
  std::function<std::uint64_t()> writes;
  std::function<void()> reset_writes;
  std::string driver_label = "synthetic";
  std::string bus_name = "bench0";

  if (iface.empty()) {
    auto synthetic = std::make_shared<CountingCanDriver>();
    auto* synthetic_ptr = synthetic.get();
    writes = [synthetic_ptr] {
      return synthetic_ptr->writes.load(std::memory_order_relaxed);
    };
    reset_writes = [synthetic_ptr] {
      synthetic_ptr->writes.store(0, std::memory_order_relaxed);
    };
    driver = std::move(synthetic);
  } else {
    auto virtual_can = std::make_shared<CountingVirtualCanDriver>(iface);
    auto* virtual_ptr = virtual_can.get();
    writes = [virtual_ptr] {
      return virtual_ptr->writes.load(std::memory_order_relaxed);
    };
    reset_writes = [virtual_ptr] {
      virtual_ptr->writes.store(0, std::memory_order_relaxed);
    };
    driver = std::move(virtual_can);
    driver_label = iface;
    bus_name = iface;
  }

  if (!can_registry.Add(bus_name, driver, bus)) {
    std::fprintf(stderr, "benchmark: failed to add CAN interface '%s'\n",
                 bus_name.c_str());
    std::exit(1);
  }

  boat::gateway::FrameSink sink(can_registry, eth_registry);
  auto frame = boat::core::Frame::FromCan(
      bus_name, 0x123, 8, 0, std::vector<std::uint8_t>(8, 0xAA));

  // Warm caches and the allocator without charging them to the measurement.
  for (std::uint64_t i = 0; i < 10000; ++i) {
    if (!sink.Publish(frame)) {
      std::fprintf(stderr, "benchmark: frame sink rejected a CAN frame on '%s'\n",
                   bus_name.c_str());
      std::exit(1);
    }
  }
  // Reset the counter after warmup. The vcan path has already handed those
  // frames to the kernel; this only removes them from the reported count.
  reset_writes();

  const auto start = std::chrono::steady_clock::now();
  for (std::uint64_t i = 0; i < frames; ++i) {
    if (!sink.Publish(frame)) {
      std::fprintf(stderr, "benchmark: frame sink rejected a CAN frame on '%s'\n",
                   bus_name.c_str());
      std::exit(1);
    }
  }
  const auto elapsed = std::chrono::steady_clock::now() - start;
  const double seconds = std::chrono::duration<double>(elapsed).count();

  const auto written = writes();
  if (written != frames) {
    std::fprintf(stderr, "benchmark: driver received %llu of %llu frames\n",
                 static_cast<unsigned long long>(written),
                 static_cast<unsigned long long>(frames));
    std::exit(1);
  }

  FrameThroughput result;
  result.driver = driver_label;
  result.frames = frames;
  result.seconds = seconds;
  result.frames_per_second = seconds > 0.0 ? static_cast<double>(frames) / seconds : 0.0;
  return result;
}

struct TickJitter {
  std::uint64_t samples{0};
  std::uint64_t interval_us{0};
  double mean_interval_us{0.0};
  double p50_jitter_us{0.0};
  double p95_jitter_us{0.0};
  double p99_jitter_us{0.0};
  double max_jitter_us{0.0};
  double within_1ms_fraction{0.0};
};

double Percentile(const std::vector<std::int64_t>& sorted, double p) {
  if (sorted.empty()) return 0.0;
  const auto idx = static_cast<std::size_t>(
      std::ceil(p * static_cast<double>(sorted.size())) - 1.0);
  return static_cast<double>(sorted[std::min(idx, sorted.size() - 1)]);
}

TickJitter MeasureTickJitter(std::uint64_t ticks, std::uint64_t interval_us,
                             int load_threads) {
  boat::hil::TickAuthority authority;
  std::vector<std::int64_t> deltas_us;
  deltas_us.reserve(static_cast<std::size_t>(ticks));
  std::atomic<std::uint64_t> observed{0};
  bool have_last = false;
  std::chrono::steady_clock::time_point last{};

  authority.AddPhase("measure", [&](std::uint64_t) {
    const auto now = std::chrono::steady_clock::now();
    if (have_last) {
      deltas_us.push_back(
          std::chrono::duration_cast<std::chrono::microseconds>(now - last).count());
    }
    last = now;
    have_last = true;
    observed.fetch_add(1, std::memory_order_release);
  });

  std::atomic<bool> stop_load{false};
  std::vector<std::thread> loaders;
  for (int i = 0; i < load_threads; ++i) {
    loaders.emplace_back([&stop_load] {
      while (!stop_load.load(std::memory_order_acquire)) {
        std::this_thread::yield();
      }
    });
  }

  if (!authority.Start(std::chrono::microseconds(interval_us),
                       boat::hil::TimeSource::kRealTime)) {
    std::fprintf(stderr, "benchmark: tick authority failed to start\n");
    std::exit(1);
  }

  const auto deadline = std::chrono::steady_clock::now() + 20s;
  while (observed.load(std::memory_order_acquire) < ticks &&
         std::chrono::steady_clock::now() < deadline) {
    std::this_thread::sleep_for(1ms);
  }
  authority.Stop();
  stop_load.store(true, std::memory_order_release);
  for (auto& loader : loaders) loader.join();

  if (observed.load(std::memory_order_acquire) < ticks) {
    std::fprintf(stderr, "benchmark: tick authority produced %llu of %llu ticks\n",
                 static_cast<unsigned long long>(observed.load()),
                 static_cast<unsigned long long>(ticks));
    std::exit(1);
  }

  TickJitter result;
  result.interval_us = interval_us;
  result.samples = deltas_us.size();
  if (deltas_us.empty()) return result;

  std::int64_t sum = 0;
  std::vector<std::int64_t> jitter_us;
  jitter_us.reserve(deltas_us.size());
  for (const auto delta : deltas_us) {
    sum += delta;
    jitter_us.push_back(std::llabs(delta - static_cast<std::int64_t>(interval_us)));
  }
  std::sort(jitter_us.begin(), jitter_us.end());
  result.mean_interval_us =
      static_cast<double>(sum) / static_cast<double>(deltas_us.size());
  result.p50_jitter_us = Percentile(jitter_us, 0.50);
  result.p95_jitter_us = Percentile(jitter_us, 0.95);
  result.p99_jitter_us = Percentile(jitter_us, 0.99);
  result.max_jitter_us = static_cast<double>(jitter_us.back());
  const auto within = std::count_if(
      jitter_us.begin(), jitter_us.end(),
      [](std::int64_t value) { return value <= 1000; });
  result.within_1ms_fraction =
      static_cast<double>(within) / static_cast<double>(jitter_us.size());
  return result;
}

}  // namespace

int main(int argc, char** argv) {
  std::uint64_t frames = 1'000'000;
  std::uint64_t ticks = 1000;
  std::uint64_t interval_us = 1000;
  int load_threads = 0;
  std::string iface;

  for (int i = 1; i < argc; ++i) {
    const std::string arg = argv[i];
    auto next = [&](const char* name) -> std::uint64_t {
      if (i + 1 >= argc) {
        std::fprintf(stderr, "benchmark: %s needs a value\n", name);
        std::exit(2);
      }
      return std::strtoull(argv[++i], nullptr, 10);
    };
    auto next_string = [&](const char* name) -> std::string {
      if (i + 1 >= argc) {
        std::fprintf(stderr, "benchmark: %s needs a value\n", name);
        std::exit(2);
      }
      return argv[++i];
    };
    if (arg == "--frames") frames = next("--frames");
    else if (arg == "--ticks") ticks = next("--ticks");
    else if (arg == "--interval-us") interval_us = next("--interval-us");
    else if (arg == "--load-threads") load_threads = static_cast<int>(next("--load-threads"));
    else if (arg == "--iface") iface = next_string("--iface");
    else {
      std::fprintf(stderr,
                   "usage: %s [--frames N] [--ticks N] [--interval-us N] "
                   "[--load-threads N] [--iface NAME]\n",
                   argv[0]);
      return 2;
    }
  }

  const FrameThroughput throughput =
      frames > 0 ? MeasureFrameThroughput(frames, iface) : FrameThroughput{};
  const TickJitter jitter = ticks > 0
                                ? MeasureTickJitter(ticks, interval_us, load_threads)
                                : TickJitter{};

  std::printf("{\n");
  std::printf("  \"frame_sink\": {\"driver\": \"%s\", \"frames\": %llu, "
              "\"seconds\": %.6f, \"frames_per_second\": %.0f},\n",
              throughput.driver.c_str(),
              static_cast<unsigned long long>(throughput.frames),
              throughput.seconds, throughput.frames_per_second);
  std::printf("  \"tick\": {\"samples\": %llu, \"interval_us\": %llu, "
              "\"mean_interval_us\": %.3f, \"p50_jitter_us\": %.3f, "
              "\"p95_jitter_us\": %.3f, \"p99_jitter_us\": %.3f, "
              "\"max_jitter_us\": %.3f, \"within_1ms_fraction\": %.6f, "
              "\"load_threads\": %d}\n",
              static_cast<unsigned long long>(jitter.samples),
              static_cast<unsigned long long>(jitter.interval_us),
              jitter.mean_interval_us, jitter.p50_jitter_us, jitter.p95_jitter_us,
              jitter.p99_jitter_us, jitter.max_jitter_us,
              jitter.within_1ms_fraction, load_threads);
  std::printf("}\n");
  return 0;
}
