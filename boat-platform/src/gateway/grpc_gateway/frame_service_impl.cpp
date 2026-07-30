#include "frame_service_impl.h"

#include <algorithm>
#include <chrono>
#include <cstring>
#include <functional>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "can_bus_registry.h"
#include "core/frame.h"
#include "ethernet_bus_registry.h"
#include "frame_sink.h"

namespace boat::gateway {
namespace {

/* ── Proto → core::Frame conversion ─────────────────────────────────── */

static boat::core::Frame ProtoToFrame(const boat::v1::Frame& pf) {
  using boat::core::Frame;

  std::string iface = pf.iface();
  std::vector<uint8_t> payload(pf.payload().begin(), pf.payload().end());

  switch (pf.bus_type()) {
    case boat::v1::Frame::CAN:
    case boat::v1::Frame::CANFD: {
      const auto& cm = pf.can();
      return Frame::FromCan(std::move(iface), cm.can_id(),
                            static_cast<uint8_t>(cm.dlc()),
                            static_cast<uint8_t>(cm.flags()),
                            std::move(payload),
                            pf.bus_type() == boat::v1::Frame::CANFD);
    }
    case boat::v1::Frame::ETHERNET: {
      const auto& em = pf.eth();
      uint8_t dm[6], sm[6];
      std::memcpy(dm, em.dst_mac().data(), std::min(em.dst_mac().size(), 6UL));
      std::memcpy(sm, em.src_mac().data(), std::min(em.src_mac().size(), 6UL));
      const uint8_t* sip = em.src_ip().empty() ? nullptr
                            : reinterpret_cast<const uint8_t*>(em.src_ip().data());
      const uint8_t* dip = em.dst_ip().empty() ? nullptr
                            : reinterpret_cast<const uint8_t*>(em.dst_ip().data());
      return Frame::FromEthernet(std::move(iface), dm, sm,
                                 static_cast<uint16_t>(em.ethertype()),
                                 static_cast<uint16_t>(em.vlan_id()),
                                 sip, static_cast<uint8_t>(em.ip_version()), dip,
                                 std::move(payload));
    }
    case boat::v1::Frame::TCP: {
      const auto& tm = pf.tcp();
      const uint8_t* sip = tm.src_ip().empty() ? nullptr
                            : reinterpret_cast<const uint8_t*>(tm.src_ip().data());
      const uint8_t* dip = tm.dst_ip().empty() ? nullptr
                            : reinterpret_cast<const uint8_t*>(tm.dst_ip().data());
      return Frame::FromTcp(std::move(iface), sip,
                            static_cast<uint8_t>(tm.ip_version()), dip,
                            static_cast<uint16_t>(tm.src_port()),
                            static_cast<uint16_t>(tm.dst_port()),
                            tm.conn_id(), std::move(payload));
    }
    case boat::v1::Frame::PDU: {
      const auto& pm = pf.pdu();
      return Frame::FromPdu(std::move(iface), pm.pdu_id(), std::move(payload));
    }
    default:
      return Frame{};
  }
}

/* ── core::Frame → Proto conversion ─────────────────────────────────── */

static void FrameToProto(const boat::core::Frame& f, boat::v1::Frame* proto) {
  proto->set_bus_type(static_cast<boat::v1::Frame::BusType>(f.bus_type()));
  proto->set_iface(f.iface());
  proto->set_timestamp_ns(f.timestamp_ns());
  proto->set_payload(f.payload().data(), f.payload().size());

  switch (f.bus_type()) {
    case boat::core::Frame::BusType::kCan:
    case boat::core::Frame::BusType::kCanFd: {
      auto* cm = proto->mutable_can();
      cm->set_can_id(f.can_meta().can_id);
      cm->set_dlc(f.can_meta().dlc);
      cm->set_flags(f.can_meta().flags);
      break;
    }
    case boat::core::Frame::BusType::kEthernet: {
      auto* em = proto->mutable_eth();
      em->set_dst_mac(f.eth_meta().dst_mac, 6);
      em->set_src_mac(f.eth_meta().src_mac, 6);
      em->set_ethertype(f.eth_meta().ethertype);
      em->set_vlan_id(f.eth_meta().vlan_id);
      em->set_ip_version(f.eth_meta().ip_version);
      em->set_flags(f.eth_meta().flags);
      if (f.eth_meta().ip_version == 4) {
        em->set_src_ip(f.eth_meta().src_ip, 4);
        em->set_dst_ip(f.eth_meta().dst_ip, 4);
      } else if (f.eth_meta().ip_version == 6) {
        em->set_src_ip(f.eth_meta().src_ip, 16);
        em->set_dst_ip(f.eth_meta().dst_ip, 16);
      }
      break;
    }
    case boat::core::Frame::BusType::kTcp: {
      auto* tm = proto->mutable_tcp();
      tm->set_ip_version(f.tcp_meta().ip_version);
      tm->set_src_port(f.tcp_meta().src_port);
      tm->set_dst_port(f.tcp_meta().dst_port);
      tm->set_conn_id(f.tcp_meta().conn_id);
      if (f.tcp_meta().ip_version == 4) {
        tm->set_src_ip(f.tcp_meta().src_ip, 4);
        tm->set_dst_ip(f.tcp_meta().dst_ip, 4);
      } else if (f.tcp_meta().ip_version == 6) {
        tm->set_src_ip(f.tcp_meta().src_ip, 16);
        tm->set_dst_ip(f.tcp_meta().dst_ip, 16);
      }
      break;
    }
    case boat::core::Frame::BusType::kPdu: {
      auto* pm = proto->mutable_pdu();
      pm->set_pdu_id(f.pdu_meta().pdu_id);
      break;
    }
    default:
      break;
  }
}

/* ── Shared subscription plumbing ───────────────────────────────────── */

/* RX callbacks registered for the lifetime of one streaming RPC.
   Unsubscribes on destruction, so callbacks can never outlive the stream
   they write to. */
class FrameSubscription {
 public:
  FrameSubscription(GatewayContext& ctx,
                    const boat::v1::SubscribeFramesRequest& request,
                    std::function<void(const boat::v1::Frame&)> emit)
      : ctx_(ctx) {
    bool want_can = true;
    bool want_eth = true;
    if (!request.bus_types().empty()) {
      want_can = false;
      want_eth = false;
      for (auto bt : request.bus_types()) {
        if (bt == boat::v1::Frame::CAN || bt == boat::v1::Frame::CANFD) want_can = true;
        if (bt == boat::v1::Frame::ETHERNET) want_eth = true;
      }
    }

    // An empty filter means every interface, matching SubscribeFramesRequest.
    const std::string iface_filter = request.iface_filter();
    auto forward = [emit = std::move(emit), iface_filter](const boat::core::Frame& f) {
      if (!iface_filter.empty() && f.iface() != iface_filter) return;
      boat::v1::Frame proto;
      FrameToProto(f, &proto);
      emit(proto);
    };

    if (want_can) can_.push_back(ctx_.can_bus_registry.SubscribeFrame(forward));
    if (want_eth) eth_.push_back(ctx_.ethernet_bus_registry.SubscribeFrame(forward));
  }

