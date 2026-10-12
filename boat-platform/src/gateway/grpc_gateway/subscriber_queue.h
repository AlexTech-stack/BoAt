// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include <condition_variable>
#include <mutex>
#include <vector>

namespace boat::gateway {

/* State shared between a streaming RPC handler and the producer callbacks
   feeding it.

   Heap-owned, and captured into the callback *by value* as a std::shared_ptr.

   Stack locals captured by reference are a use-after-free: producers snapshot
   their callbacks under their own lock and invoke them after releasing it
   (CanBusRegistry::DispatchRx, PduRouter::DispatchPdu, CanTpPlugin::
   NotifySubscribers, EventBus dispatch), and Unsubscribe does not wait for an
   in-flight callback. A callback taken from a snapshot made before the
   unsubscribe can therefore still be running after the RPC handler returns and
   tears down the frame it was writing into. TSan reported the first instance
   as "double lock of a mutex" whose "location is stack of thread T10", which
   is what a reused dead stack frame looks like once the mutex's own state is
   garbage.

   Owning the state here means a late callback writes into a live object, and
   the last reference to drop frees it -- whether that is the handler or the
   straggling callback. Handlers must write to the gRPC stream only from their
   own thread, never from the producer callback. */
template <typename Event>
struct SubscriberQueue {
  std::mutex              mutex;
  std::condition_variable cv;
  std::vector<Event>      queue;
  bool                    closed = false;  // guarded by mutex; set on teardown
};

}  // namespace boat::gateway
