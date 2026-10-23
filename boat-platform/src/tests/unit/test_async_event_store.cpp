// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#include <catch2/catch_test_macros.hpp>

#include <array>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <filesystem>
#include <memory>
#include <mutex>
#include <span>
#include <stdexcept>
#include <thread>
#include <vector>

#include "event_store/async_event_store.h"
#include "event_store/event_store.h"

namespace {

using namespace std::chrono_literals;

struct RecordingState {
  std::mutex mutex;
  std::vector<boat::store::EventRecord> inserted;
};

struct RecordingStore : boat::store::IEventStore {
  explicit RecordingStore(std::shared_ptr<RecordingState> state)
      : state_(std::move(state)) {}

  void InsertBatch(std::span<const boat::store::EventRecord> events) override {
    std::lock_guard<std::mutex> lock(state_->mutex);
    for (const auto& event : events) state_->inserted.push_back(event);
  }

  std::vector<boat::store::EventRecord> Query(
      const boat::store::EventFilter&) override {
    std::lock_guard<std::mutex> lock(state_->mutex);
    return state_->inserted;
  }

  std::shared_ptr<RecordingState> state_;
};

struct BlockingStore : boat::store::IEventStore {
  std::mutex mutex;
  std::condition_variable cv;
  std::vector<boat::store::EventRecord> inserted;
  bool entered{false};
  bool release{false};

  void InsertBatch(std::span<const boat::store::EventRecord> events) override {
    std::unique_lock<std::mutex> lock(mutex);
    entered = true;
    cv.notify_all();
    cv.wait(lock, [this] { return release; });
    for (const auto& event : events) inserted.push_back(event);
  }

  std::vector<boat::store::EventRecord> Query(
      const boat::store::EventFilter&) override {
    std::lock_guard<std::mutex> lock(mutex);
    return inserted;
  }
};

struct FlakyStore : boat::store::IEventStore {
  std::atomic<int> calls{0};
  std::mutex mutex;
  std::vector<boat::store::EventRecord> inserted;

  void InsertBatch(std::span<const boat::store::EventRecord> events) override {
    if (calls.fetch_add(1) == 0) {
      throw std::runtime_error("simulated disk full");
    }
    std::lock_guard<std::mutex> lock(mutex);
    for (const auto& event : events) inserted.push_back(event);
  }

  std::vector<boat::store::EventRecord> Query(
      const boat::store::EventFilter&) override {
    std::lock_guard<std::mutex> lock(mutex);
    return inserted;
  }
};

boat::store::EventRecord MakeRecord(std::string id) {
  boat::store::EventRecord record;
  record.id = std::move(id);
  record.simulation_id = "sim";
  record.tick = 1;
  record.wall_time_ns = 1;
  record.signal_id = "sig";
  record.value_type = 0;
  record.value_blob = {0x01};
  record.tags = "{}";
  return record;
}

template <typename Predicate>
bool WaitFor(Predicate predicate, std::chrono::milliseconds timeout = 2s) {
  const auto deadline = std::chrono::steady_clock::now() + timeout;
  while (std::chrono::steady_clock::now() < deadline) {
    if (predicate()) return true;
    std::this_thread::sleep_for(1ms);
  }
  return predicate();
}

}  // namespace

TEST_CASE("AsyncEventStore flushes FIFO order and queries read through",
          "[unit][event_store][async]") {
  auto state = std::make_shared<RecordingState>();
  auto inner = std::make_unique<RecordingStore>(state);
  boat::store::AsyncEventStore::Options options;
  options.queue_capacity = 64;
  options.batch_size = 3;
  options.flush_interval = 1ms;

  {
    boat::store::AsyncEventStore store(std::move(inner), options);
    std::vector<boat::store::EventRecord> events;
    for (int i = 0; i < 10; ++i) {
      events.push_back(MakeRecord("e" + std::to_string(i)));
    }
    store.InsertBatch(std::span<const boat::store::EventRecord>(
        events.data(), events.size()));

    // Query() flushes first, so this also verifies read-your-writes without
    // requiring callers to know the queue exists.
    const auto result = store.Query({});
    REQUIRE(result.size() == 10);
    for (int i = 0; i < 10; ++i) {
      REQUIRE(result[static_cast<std::size_t>(i)].id ==
              "e" + std::to_string(i));
    }
    store.Flush();
    REQUIRE(store.DroppedEventCount() == 0);
    REQUIRE(store.WriteErrorCount() == 0);
  }

  REQUIRE(state->inserted.size() == 10);
}

