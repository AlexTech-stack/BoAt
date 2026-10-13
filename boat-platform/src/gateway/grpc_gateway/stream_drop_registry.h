// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <map>
#include <mutex>
#include <string>
#include <utility>
#include <vector>

namespace boat::gateway {

/* Default backlog for one streaming subscriber, in events. 4096 matches the
   SignalRouter ring: enough that a briefly stalled client (a GC pause, a slow
   disk write) rides it out, small enough that a permanently slow client cannot
   grow the gateway -- frames are at most a few hundred bytes, so the worst
   case per subscriber is single-digit MB. BOAT_STREAM_QUEUE_CAPACITY
   overrides it; see EffectiveConfig. */
inline constexpr std::size_t kDefaultStreamQueueCapacity = 4096;

/* Upper bound accepted from BOAT_STREAM_QUEUE_CAPACITY. A typo'd extra digit
   must not turn a "bound the memory" feature into the opposite. */
inline constexpr std::size_t kMaxStreamQueueCapacity = 1'000'000;

/* Metric names for per-stream overflow, and the store behind them.
 *
 * Every streaming RPC hands each subscriber a bounded queue; when a client
 * cannot keep up, the queue drops its oldest entries rather than growing
 * without limit (issue #21 -- a fast bus could otherwise push a slow client's
 * backlog until the gateway was OOM-killed). The count of what was dropped is
 * the only signal that a consumer's view has gaps, so it must survive the
 * connection: a client that never learns it lost frames will trust an
 * incomplete trace.
 *
 * Names follow the `can.<iface>.<field>` convention and are emitted by
 * MetricsService (GetMetrics snapshot, StreamMetrics once a second). They are
 * cumulative totals since gateway start, across all connections of that
 * stream -- per-connection identity is not worth the cardinality here. */
namespace stream_metrics {
inline constexpr char kFramesSubscribe[]  = "grpc.frames.subscribe.dropped";
inline constexpr char kFramesStream[]     = "grpc.frames.stream.dropped";
inline constexpr char kPdusSubscribe[]    = "grpc.pdus.subscribe.dropped";
inline constexpr char kCanTpSubscribe[]   = "grpc.can_tp.subscribe.dropped";
inline constexpr char kCanTpErrors[]      = "grpc.can_tp.subscribe_errors.dropped";
inline constexpr char kBusSubscribe[]     = "grpc.bus.subscribe.dropped";
inline constexpr char kSignalsSubscribe[] = "grpc.signals.subscribe.dropped";
inline constexpr char kDebugEvents[]      = "grpc.debug.stream_events.dropped";
inline constexpr char kMetricsStream[]    = "grpc.metrics.stream.dropped";

/* Replay drops live in ReplayController (its queue is core code and has no
   registry), so it is reported separately; the names below are the ones backed
   by StreamDropRegistry and are what MetricsService enumerates. */
inline constexpr char kReplayStream[] = "grpc.replay.stream.dropped";

/* Not a gRPC stream and not registry-backed: SignalRouter's own per-
   subscription ring drops the newest event when full, upstream of any handler
   queue, and reports one aggregate count. */
inline constexpr char kSignalRouterDropped[] = "signal_router.dropped";

inline constexpr std::array<const char*, 9> kRegistryBacked = {
    kFramesSubscribe, kFramesStream,     kPdusSubscribe,    kCanTpSubscribe,
    kCanTpErrors,     kBusSubscribe,     kSignalsSubscribe, kDebugEvents,
    kMetricsStream,
};
}  // namespace stream_metrics

class StreamDropRegistry {
 public:
  void Add(const char* stream, std::uint64_t count) {
    if (count == 0) return;
    std::lock_guard<std::mutex> lock(mutex_);
    totals_[stream] += count;
  }

  // Cumulative count for one stream name; 0 when it has never dropped.
  [[nodiscard]] std::uint64_t Count(const char* stream) const {
    std::lock_guard<std::mutex> lock(mutex_);
    const auto it = totals_.find(stream);
    return it == totals_.end() ? 0 : it->second;
  }

  // Non-zero streams only, as (name, count) pairs.
  [[nodiscard]] std::vector<std::pair<std::string, std::uint64_t>> Snapshot() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return {totals_.begin(), totals_.end()};
  }

 private:
  mutable std::mutex mutex_;
  std::map<std::string, std::uint64_t> totals_;
};

}  // namespace boat::gateway
