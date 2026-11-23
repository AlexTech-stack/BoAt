// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#include "event_store/async_event_store.h"

#include <algorithm>
#include <cstdio>
#include <stdexcept>
#include <utility>

namespace boat::store {

AsyncEventStore::AsyncEventStore(std::unique_ptr<IEventStore> inner)
    : AsyncEventStore(std::move(inner), Options{}) {}

AsyncEventStore::AsyncEventStore(std::unique_ptr<IEventStore> inner,
                                 Options options)
    : inner_(std::move(inner)), options_(options) {
  if (!inner_) {
    throw std::invalid_argument("AsyncEventStore requires an inner store");
  }
  options_.queue_capacity = std::max<std::size_t>(1, options_.queue_capacity);
  options_.batch_size = std::max<std::size_t>(1, options_.batch_size);
  if (options_.flush_interval.count() < 0) {
    options_.flush_interval = std::chrono::milliseconds(0);
  }

  writer_ = std::thread([this] { WriterLoop(); });
}

AsyncEventStore::~AsyncEventStore() {
  {
    std::lock_guard<std::mutex> lock(mutex_);
    stopping_ = true;
  }
  cv_.notify_all();
  if (writer_.joinable()) {
    writer_.join();
  }
}

void AsyncEventStore::InsertBatch(std::span<const EventRecord> events) {
  if (events.empty()) return;

  {
    std::lock_guard<std::mutex> lock(mutex_);
    for (const auto& event : events) {
      if (queue_.size() >= options_.queue_capacity) {
        queue_.pop_front();
        queue_seq_.pop_front();
        const auto total =
            dropped_.fetch_add(1, std::memory_order_relaxed) + 1;
        if (total == 1 || total % 1000 == 0) {
          std::fprintf(stderr,
                       "[EventStore] queue full: dropped %llu record(s) total\n",
                       static_cast<unsigned long long>(total));
        }
      }
      queue_.push_back(event);
      queue_seq_.push_back(++next_seq_);
    }
  }
  cv_.notify_one();
  // A drop can retire the last record a Flush() was waiting for.
  idle_cv_.notify_all();
}

std::vector<EventRecord> AsyncEventStore::Query(const EventFilter& filter) {
  Flush();
  return inner_->Query(filter);
}

bool AsyncEventStore::RetiredThrough(std::uint64_t target) const {
  // The in-flight batch is older than anything still queued.
  if (in_flight_first_ != 0 && in_flight_first_ <= target) return false;
  return queue_seq_.empty() || queue_seq_.front() > target;
}

void AsyncEventStore::Flush() {
  std::unique_lock<std::mutex> lock(mutex_);
  const std::uint64_t target = next_seq_;
  if (RetiredThrough(target)) return;

  flush_target_ = std::max(flush_target_, target);
  cv_.notify_all();
  idle_cv_.wait(lock, [this, target] { return RetiredThrough(target); });
}

std::uint64_t AsyncEventStore::DroppedEventCount() const {
  return dropped_.load(std::memory_order_relaxed);
}

std::uint64_t AsyncEventStore::WriteErrorCount() const {
  return write_errors_.load(std::memory_order_relaxed);
}

void AsyncEventStore::WriterLoop() {
  for (;;) {
    std::vector<EventRecord> batch;
    {
      std::unique_lock<std::mutex> lock(mutex_);
      cv_.wait(lock, [this] { return stopping_ || !queue_.empty(); });

      if (queue_.empty()) {
        if (stopping_) break;
        continue;
      }

      // A waiting Flush() wants the front of the queue now; don't sit out
      // flush_interval collecting a fuller batch.
      const auto flush_pending = [this] {
        return !queue_seq_.empty() && queue_seq_.front() <= flush_target_;
      };
      if (!flush_pending() && !stopping_ &&
          queue_.size() < options_.batch_size) {
        cv_.wait_for(lock, options_.flush_interval, [&] {
          return stopping_ || flush_pending() ||
                 queue_.size() >= options_.batch_size;
        });
        // Only this thread shrinks the queue (a drop is always followed by a
        // push), so it is still non-empty after the wait.
      }

      const std::size_t count =
          std::min(queue_.size(), options_.batch_size);
      batch.reserve(count);
      in_flight_first_ = queue_seq_.front();
      for (std::size_t i = 0; i < count; ++i) {
        batch.push_back(std::move(queue_.front()));
        queue_.pop_front();
        queue_seq_.pop_front();
      }
    }

    try {
      WriteBatch(std::span<const EventRecord>(batch.data(), batch.size()));
    } catch (const std::exception& ex) {
      const auto total =
          write_errors_.fetch_add(1, std::memory_order_relaxed) + 1;
      if (total == 1 || total % 1000 == 0) {
        std::fprintf(stderr,
                     "[EventStore] write failed (%llu batch(es) total): %s\n",
                     static_cast<unsigned long long>(total), ex.what());
      }
    } catch (...) {
      const auto total =
          write_errors_.fetch_add(1, std::memory_order_relaxed) + 1;
      std::fprintf(stderr,
                   "[EventStore] write failed (%llu batch(es) total): "
                   "unknown error\n",
                   static_cast<unsigned long long>(total));
    }

    {
      std::lock_guard<std::mutex> lock(mutex_);
      in_flight_first_ = 0;
    }
    idle_cv_.notify_all();
  }
}

void AsyncEventStore::WriteBatch(std::span<const EventRecord> batch) {
  inner_->InsertBatch(batch);
}

}  // namespace boat::store
