// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#include "metrics_service_impl.h"

#include <chrono>
#include <cstring>
#include <deque>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "can_bus_registry.h"
#include "core/pdu_router_interface.h"
#include "core/plugin/plugin_manager.h"
#include "subscriber_queue.h"

namespace boat::gateway {
namespace {
constexpr std::uint32_t kMetricEventType = 9101;

/* -1 is what a hop with no bittiming reports; keep an explicit sentinel so a
   consumer can tell "not reported" from CAN_STATE_ERROR_ACTIVE (0). */
constexpr double kUnknownBusState = -1.0;

double DecodeNumeric(const std::vector<std::uint8_t>& blob) {
  if (blob.size() >= sizeof(double)) {
    double value = 0.0;
    std::memcpy(&value, blob.data(), sizeof(double));
    return value;
  }
  return 0.0;
}

/* CAN bus health as MetricPoints: `can.<iface>.<field>`. Before this, a bus
   that had gone bus-off and a bus that was merely idle looked identical from
   the outside (issue #22), and RX overflow dropped frames silently (issue #20).
   tick is the current simulation tick; these counters are gateway-scoped, not
   simulation-scoped. */
template <typename Emit>
void AppendCanHealthMetrics(GatewayContext& ctx, std::uint64_t tick, Emit emit) {
  for (const auto& iface : ctx.can_bus_registry.Interfaces()) {
    const boat::hil::CanBusHealth health = ctx.can_bus_registry.GetInterfaceHealth(iface);
    const std::string prefix = "can." + iface + ".";
    emit(prefix + "rx_dropped", static_cast<double>(health.rx_dropped));
    emit(prefix + "device_rx_dropped", static_cast<double>(health.device_rx_dropped));
    emit(prefix + "device_rx_errors", static_cast<double>(health.device_rx_errors));
    emit(prefix + "error_frames", static_cast<double>(health.error_frames));
    emit(prefix + "bus_off_events", static_cast<double>(health.bus_off_events));
    emit(prefix + "tec", static_cast<double>(health.tec));
    emit(prefix + "rec", static_cast<double>(health.rec));
    emit(prefix + "bus_state",
         health.state_code == 0xFFFFFFFFu ? kUnknownBusState
                                          : static_cast<double>(health.state_code));
  }
}

/* Every streaming RPC's cumulative overflow count, one point per stream, plus
   the overflow sources that are not registry-backed (replay's stream queue,
   SignalRouter's ring, EventBus, and the event-store writer queue). Emitted
   with zeros when nothing has dropped: a consumer must be able to tell "no
   drops" from "this build does not report drops". */
template <typename Emit>
void AppendStreamDropMetrics(GatewayContext& ctx, std::uint64_t tick, Emit emit) {
  for (const char* name : stream_metrics::kRegistryBacked) {
    emit(name, static_cast<double>(ctx.stream_drops.Count(name)));
  }
  emit(stream_metrics::kReplayStream,
       static_cast<double>(ctx.replay_controller.DroppedEventCount()));
  emit(stream_metrics::kSignalRouterDropped,
       static_cast<double>(ctx.sim.signal_router().DroppedEventCount()));
  emit(stream_metrics::kEventBusDropped,
       static_cast<double>(ctx.sim.event_bus().DroppedEventCount()));
  // AsyncEventStore's queue and writer failures. Synchronous stores return
  // zero, so "no drops" and "does not report" remain distinguishable.
  emit("event_store.dropped",
       static_cast<double>(ctx.event_store.DroppedEventCount()));
  emit("event_store.write_errors",
       static_cast<double>(ctx.event_store.WriteErrorCount()));
}
/* PDU deadline misses are counted inside the router plugin (the only place
   that knows which PDUs are configured and when they were last received).
   Poll both managers because pdu_router can be loaded as a node plugin or as a
   simulation-scoped plugin. */
template <typename Emit>
void AppendPduDeadlineMetrics(GatewayContext& ctx, Emit emit) {
  const auto append = [&](boat::core::PluginManager& manager) {
    auto router = manager.AcquireService<boat::core::IPduRouter>("pdu_router");
    if (!router) return;
    for (const auto& [pdu_id, count] : router->DeadlineTimeoutCounts()) {
      emit("pdu." + std::to_string(pdu_id) + ".deadline_timeouts",
           static_cast<double>(count));
    }
  };
  append(ctx.plugin_manager);
  append(ctx.sim.plugin_manager());
}

}  // namespace

MetricsServiceImpl::MetricsServiceImpl(GatewayContext& ctx) : ctx_(ctx) {}

grpc::Status MetricsServiceImpl::GetMetrics(grpc::ServerContext*, const boat::v1::GetMetricsRequest* request,
                                            boat::v1::MetricsResponse* response) {
  boat::store::EventFilter filter;
  filter.simulation_id = request->simulation_id();
  const auto events = ctx_.event_store.Query(filter);
  for (const auto& event : events) {
    auto* point = response->add_points();
    point->set_tick(event.tick);
    point->set_name(event.signal_id);
    point->set_value(DecodeNumeric(event.value_blob));
  }
  const std::uint64_t tick = ctx_.sim.clock().tick();
  AppendCanHealthMetrics(ctx_, tick, [&](const std::string& name, double value) {
    auto* point = response->add_points();
    point->set_tick(tick);
    point->set_name(name);
    point->set_value(value);
  });
  AppendStreamDropMetrics(ctx_, tick, [&](const std::string& name, double value) {
    auto* point = response->add_points();
    point->set_tick(tick);
    point->set_name(name);
    point->set_value(value);
  });
  AppendPduDeadlineMetrics(ctx_, [&](const std::string& name, double value) {
    auto* point = response->add_points();
    point->set_tick(tick);
    point->set_name(name);
    point->set_value(value);
  });
  return grpc::Status::OK;
}

grpc::Status MetricsServiceImpl::StreamMetrics(grpc::ServerContext* context, const boat::v1::StreamMetricsRequest*,
                                               grpc::ServerWriter<boat::v1::MetricPoint>* writer) {
  /* Heap-owned and captured by value: EventBus dispatch may invoke the handler
     after Unsubscribe returns -- see subscriber_queue.h. Conflating by name:
     metrics are latest-value-wins, so a slow consumer gets the current value of
     every series instead of a tail of stale snapshots. */
  auto shared = MakeSubscriberQueue<boat::v1::MetricPoint>(
      &ctx_.stream_drops, stream_metrics::kMetricsStream,
      ctx_.stream_queue_capacity,
      [](const boat::v1::MetricPoint& point) { return point.name(); });

  const auto handle = ctx_.sim.event_bus().Subscribe(kMetricEventType, [shared](const boat::core::BusEvent& event) {
    boat::v1::MetricPoint point;
    point.set_tick(event.tick);
    if (const auto* unknown = std::get_if<boat::core::UnknownPayload>(&event.payload)) {
      if (!point.ParseFromArray(unknown->data.data(), static_cast<int>(unknown->data.size()))) {
        point.set_name("metric");
        point.set_value(0.0);
      }
    } else {
      point.set_name("metric");
      point.set_value(0.0);
    }
    std::size_t dropped = 0;
    {
      std::lock_guard<std::mutex> lock(shared->mutex);
      if (shared->closed) return;
      dropped = shared->Push(std::move(point));
    }
    shared->ReportDrop(dropped);
    shared->cv.notify_one();
  });

  auto last_health_push = std::chrono::steady_clock::now();
  while (!context->IsCancelled()) {
    std::deque<boat::v1::MetricPoint> local;
    {
      std::unique_lock<std::mutex> lock(shared->mutex);
      shared->cv.wait_for(lock, std::chrono::milliseconds(50),
                          [&shared] { return !shared->queue.empty(); });
      local.swap(shared->queue);
    }
    bool write_ok = true;
    for (const auto& metric : local) {
      if (!writer->Write(metric)) {
        write_ok = false;
        break;
      }
    }
    if (!write_ok) {
      break;
    }

    // CAN health is polled rather than event-driven: the counters live in the
    // kernel, and one push per second is enough to watch a bus degrade.
    const auto now = std::chrono::steady_clock::now();
    if (now - last_health_push >= std::chrono::seconds(1)) {
      last_health_push = now;
      const std::uint64_t tick = ctx_.sim.clock().tick();
      AppendCanHealthMetrics(ctx_, tick, [&](const std::string& name, double value) {
        if (!write_ok) {
          return;
        }
        boat::v1::MetricPoint point;
        point.set_tick(tick);
        point.set_name(name);
        point.set_value(value);
        if (!writer->Write(point)) {
          write_ok = false;
        }
      });
      AppendStreamDropMetrics(ctx_, tick, [&](const std::string& name, double value) {
        if (!write_ok) {
          return;
        }
        boat::v1::MetricPoint point;
        point.set_tick(tick);
        point.set_name(name);
        point.set_value(value);
        if (!writer->Write(point)) {
          write_ok = false;
        }
      });
      AppendPduDeadlineMetrics(ctx_, [&](const std::string& name, double value) {
        if (!write_ok) {
          return;
        }
        boat::v1::MetricPoint point;
        point.set_tick(tick);
        point.set_name(name);
        point.set_value(value);
        if (!writer->Write(point)) {
          write_ok = false;
        }
      });
      if (!write_ok) {
        break;
      }
    }
  }

  {
    std::lock_guard<std::mutex> lock(shared->mutex);
    shared->closed = true;
  }
  ctx_.sim.event_bus().Unsubscribe(handle);
  return grpc::Status::OK;
}

}  // namespace boat::gateway
