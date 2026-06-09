#include "signal/signal_bus.h"

#include <algorithm>
#include <utility>

namespace boat::core {

BusSubscriberId SignalBus::Subscribe(std::vector<std::string> names,
                                     BusSubscribeFn fn) {
  std::lock_guard<std::mutex> lock(mutex_);
  const BusSubscriberId id = next_id_++;
  subscriptions_[id] = Subscription{std::move(names), std::move(fn)};
  return id;
}

void SignalBus::Unsubscribe(BusSubscriberId id) {
  std::lock_guard<std::mutex> lock(mutex_);
  subscriptions_.erase(id);
}

void SignalBus::Publish(const std::string& name,
                        const BusSignalValue& value) {
  // Snapshot matching callbacks under lock.
  std::vector<BusSubscribeFn> to_call;
  {
    std::lock_guard<std::mutex> lock(mutex_);
    for (const auto& [id, sub] : subscriptions_) {
      (void)id;
      if (sub.names.empty()) {
        to_call.push_back(sub.fn);
        continue;
      }
      for (const auto& n : sub.names) {
        if (n == name) {
          to_call.push_back(sub.fn);
          break;
        }
      }
    }
  }
  // Invoke callbacks without holding the lock.
  BusSignal signal{name, value};
  for (auto& fn : to_call) {
    fn(signal);
  }
}

void SignalBus::Publish(const std::string& name, double value) {
  Publish(name, BusSignalValue{value});
}

}  // namespace boat::core
