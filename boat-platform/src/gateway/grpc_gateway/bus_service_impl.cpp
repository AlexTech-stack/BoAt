#include "bus_service_impl.h"

#include <chrono>
#include <cstdint>
#include <mutex>
#include <sstream>
#include <thread>
#include <vector>

namespace boat::gateway {

// ── Internal registry ─────────────────────────────────────────────────────────

BusServiceImpl::SubId BusServiceImpl::Subscribe(std::vector<std::string> names,
                                                 Callback cb) {
  std::lock_guard<std::mutex> lock(subs_mutex_);
  const SubId id = next_id_++;
  subscriptions_[id] = Subscription{std::move(names), std::move(cb)};
  return id;
}

void BusServiceImpl::Unsubscribe(SubId id) {
  std::lock_guard<std::mutex> lock(subs_mutex_);
  subscriptions_.erase(id);
}

void BusServiceImpl::Dispatch(const boat::v1::BusSignal& signal) {
  // Snapshot to avoid holding the lock during callbacks.
  std::vector<Callback> to_call;
  {
    std::lock_guard<std::mutex> lock(subs_mutex_);
    for (const auto& [id, sub] : subscriptions_) {
      if (sub.names.empty()) {
        to_call.push_back(sub.cb);
        continue;
      }
      for (const auto& n : sub.names) {
        if (n == signal.name()) {
          to_call.push_back(sub.cb);
          break;
        }
      }
    }
  }
  for (const auto& cb : to_call) {
    cb(signal);
  }
}

// ── RPC handlers ──────────────────────────────────────────────────────────────

static uint64_t NowNsBus() {
  return static_cast<uint64_t>(
      std::chrono::duration_cast<std::chrono::nanoseconds>(
          std::chrono::system_clock::now().time_since_epoch()).count());
}

static std::string SignalSummary(const boat::v1::BusSignal& sig) {
  using VC = boat::v1::BusSignal::ValueCase;
  std::ostringstream ss;
  ss << sig.name() << " = ";
  switch (sig.value_case()) {
    case VC::kNumberValue: ss << sig.number_value(); break;
    case VC::kStringValue: ss << '"' << sig.string_value() << '"'; break;
    case VC::kBoolValue:   ss << (sig.bool_value() ? "true" : "false"); break;
    case VC::kBytesValue: {
      const auto& b = sig.bytes_value();
      for (std::size_t i = 0; i < b.size() && i < 8; ++i) {
        if (i) ss << ':';
        const auto v = static_cast<unsigned>(static_cast<uint8_t>(b[i]));
        if (v < 16) ss << '0';
        ss << std::hex << std::uppercase << v;
      }
      if (b.size() > 8) ss << "...";
      break;
    }
    default: ss << "(empty)"; break;
  }
  if (!sig.publisher().empty()) ss << "  (pub: " << sig.publisher() << ')';
  return ss.str();
}

grpc::Status BusServiceImpl::Publish(grpc::ServerContext* context,
                                     const boat::v1::BusPublishRequest* request,
                                     boat::v1::BusPublishResponse* response) {
  if (request->signal().name().empty()) {
    return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT, "signal name required");
  }

  // Stamp wall-clock time if the publisher didn't set one.
  boat::v1::BusSignal signal = request->signal();
  if (signal.timestamp_ns() == 0) {
    signal.set_timestamp_ns(static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::system_clock::now().time_since_epoch())
            .count()));
  }

  Dispatch(signal);

  RpcEvent ev;
  ev.timestamp_ns = NowNsBus();
  ev.method     = "BusService/Publish";
  ev.peer       = context->peer();
  ev.event_type = "DATA";
  ev.call_type  = "UNARY";
  ev.summary    = SignalSummary(signal);
  audit_log_.Push(std::move(ev));

  response->set_accepted(true);
  return grpc::Status::OK;
}

grpc::Status BusServiceImpl::Subscribe(
    grpc::ServerContext* context,
    const boat::v1::BusSubscribeRequest* request,
    grpc::ServerWriter<boat::v1::BusSignal>* writer) {

  const std::string peer = context->peer();
  std::vector<std::string> names(request->names().begin(), request->names().end());

  // Audit: subscription opened.
  {
    std::ostringstream ss;
    if (names.empty()) {
      ss << "filter=(all)";
    } else {
      ss << "filter=";
      for (std::size_t i = 0; i < names.size(); ++i) {
        if (i) ss << ',';
        ss << names[i];
      }
    }
    RpcEvent ev;
    ev.timestamp_ns = NowNsBus();
    ev.method     = "BusService/Subscribe";
    ev.peer       = peer;
    ev.event_type = "SUBSCRIBE_OPEN";
    ev.call_type  = "SERVER_STREAM";
    ev.summary    = ss.str();
    audit_log_.Push(std::move(ev));
  }

  std::mutex queue_mutex;
  std::vector<boat::v1::BusSignal> queue;

  const SubId sub_id = Subscribe(
      names,
      [&queue_mutex, &queue](const boat::v1::BusSignal& sig) {
        std::lock_guard<std::mutex> lock(queue_mutex);
        queue.push_back(sig);
      });

  while (!context->IsCancelled()) {
    std::vector<boat::v1::BusSignal> pending;
    {
      std::lock_guard<std::mutex> lock(queue_mutex);
      pending.swap(queue);
    }
    for (const auto& sig : pending) {
      if (!writer->Write(sig)) {
        Unsubscribe(sub_id);
        return grpc::Status::OK;
      }
      RpcEvent ev;
      ev.timestamp_ns = NowNsBus();
      ev.method     = "BusService/Subscribe";
      ev.peer       = peer;
      ev.event_type = "DATA";
      ev.call_type  = "SERVER_STREAM";
      ev.summary    = SignalSummary(sig);
      audit_log_.Push(std::move(ev));
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
  }

  Unsubscribe(sub_id);
  return grpc::Status::OK;
}

}  // namespace boat::gateway