  ~FrameSubscription() {
    for (auto id : can_) ctx_.can_bus_registry.UnsubscribeFrame(id);
    for (auto id : eth_) ctx_.ethernet_bus_registry.UnsubscribeFrame(id);
  }

  FrameSubscription(const FrameSubscription&)            = delete;
  FrameSubscription& operator=(const FrameSubscription&) = delete;

 private:
  GatewayContext& ctx_;
  std::vector<boat::hil::CanBusRegistry::RxCallbackId>      can_;
  std::vector<boat::hil::EthernetBusRegistry::RxCallbackId> eth_;
};

/* Transmit one client-supplied frame. Shared by SendFrame and StreamFrames so
   both apply identical interface validation and take the same FrameSink path. */
static grpc::Status PublishFrame(GatewayContext& ctx, const boat::v1::Frame& pf) {
  auto frame = ProtoToFrame(pf);

  switch (frame.bus_type()) {
    case boat::core::Frame::BusType::kCan:
    case boat::core::Frame::BusType::kCanFd:
      if (frame.iface().empty() || !ctx.can_bus_registry.Has(frame.iface())) {
        return grpc::Status(grpc::NOT_FOUND, "CAN interface not found");
      }
      ctx.frame_sink.Publish(frame);
      return grpc::Status::OK;

    case boat::core::Frame::BusType::kEthernet:
      if (frame.iface().empty() || !ctx.ethernet_bus_registry.Has(frame.iface())) {
        return grpc::Status(grpc::NOT_FOUND, "Ethernet interface not found");
      }
      ctx.frame_sink.Publish(frame);
      return grpc::Status::OK;

    case boat::core::Frame::BusType::kPdu: {
      // PDU frames are not a wire bus — dispatch to the plugin frame bus so the
      // pdu_router plugin (if loaded) routes them onto their configured transport.
      BoatFrame abi{};
      frame.ToAbi(&abi);
      ctx.plugin_manager.DispatchFrame(abi);
      return grpc::Status::OK;
    }

    case boat::core::Frame::BusType::kTcp:
      // TCP is a stateful conversation, not a fire-and-forget frame: it is driven
      // through the TCP plugin's own connection API, not raw frame transmission.
      return grpc::Status(
          grpc::StatusCode::UNIMPLEMENTED,
          "TCP is connection-oriented; use the TCP plugin, not FrameService.SendFrame");

    default:
      return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT, "unknown bus type");
  }
}

}  // namespace