TEST_CASE("AsyncEventStore drops the oldest record when the queue is full",
          "[unit][event_store][async]") {
  auto inner = std::make_unique<BlockingStore>();
  auto* blocking = inner.get();
  boat::store::AsyncEventStore::Options options;
  options.queue_capacity = 2;
  options.batch_size = 1;
  options.flush_interval = 1ms;

  boat::store::AsyncEventStore store(std::move(inner), options);

  // The first record is taken by the writer and blocks inside InsertBatch,
  // leaving the two-record queue available for the next inserts.
  const std::array<boat::store::EventRecord, 1> first{MakeRecord("e1")};
  store.InsertBatch(
      std::span<const boat::store::EventRecord>(first.data(), first.size()));
  REQUIRE(WaitFor([&] {
    std::lock_guard<std::mutex> lock(blocking->mutex);
    return blocking->entered;
  }));

  for (const char* id : {"e2", "e3", "e4"}) {
    const std::array<boat::store::EventRecord, 1> event{MakeRecord(id)};
    store.InsertBatch(std::span<const boat::store::EventRecord>(
        event.data(), event.size()));
  }

  REQUIRE(store.DroppedEventCount() == 1);
  {
    std::lock_guard<std::mutex> lock(blocking->mutex);
    blocking->release = true;
  }
  blocking->cv.notify_all();
  store.Flush();

  REQUIRE(blocking->inserted.size() == 3);
  REQUIRE(blocking->inserted[0].id == "e1");
  REQUIRE(blocking->inserted[1].id == "e3");
  REQUIRE(blocking->inserted[2].id == "e4");
}

TEST_CASE("AsyncEventStore counts write failures and keeps draining",
          "[unit][event_store][async]") {
  auto inner = std::make_unique<FlakyStore>();
  auto* flaky = inner.get();
  boat::store::AsyncEventStore::Options options;
  options.queue_capacity = 16;
  options.batch_size = 1;
  options.flush_interval = 1ms;
  boat::store::AsyncEventStore store(std::move(inner), options);

  const std::array<boat::store::EventRecord, 1> first{MakeRecord("e1")};
  store.InsertBatch(
      std::span<const boat::store::EventRecord>(first.data(), first.size()));
  store.Flush();
  REQUIRE(store.WriteErrorCount() == 1);
  REQUIRE(flaky->inserted.empty());

  const std::array<boat::store::EventRecord, 1> second{MakeRecord("e2")};
  store.InsertBatch(
      std::span<const boat::store::EventRecord>(second.data(), second.size()));
  store.Flush();
  REQUIRE(store.WriteErrorCount() == 1);
  REQUIRE(flaky->inserted.size() == 1);
  REQUIRE(flaky->inserted[0].id == "e2");
}

TEST_CASE("AsyncEventStore destructor drains accepted records",
          "[unit][event_store][async]") {
  auto state = std::make_shared<RecordingState>();
  auto inner = std::make_unique<RecordingStore>(state);
  boat::store::AsyncEventStore::Options options;
  options.queue_capacity = 16;
  options.batch_size = 4;
  options.flush_interval = 10ms;

  {
    boat::store::AsyncEventStore store(std::move(inner), options);
    const std::array<boat::store::EventRecord, 2> events{
        MakeRecord("e1"), MakeRecord("e2")};
    store.InsertBatch(
        std::span<const boat::store::EventRecord>(events.data(), events.size()));
  }

  REQUIRE(state->inserted.size() == 2);
}

TEST_CASE("AsyncEventStore drains SQLite on destruction",
          "[unit][event_store][async]") {
  const auto suffix = std::chrono::steady_clock::now().time_since_epoch().count();
  const auto db_path = std::filesystem::temp_directory_path() /
                       ("boat_async_event_store_" + std::to_string(suffix) + ".db");
  std::filesystem::remove(db_path);

  {
    boat::store::AsyncEventStore::Options options;
    options.queue_capacity = 16;
    options.batch_size = 4;
    options.flush_interval = 10ms;
    boat::store::AsyncEventStore store(
        std::make_unique<boat::store::SqliteEventStore>(db_path.string()),
        options);
    const std::array<boat::store::EventRecord, 2> events{
        MakeRecord("e1"), MakeRecord("e2")};
    store.InsertBatch(
        std::span<const boat::store::EventRecord>(events.data(), events.size()));
  }

  boat::store::SqliteEventStore reopened(db_path.string());
  const auto rows = reopened.Query({});
  REQUIRE(rows.size() == 2);

  std::filesystem::remove(db_path);
  std::filesystem::remove(db_path.string() + "-wal");
  std::filesystem::remove(db_path.string() + "-shm");
}
