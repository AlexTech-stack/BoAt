#include <catch2/catch_test_macros.hpp>

#include <atomic>
#include <cstdint>
#include <chrono>
#include <cstring>
#include <span>
#include <stdexcept>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

#include "event/event_bus.h"
#include "event_store/event_store.h"
#include "replay_engine/replay_engine.h"
#include "trace_store/trace_store.h"

using namespace boat::replay;
using namespace boat::store;

namespace {

constexpr std::uint32_t kTraceMagic = 0xB0A7B0A7;

std::vector<std::uint8_t> MakeTraceRecord(std::uint32_t event_type, std::uint64_t tick,
                                          const std::vector<std::uint8_t>& payload) {
  TraceRecordHeader header{};
  header.magic = kTraceMagic;
  header.event_type = event_type;
  header.tick = tick;
  header.wall_time_ns = static_cast<std::int64_t>(tick * 1'000'000ULL);
  header.payload_size = static_cast<std::uint32_t>(payload.size());

  std::vector<std::uint8_t> record(sizeof(header) + payload.size());
  std::memcpy(record.data(), &header, sizeof(header));
  if (!payload.empty()) {
    std::memcpy(record.data() + sizeof(header), payload.data(), payload.size());
  }
  return record;
}

std::vector<std::uint8_t> BuildSequentialTrace(std::uint64_t start_tick, std::uint64_t count) {
  std::vector<std::uint8_t> trace_data;
  for (std::uint64_t i = 0; i < count; ++i) {
    std::uint8_t val = static_cast<std::uint8_t>(i & 0xFF);
    auto record = MakeTraceRecord(100, start_tick + i * 10, {val});
    trace_data.insert(trace_data.end(), record.begin(), record.end());
  }
  return trace_data;
}

struct MockTraceStore : ITraceStore {
  std::unordered_map<std::string, std::vector<std::uint8_t>> traces;
  std::vector<std::string> unmapped;

  void WriteTrace(const TraceRecord& meta, std::span<const std::uint8_t> data) override {
    traces[meta.id] = std::vector<std::uint8_t>(data.begin(), data.end());
  }
  std::span<const std::uint8_t> ReadTraceMmap(const std::string& trace_id) override {
    const auto it = traces.find(trace_id);
    if (it == traces.end()) {
      throw std::runtime_error("trace id not found");
    }
    return it->second;
  }
  std::vector<TraceRecord> ListTraces(const std::string&) override { return {}; }
  std::vector<TraceRecord> ListAllTraces() override { return {}; }
  void UnmapTrace(const std::string& trace_id) override {
    unmapped.push_back(trace_id);
  }
};

struct MockEventStore : IEventStore {
  std::vector<EventRecord> inserted;
  std::vector<EventRecord> QueryResult;

  void InsertBatch(std::span<const EventRecord> events) override {
    for (const auto& e : events) {
      inserted.push_back(e);
    }
  }
  std::vector<EventRecord> Query(const EventFilter&) override { return QueryResult; }
};

}  // namespace

TEST_CASE("ReplayController Start/Stop lifecycle", "[unit][replay]") {
  MockTraceStore trace_store;
  MockEventStore event_store;
  boat::core::EventBus event_bus;
  ReplayController controller(trace_store, event_store, event_bus);

  auto trace_data = BuildSequentialTrace(100, 3);
  trace_store.traces["lifecycle"] = trace_data;

  ReplayConfig config;
  config.trace_id = "lifecycle";
  config.speed = ReplaySpeed::REAL_TIME;
  config.speed_multiplier = 100.0;

  controller.Start(config);
  REQUIRE_FALSE(controller.HasError());

  std::this_thread::sleep_for(std::chrono::milliseconds(50));
  controller.Stop();

  REQUIRE_FALSE(controller.HasError());
  REQUIRE(event_store.inserted.size() == 3);
  REQUIRE(trace_store.unmapped.size() == 1);
  REQUIRE(trace_store.unmapped[0] == "lifecycle");
}

