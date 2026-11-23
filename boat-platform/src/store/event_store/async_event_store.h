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

  /* Wait until every record accepted *before this call* has been handed to
   * the inner store (or dropped as oldest by the queue-full policy). Records
   * inserted while Flush() waits are not waited for: a producer that keeps
   * the queue non-empty must not be able to block a reader forever. Safe to
   * call from a non-writer thread. */
  void Flush() override;

  [[nodiscard]] std::uint64_t DroppedEventCount() const override;
  [[nodiscard]] std::uint64_t WriteErrorCount() const override;

 private:
  void WriterLoop();
  void WriteBatch(std::span<const EventRecord> batch);
  /* True once every record with sequence number <= target has left the
   * queue and is no longer in a batch being written. Requires mutex_. */
  [[nodiscard]] bool RetiredThrough(std::uint64_t target) const;

  std::unique_ptr<IEventStore> inner_;
  Options options_;
  std::thread writer_;
  std::mutex mutex_;
  std::condition_variable cv_;
  std::condition_variable idle_cv_;
  std::deque<EventRecord> queue_;
  /* Sequence number of each queued record, parallel to queue_. Records are
   * numbered 1, 2, ... in acceptance order and leave the queue in that order
   * (writer and drop policy both pop the front), so "everything up to N is
   * done" is a comparison against the front, not a scan. */
  std::deque<std::uint64_t> queue_seq_;
  std::uint64_t next_seq_{0};       // last sequence number assigned
  std::uint64_t in_flight_first_{0}; // first seq of the batch being written; 0 = none
  std::uint64_t flush_target_{0};   // highest seq any Flush() is waiting for
  bool stopping_{false};
  std::atomic<std::uint64_t> dropped_{0};
  std::atomic<std::uint64_t> write_errors_{0};
};

}  // namespace boat::store
