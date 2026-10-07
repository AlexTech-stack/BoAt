// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include <array>
#include <atomic>
#include <cstdint>
#include <functional>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <thread>
#include <variant>
#include <vector>

namespace boat::core {

class FaultInjector;

struct SignalEvent {
  std::uint64_t signal_id;
  std::uint64_t tick;
  std::variant<double, std::int64_t, bool, std::vector<std::uint8_t>, std::string> value;
};

struct FilterPredicate {
  std::uint64_t signal_id;
  std::optional<std::uint64_t> tick_min;
  std::optional<std::uint64_t> tick_max;
  std::function<bool(const SignalEvent&)> comparator;
};

class SignalRouter {
 public:
  using SubscriptionHandle = std::uint64_t;
  using Callback = std::function<void(const SignalEvent&)>;

  SignalRouter();
  ~SignalRouter();

  SubscriptionHandle Subscribe(std::uint64_t signal_id, FilterPredicate filter, Callback callback);
  void Unsubscribe(SubscriptionHandle handle);
  void Publish(const SignalEvent& event);
  void SetFaultInjector(FaultInjector* injector);

 private:
  static constexpr std::size_t kRingBufferSize = 4096;

  struct SpscRingBuffer {
    std::array<SignalEvent, kRingBufferSize> buffer{};
    std::atomic<std::size_t> head{0};
    std::atomic<std::size_t> tail{0};

    bool Push(const SignalEvent& event);
    bool Pop(SignalEvent& out);
  };

  struct Subscription {
    SubscriptionHandle handle;
    std::uint64_t signal_id;
    FilterPredicate filter;
    Callback callback;
    SpscRingBuffer queue;
    std::atomic<std::uint64_t> overflow_count{0};
    std::atomic<bool> active{true};
  };

  bool Matches(const Subscription& subscription, const SignalEvent& event) const;
  void EnqueueEvent(const SignalEvent& event);
  void CleanupInactiveSubscriptions();
  void DispatchLoop();

  std::atomic<bool> running_{true};
  std::atomic<SubscriptionHandle> next_handle_{1};
  std::vector<std::shared_ptr<Subscription>> subscriptions_;
  mutable std::mutex subscriptions_mutex_;
  FaultInjector* fault_injector_{nullptr};

  /* Declared LAST, and started in the constructor *body* -- never in the
     member-init list. Members initialise in declaration order, so a thread
     started before this point runs DispatchLoop() against members that do not
     exist yet: it locks subscriptions_mutex_ and reads subscriptions_, both of
     which were still unconstructed. That is undefined behaviour, and TSan
     reported it as a race between DispatchLoop()'s lock and the mutex's own
     constructor. Keep this declaration last so the mistake is harder to make
     again. */
  std::thread dispatcher_thread_;
};

}  // namespace boat::core