TEST_CASE("ReplayController replays records in tick order", "[unit][replay]") {
  MockTraceStore trace_store;
  MockEventStore event_store;
  boat::core::EventBus event_bus;
  ReplayController controller(trace_store, event_store, event_bus);

  auto trace_data = BuildSequentialTrace(100, 5);
  trace_store.traces["ordered"] = trace_data;

  ReplayConfig config;
  config.trace_id = "ordered";
  config.speed = ReplaySpeed::REAL_TIME;
  config.speed_multiplier = 1000.0;

  controller.Start(config);
  std::this_thread::sleep_for(std::chrono::milliseconds(50));
  controller.Stop();

  REQUIRE_FALSE(controller.HasError());
  REQUIRE(event_store.inserted.size() == 5);
  REQUIRE(event_store.inserted[0].tick == 100);
  REQUIRE(event_store.inserted[1].tick == 110);
  REQUIRE(event_store.inserted[2].tick == 120);
  REQUIRE(event_store.inserted[3].tick == 130);
  REQUIRE(event_store.inserted[4].tick == 140);
}

TEST_CASE("ReplayController publishes events on EventBus", "[unit][replay]") {
  MockTraceStore trace_store;
  MockEventStore event_store;
  boat::core::EventBus event_bus;
  ReplayController controller(trace_store, event_store, event_bus);

  auto trace_data = BuildSequentialTrace(200, 2);
  trace_store.traces["bus_test"] = trace_data;

  std::atomic<int> original_events{0};
  std::atomic<int> replay_events{0};

  event_bus.Subscribe(100, [&](const boat::core::BusEvent&) { ++original_events; });
  event_bus.Subscribe(kReplayBusEventType, [&](const boat::core::BusEvent&) { ++replay_events; });

  ReplayConfig config;
  config.trace_id = "bus_test";
  config.speed = ReplaySpeed::REAL_TIME;
  config.speed_multiplier = 1000.0;

  controller.Start(config);
  std::this_thread::sleep_for(std::chrono::milliseconds(50));
  event_bus.Dispatch();
  controller.Stop();

  REQUIRE(original_events.load() >= 2);
  REQUIRE(replay_events.load() >= 2);
}

TEST_CASE("ReplayController accelerated speed finishes faster than real-time", "[unit][replay]") {
  MockTraceStore trace_store;
  MockEventStore event_store;
  boat::core::EventBus event_bus;
  ReplayController controller(trace_store, event_store, event_bus);

  auto trace_data = BuildSequentialTrace(100, 10);
  trace_store.traces["fast"] = trace_data;

  ReplayConfig config;
  config.trace_id = "fast";
  config.speed = ReplaySpeed::ACCELERATED;
  config.speed_multiplier = 1000.0;

  auto start = std::chrono::steady_clock::now();
  controller.Start(config);
  std::this_thread::sleep_for(std::chrono::milliseconds(100));
  controller.Stop();
  auto elapsed = std::chrono::steady_clock::now() - start;

  REQUIRE_FALSE(controller.HasError());
  REQUIRE(event_store.inserted.size() == 10);
  REQUIRE(elapsed < std::chrono::milliseconds(500));
}

TEST_CASE("ReplayController step-by-step pauses after each record", "[unit][replay]") {
  MockTraceStore trace_store;
  MockEventStore event_store;
  boat::core::EventBus event_bus;
  ReplayController controller(trace_store, event_store, event_bus);

  auto trace_data = BuildSequentialTrace(100, 3);
  trace_store.traces["step"] = trace_data;

  ReplayConfig config;
  config.trace_id = "step";
  config.speed = ReplaySpeed::STEP_BY_STEP;
  config.speed_multiplier = 1.0;

  controller.Start(config);
  std::this_thread::sleep_for(std::chrono::milliseconds(50));
  REQUIRE(event_store.inserted.size() == 1);

  controller.Resume();
  std::this_thread::sleep_for(std::chrono::milliseconds(50));
  REQUIRE(event_store.inserted.size() == 2);

  controller.Resume();
  std::this_thread::sleep_for(std::chrono::milliseconds(50));
  REQUIRE(event_store.inserted.size() == 3);

  controller.Stop();
  REQUIRE_FALSE(controller.HasError());
}