FrameServiceImpl::FrameServiceImpl(GatewayContext& ctx) : ctx_(ctx) {}

grpc::Status FrameServiceImpl::SendFrame(grpc::ServerContext*,
                                         const boat::v1::SendFrameRequest* request,
                                         boat::v1::SendFrameResponse* response) {
  const grpc::Status status = PublishFrame(ctx_, request->frame());

  // An unrecognised bus type has always been reported in-band as accepted=false
  // rather than as an RPC error; keep that contract for existing callers.
  if (status.error_code() == grpc::StatusCode::INVALID_ARGUMENT) {
    response->set_accepted(false);
    return grpc::Status::OK;
  }
  if (!status.ok()) return status;

  response->set_accepted(true);
  return grpc::Status::OK;
}

grpc::Status FrameServiceImpl::SubscribeFrames(
    grpc::ServerContext* context,
    const boat::v1::SubscribeFramesRequest* request,
    grpc::ServerWriter<boat::v1::Frame>* writer) {

  std::mutex write_mutex;
  FrameSubscription subscription(
      ctx_, *request, [&write_mutex, writer](const boat::v1::Frame& proto) {
        std::lock_guard<std::mutex> lock(write_mutex);
        writer->Write(proto);
      });

  // Wait for client disconnect; the subscription unsubscribes on scope exit.
  while (!context->IsCancelled()) {
    std::this_thread::sleep_for(std::chrono::milliseconds(50));
  }

  return grpc::Status::OK;
}

grpc::Status FrameServiceImpl::StreamFrames(
    grpc::ServerContext* context,
    grpc::ServerReaderWriter<boat::v1::Frame, boat::v1::StreamFramesRequest>* stream) {

  std::mutex write_mutex;
  auto emit = [&write_mutex, stream](const boat::v1::Frame& proto) {
    std::lock_guard<std::mutex> lock(write_mutex);
    stream->Write(proto);
  };

  // Created on demand: a client that only pushes frames never subscribes, and
  // resubscribing replaces the previous registration.
  std::unique_ptr<FrameSubscription> subscription;

  boat::v1::StreamFramesRequest message;
  while (stream->Read(&message)) {
    switch (message.kind_case()) {
      case boat::v1::StreamFramesRequest::kSubscribe:
        subscription.reset();
        subscription =
            std::make_unique<FrameSubscription>(ctx_, message.subscribe(), emit);
        break;

      case boat::v1::StreamFramesRequest::kFrame: {
        const grpc::Status status = PublishFrame(ctx_, message.frame());
        if (!status.ok()) {
          // Fail the call rather than dropping: the target interface is fixed
          // for a bridge session, so this is a misconfiguration, and a silently
          // discarded frame on a vehicle bus is worse than a broken stream.
          return status;
        }
        break;
      }

      default:
        return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT,
                            "StreamFramesRequest must set subscribe or frame");
    }
  }

  // The client half-closed but may still be reading: keep the subscription alive
  // until the call is actually cancelled.
  while (!context->IsCancelled()) {
    std::this_thread::sleep_for(std::chrono::milliseconds(50));
  }

  return grpc::Status::OK;
}

}  // namespace boat::gateway
