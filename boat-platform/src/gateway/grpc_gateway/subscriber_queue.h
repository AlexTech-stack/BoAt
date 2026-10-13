// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include <atomic>
#include <condition_variable>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <deque>
#include <functional>
#include <memory>
#include <mutex>
#include <string>
#include <utility>

#include "stream_drop_registry.h"

namespace boat::gateway {

/* State shared between a streaming RPC handler and the producer callbacks
   feeding it.

   Heap-owned, and captured into the callback *by value* as a std::shared_ptr.

   Stack locals captured by reference are a use-after-free: producers snapshot
   their callbacks under their own lock and invoke them after releasing it
   (CanBusRegistry::DispatchRx, PduRouter::DispatchPdu, CanTpPlugin::
   NotifySubscribers, EventBus dispatch, SignalBus::Publish, RpcAuditLog::Push),
   and Unsubscribe does not wait for an in-flight callback. A callback taken
   from a snapshot made before the unsubscribe can therefore still be running
   after the RPC handler returns and tears down the frame it was writing into.
   TSan reported the first instance as "double lock of a mutex" whose "location
   is stack of thread T10", which is what a reused dead stack frame looks like
   once the mutex's own state is garbage.

   Owning the state here means a late callback writes into a live object, and
   the last reference to drop frees it -- whether that is the handler or the
   straggling callback. Handlers must write to the gRPC stream only from their
   own thread, never from the producer callback.

   The queue is bounded (issue #21): a producer that outruns its client drops
   from the front, keeping the freshest events and keeping memory flat. The
   alternatives are worse -- an unbounded queue is an OOM in waiting on a busy
   bus, and blocking the producer would stall the bus-dispatch thread for every
   subscriber, including hardware RX. Every dropped event is counted and
   reported through `drops` (stream_drop_registry.h) plus a rate-limited line on
   stderr; a consumer whose view has gaps must be able to find out.

   `conflate_key` opts a stream into latest-value-wins instead of plain
   drop-oldest: when full, a new event whose key already has an older entry
   replaces that entry in place. Metrics want this -- a slow metrics consumer
   should see the current value of every series, not a tail of stale snapshots
   missing half the names. Events a conflating stream drops are counted the
   same way, since the effect on the consumer is the same: it did not see every
   event. */
template <typename Event>
struct SubscriberQueue {
  using ConflateKey = std::function<std::string(const Event&)>;

  SubscriberQueue(StreamDropRegistry* registry, const char* stream,
                  std::size_t capacity, ConflateKey conflate_key = {})
      : drops(registry),
        stream_name(stream),
        capacity(capacity),
        conflate_key(std::move(conflate_key)) {}

  std::mutex              mutex;
  std::condition_variable cv;
  std::deque<Event>       queue;
  bool                    closed = false;  // guarded by mutex; set on teardown
  StreamDropRegistry*     drops = nullptr;
  const char*             stream_name = "";
  std::size_t             capacity = kDefaultStreamQueueCapacity;
  ConflateKey             conflate_key;

  /* Call with `mutex` held. Appends the event, dropping from the front first
     when the queue is at capacity (or replacing a same-key entry for a
     conflating stream). Returns how many events this push dropped, 0 when
     none. */
  std::size_t Push(Event event) {
    if (capacity > 0 && queue.size() >= capacity) {
      if (conflate_key) {
        const std::string key = conflate_key(event);
        for (auto it = queue.rbegin(); it != queue.rend(); ++it) {
          if (conflate_key(*it) == key) {
            *it = std::move(event);
            dropped_total_.fetch_add(1, std::memory_order_relaxed);
            return 1;
          }
        }
      }
      queue.pop_front();
      queue.push_back(std::move(event));
      dropped_total_.fetch_add(1, std::memory_order_relaxed);
      return 1;
    }
    queue.push_back(std::move(event));
    return 0;
  }

  /* Call after releasing `mutex` when Push returned non-zero. Records the drop
     in the shared registry and logs it -- first drop, then once per 1000 --
     so a stalled client leaves a diagnostic trail rather than only a growing
     metric. */
  void ReportDrop(std::size_t dropped) {
    if (dropped == 0) return;
    if (drops != nullptr) drops->Add(stream_name, dropped);
    const std::uint64_t total =
        dropped_total_.fetch_add(0, std::memory_order_relaxed);
    const std::uint64_t before = total - dropped;
    if (before == 0 || before / 1000 != total / 1000) {
      std::fprintf(stderr,
                   "[Gateway] stream '%s': dropped %llu event(s) total -- "
                   "client cannot keep up\n",
                   stream_name, static_cast<unsigned long long>(total));
    }
  }

 private:
  std::atomic<std::uint64_t> dropped_total_{0};
};

template <typename Event>
std::shared_ptr<SubscriberQueue<Event>> MakeSubscriberQueue(
    StreamDropRegistry* registry, const char* stream, std::size_t capacity,
    typename SubscriberQueue<Event>::ConflateKey conflate_key = {}) {
  return std::make_shared<SubscriberQueue<Event>>(
      registry, stream, capacity, std::move(conflate_key));
}

}  // namespace boat::gateway
