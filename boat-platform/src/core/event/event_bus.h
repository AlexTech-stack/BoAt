// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include <cstdint>
#include <functional>
#include <mutex>
#include <queue>
#include <string>
#include <unordered_map>
#include <variant>
#include <vector>

namespace boat::core {

/* Payload for types that cross layer boundaries (CAN/ETH frames, protobuf, …).
   type_tag discriminates the original type; data holds the serialized bytes. */
struct UnknownPayload {
  std::uint32_t type_tag;
  std::vector<std::uint8_t> data;
};

using BusEventPayload = std::variant<
    std::monostate,
    std::int64_t,
    double,
    std::string,
    UnknownPayload
>;

struct BusEvent {
  std::uint32_t type;
  BusEventPayload payload;
  std::uint64_t tick;
};

class EventBus {
 public:
  using HandlerFn = std::function<void(const BusEvent&)>;
  using SubscriptionHandle = std::uint64_t;

  // Matches the gRPC stream queues: enough headroom for a stalled tick,
  // small enough that a permanently undrained type cannot grow the gateway.
  static constexpr std::size_t kDefaultMaxQueued = 4096;

  /* Enqueues only when a handler is subscribed for the event type: an event
     with no subscriber can never be delivered, so it is dropped at publish
     time and never allocates or enters the queue. The queue is bounded; when
     full, the oldest queued event is dropped and DroppedEventCount() grows. */
  void Publish(BusEvent event);
  SubscriptionHandle Subscribe(std::uint32_t type, HandlerFn handler);
  void Unsubscribe(SubscriptionHandle handle);
  void Dispatch();
  [[nodiscard]] bool HasSubscribers(std::uint32_t type) const;
  [[nodiscard]] std::uint64_t DroppedEventCount() const;
  void SetMaxQueued(std::size_t max);

  /* Drop everything queued but not yet dispatched. Subscriptions stay:
     this is the reset half of "a fresh run must not see events published by
     the previous one", not a teardown. The drop counter is cumulative. */
  void Clear();

 private:
  struct HandlerEntry {
    SubscriptionHandle handle;
    HandlerFn handler;
  };

  mutable std::mutex mutex_;
  std::queue<BusEvent> queue_;
  std::size_t max_queued_{kDefaultMaxQueued};
  std::uint64_t dropped_{0};
  std::unordered_map<std::uint32_t, std::vector<HandlerEntry>> subscribers_;
  std::unordered_map<SubscriptionHandle, std::uint32_t> handle_to_type_;
  SubscriptionHandle next_handle_{1};
};

}  // namespace boat::core
