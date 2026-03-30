#pragma once

#include <any>
#include <cstdint>
#include <functional>
#include <mutex>
#include <queue>
#include <unordered_map>
#include <vector>

namespace boat::core {

struct BusEvent {
  std::uint32_t type;
  std::any payload;
  std::uint64_t tick;
};

class EventBus {
 public:
  using HandlerFn = std::function<void(const BusEvent&)>;
  using SubscriptionHandle = std::uint64_t;

  void Publish(BusEvent event);
  SubscriptionHandle Subscribe(std::uint32_t type, HandlerFn handler);
  void Unsubscribe(SubscriptionHandle handle);
  void Dispatch();

 private:
  struct HandlerEntry {
    SubscriptionHandle handle;
    HandlerFn handler;
  };

  std::mutex mutex_;
  std::queue<BusEvent> queue_;
  std::unordered_map<std::uint32_t, std::vector<HandlerEntry>> subscribers_;
  SubscriptionHandle next_handle_{1};
};

}  // namespace boat::core