TEST_CASE("ReplayController Pause/Resume suspends and continues replay", "[unit][replay]") {
  MockTraceStore trace_store;
  MockEventStore event_store;
  boat::core::EventBus event_bus;
  ReplayController controller(trace_store, event_store, event_bus);

  auto trace_data = BuildSequentialTrace(100, 200);
  trace_store.traces["pause_test"] = trace_data;

  ReplayConfig config;
  config.trace_id = "pause_test";
  config.speed = ReplaySpeed::REAL_TIME;
  config.speed_multiplier = 2.0;

  controller.Start(config);

  std::this_thread::sleep_for(std::chrono::milliseconds(100));

  controller.Pause();

  std::this_thread::sleep_for(std::chrono::milliseconds(20));

  auto count_after_pause = event_store.inserted.size();
  REQUIRE(count_after_pause > 0);

  std::this_thread::sleep_for(std::chrono::milliseconds(100));

  auto count_while_paused = event_store.inserted.size();
  REQUIRE(count_while_paused <= count_after_pause + 2);

  controller.Resume();

  std::this_thread::sleep_for(std::chrono::milliseconds(100));

  auto count_after_resume = event_store.inserted.size();
  REQUIRE(count_after_resume > count_after_pause);

  controller.Stop();
  REQUIRE_FALSE(controller.HasError());
}

TEST_CASE("ReplayController Seek jumps to requested tick", "[unit][replay]") {
  MockTraceStore trace_store;
  MockEventStore event_store;
  boat::core::EventBus event_bus;
  ReplayController controller(trace_store, event_store, event_bus);

  auto trace_data = BuildSequentialTrace(100, 10);
  trace_store.traces["seek_test"] = trace_data;

  ReplayConfig config;
  config.trace_id = "seek_test";
  config.speed = ReplaySpeed::REAL_TIME;
  config.speed_multiplier = 1000.0;

  controller.Start(config);
  std::this_thread::sleep_for(std::chrono::milliseconds(50));
  controller.Seek(150);
  controller.Resume();
  std::this_thread::sleep_for(std::chrono::milliseconds(50));
  controller.Stop();

  REQUIRE_FALSE(controller.HasError());
  bool found_150 = false;
  bool found_before_150 = false;
  for (const auto& e : event_store.inserted) {
    if (e.tick == 150) found_150 = true;
    if (e.tick < 150) found_before_150 = true;
  }
  REQUIRE(found_150);
}

TEST_CASE("ReplayController error on missing trace", "[unit][replay]") {
  MockTraceStore trace_store;
  MockEventStore event_store;
  boat::core::EventBus event_bus;
  ReplayController controller(trace_store, event_store, event_bus);

  ReplayConfig config;
  config.trace_id = "nonexistent";

  REQUIRE_THROWS_AS(controller.Start(config), std::runtime_error);
}

TEST_CASE("ReplayController empty trace finishes immediately", "[unit][replay]") {
  MockTraceStore trace_store;
  MockEventStore event_store;
  boat::core::EventBus event_bus;
  ReplayController controller(trace_store, event_store, event_bus);

  trace_store.traces["empty"] = {};

  ReplayConfig config;
  config.trace_id = "empty";
  config.speed = ReplaySpeed::REAL_TIME;

  controller.Start(config);
  std::this_thread::sleep_for(std::chrono::milliseconds(50));
  controller.Stop();

  REQUIRE_FALSE(controller.HasError());
  REQUIRE(event_store.inserted.empty());
}

TEST_CASE("ReplayController Stop unmaps trace", "[unit][replay]") {
  MockTraceStore trace_store;
  MockEventStore event_store;
  boat::core::EventBus event_bus;
  ReplayController controller(trace_store, event_store, event_bus);

  auto trace_data = BuildSequentialTrace(100, 3);
  trace_store.traces["unmap_test"] = trace_data;

  ReplayConfig config;
  config.trace_id = "unmap_test";
  config.speed = ReplaySpeed::REAL_TIME;
  config.speed_multiplier = 1000.0;

  REQUIRE(trace_store.unmapped.empty());
  controller.Start(config);
  std::this_thread::sleep_for(std::chrono::milliseconds(50));
  controller.Stop();

  REQUIRE(trace_store.unmapped.size() == 1);
  REQUIRE(trace_store.unmapped[0] == "unmap_test");
}

