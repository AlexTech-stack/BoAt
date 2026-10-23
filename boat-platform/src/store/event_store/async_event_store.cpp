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
        const auto total =
            dropped_.fetch_add(1, std::memory_order_relaxed) + 1;
        if (total == 1 || total % 1000 == 0) {
          std::fprintf(stderr,
                       "[EventStore] queue full: dropped %llu record(s) total\n",
                       static_cast<unsigned long long>(total));
        }
      }
      queue_.push_back(event);
    }
  }
  cv_.notify_one();
}

std::vector<EventRecord> AsyncEventStore::Query(const EventFilter& filter) {
  Flush();
  return inner_->Query(filter);
}

void AsyncEventStore::Flush() {
  std::unique_lock<std::mutex> lock(mutex_);
  if (queue_.empty() && !writing_) return;

  flush_requested_ = true;
  cv_.notify_all();
  idle_cv_.wait(lock, [this] { return queue_.empty() && !writing_; });
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
      cv_.wait(lock, [this] {
        return stopping_ || !queue_.empty() || flush_requested_;
      });

      if (queue_.empty()) {
        if (stopping_) break;
        flush_requested_ = false;
        idle_cv_.notify_all();
        continue;
      }

      if (!flush_requested_ && !stopping_ &&
          queue_.size() < options_.batch_size) {
        cv_.wait_for(lock, options_.flush_interval, [this] {
          return stopping_ || flush_requested_ ||
                 queue_.size() >= options_.batch_size;
        });
      }

      const std::size_t count =
          std::min(queue_.size(), options_.batch_size);
      batch.reserve(count);
      for (std::size_t i = 0; i < count; ++i) {
        batch.push_back(std::move(queue_.front()));
        queue_.pop_front();
      }
      writing_ = true;
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
      writing_ = false;
      if (queue_.empty()) {
        flush_requested_ = false;
        idle_cv_.notify_all();
      } else {
        cv_.notify_one();
      }
    }
  }
}

void AsyncEventStore::WriteBatch(std::span<const EventRecord> batch) {
  inner_->InsertBatch(batch);
}

}  // namespace boat::store
