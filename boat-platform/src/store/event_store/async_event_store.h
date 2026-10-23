// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <memory>
#include <mutex>
#include <span>
#include <thread>
#include <vector>

#include "event_store/event_store.h"

namespace boat::store {

/* Bounded, ordered writer that sits in front of an IEventStore.
 *
 * Every production event-store write used to happen on the caller's thread.
 * Replay happens to run on the tick thread, so a slow SQLite commit delayed
 * every later phase of that tick. This wrapper moves writes to one worker
 * thread and batches them into one InsertBatch call per batch.
 *
 * Queue-full policy: drop the oldest queued record, matching the gRPC stream
 * queues and EventBus. Blocking would put the tick thread back on SQLite, the
 * exact problem this class exists to avoid. Dropping is counted and logged;
 * a consumer that needs a lossless recording should use a trace, not the
 * event store.
 *
 * Lifetime: the wrapper owns the inner store. Flush() (also run by the
 * destructor) waits until every accepted record has been handed to the inner
 * store, so a clean shutdown does not silently truncate the queue.
 */
class AsyncEventStore : public IEventStore {
 public:
  struct Options {
    std::size_t queue_capacity = 16384;
    std::size_t batch_size = 256;
    std::chrono::milliseconds flush_interval{10};
  };

  explicit AsyncEventStore(std::unique_ptr<IEventStore> inner);
  AsyncEventStore(std::unique_ptr<IEventStore> inner, Options options);
  ~AsyncEventStore() override;

  AsyncEventStore(const AsyncEventStore&) = delete;
  AsyncEventStore& operator=(const AsyncEventStore&) = delete;

  void InsertBatch(std::span<const EventRecord> events) override;
  std::vector<EventRecord> Query(const EventFilter& filter) override;

  /* Drain the queue. Safe to call from a non-writer thread; the worker stops
   * taking new work only for as long as it takes to empty the queue. */
  void Flush() override;

  [[nodiscard]] std::uint64_t DroppedEventCount() const override;
  [[nodiscard]] std::uint64_t WriteErrorCount() const override;

 private:
  void WriterLoop();
  void WriteBatch(std::span<const EventRecord> batch);

  std::unique_ptr<IEventStore> inner_;
  Options options_;
  std::thread writer_;
  std::mutex mutex_;
  std::condition_variable cv_;
  std::condition_variable idle_cv_;
  std::deque<EventRecord> queue_;
  bool stopping_{false};
  bool writing_{false};
  bool flush_requested_{false};
  std::atomic<std::uint64_t> dropped_{0};
  std::atomic<std::uint64_t> write_errors_{0};
};

}  // namespace boat::store