TEST_CASE("ReplayController multiple Start calls stop previous replay", "[unit][replay]") {
  MockTraceStore trace_store;
  MockEventStore event_store;
  boat::core::EventBus event_bus;
  ReplayController controller(trace_store, event_store, event_bus);

  auto trace_data = BuildSequentialTrace(100, 3);
  trace_store.traces["first"] = trace_data;
  trace_store.traces["second"] = trace_data;

  ReplayConfig config1{.trace_id = "first", .speed = ReplaySpeed::REAL_TIME, .speed_multiplier = 1000.0};
  ReplayConfig config2{.trace_id = "second", .speed = ReplaySpeed::REAL_TIME, .speed_multiplier = 1000.0};

  controller.Start(config1);
  std::this_thread::sleep_for(std::chrono::milliseconds(30));
  controller.Start(config2);
  std::this_thread::sleep_for(std::chrono::milliseconds(50));
  controller.Stop();

  REQUIRE(trace_store.unmapped.size() == 2);
}

TEST_CASE("ReplayController StartFromEvents replays events from event store", "[unit][replay]") {
  MockTraceStore trace_store;
  MockEventStore event_store;
  boat::core::EventBus event_bus;
  ReplayController controller(trace_store, event_store, event_bus);

  std::vector<EventRecord> events = {
      {.id = "e1", .simulation_id = "sim1", .tick = 100, .wall_time_ns = 100000000, .signal_id = "sig1",
       .value_type = 1, .value_blob = {0x01, 0x02}, .tags = "{}"},
      {.id = "e2", .simulation_id = "sim1", .tick = 110, .wall_time_ns = 110000000, .signal_id = "sig1",
       .value_type = 1, .value_blob = {0x03, 0x04}, .tags = "{}"},
      {.id = "e3", .simulation_id = "sim1", .tick = 120, .wall_time_ns = 120000000, .signal_id = "sig1",
       .value_type = 1, .value_blob = {0x05, 0x06}, .tags = "{}"},
  };
  event_store.QueryResult = events;

  EventFilter filter;
  filter.simulation_id = "sim1";

  ReplayConfig cfg;
  cfg.speed = ReplaySpeed::ACCELERATED;
  cfg.speed_multiplier = 100.0;
  cfg.start_tick = 100;
  controller.StartFromEvents(filter, cfg);
  std::this_thread::sleep_for(std::chrono::milliseconds(50));
  controller.Stop();

  REQUIRE_FALSE(controller.HasError());
  REQUIRE(event_store.inserted.size() >= 3);
}

TEST_CASE("ReplayController StartFromEvents handles empty result", "[unit][replay]") {
  MockTraceStore trace_store;
  MockEventStore event_store;
  boat::core::EventBus event_bus;
  ReplayController controller(trace_store, event_store, event_bus);

  EventFilter filter;
  filter.simulation_id = "nonexistent";

  controller.StartFromEvents(filter);

  REQUIRE_FALSE(controller.HasError());
}

TEST_CASE("ReplayController defaults speed_multiplier to 1.0 when zero", "[unit][replay]") {
  MockTraceStore trace_store;
  MockEventStore event_store;
  boat::core::EventBus event_bus;
  ReplayController controller(trace_store, event_store, event_bus);

  auto trace_data = BuildSequentialTrace(100, 3);
  trace_store.traces["zero_mult"] = trace_data;

  ReplayConfig config;
  config.trace_id = "zero_mult";
  config.speed = ReplaySpeed::REAL_TIME;
  config.speed_multiplier = 0.0;
  config.start_tick = 100;

  controller.Start(config);
  std::this_thread::sleep_for(std::chrono::milliseconds(100));
  controller.Stop();

  REQUIRE_FALSE(controller.HasError());
  REQUIRE(event_store.inserted.size() == 3);
}
