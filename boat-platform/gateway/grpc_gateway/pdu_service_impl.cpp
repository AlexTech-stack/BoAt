#include "pdu_service_impl.h"

#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <mutex>
#include <sstream>
#include <vector>

#include "pdu/pdu_router.h"
#include "rpc_audit_log.h"

namespace boat::gateway {

PduServiceImpl::PduServiceImpl(GatewayContext& ctx) : ctx_(ctx) {}

// ── helpers ───────────────────────────────────────────────────────────────────

static uint64_t NowNsPdu() {
  return static_cast<uint64_t>(
      std::chrono::duration_cast<std::chrono::nanoseconds>(
          std::chrono::system_clock::now().time_since_epoch()).count());
}

static boat::hil::PduRoute ProtoToRoute(const boat::v1::PduRoute& pr) {
  boat::hil::PduRoute r;
  r.pdu_id = pr.pdu_id();
  r.iface  = pr.iface();
  r.vlan_id    = static_cast<uint16_t>(pr.vlan_id()   & 0x0FFF);
  r.can_id     = pr.can_id();
  r.ethertype  = static_cast<uint16_t>(pr.ethertype() & 0xFFFF);
  switch (pr.transport()) {
    case boat::v1::PDU_TRANSPORT_CAN:      r.transport = boat::hil::PduTransport::kCan;      break;
    case boat::v1::PDU_TRANSPORT_ETHERNET: r.transport = boat::hil::PduTransport::kEthernet; break;
    default:                               r.transport = boat::hil::PduTransport::kUnspecified; break;
  }
  return r;
}

static void RouteToProto(const boat::hil::PduRoute& r, boat::v1::PduRoute* pr) {
  pr->set_pdu_id(r.pdu_id);
  pr->set_iface(r.iface);
  pr->set_vlan_id(r.vlan_id);
  pr->set_can_id(r.can_id);
  pr->set_ethertype(r.ethertype);
  switch (r.transport) {
    case boat::hil::PduTransport::kCan:      pr->set_transport(boat::v1::PDU_TRANSPORT_CAN);      break;
    case boat::hil::PduTransport::kEthernet: pr->set_transport(boat::v1::PDU_TRANSPORT_ETHERNET); break;
    default:                                 pr->set_transport(boat::v1::PDU_TRANSPORT_UNSPECIFIED); break;
  }
}

static void PduFrameToProto(const boat::hil::PduFrame& f, boat::v1::PduFrame* pf) {
  pf->set_pdu_id(f.pdu_id);
  pf->set_payload(f.payload.data(), f.payload.size());
  pf->set_timestamp_ns(f.timestamp_ns);
  pf->set_iface(f.iface);
  switch (f.source) {
    case boat::hil::PduTransport::kCan:      pf->set_source(boat::v1::PDU_TRANSPORT_CAN);      break;
    case boat::hil::PduTransport::kEthernet: pf->set_source(boat::v1::PDU_TRANSPORT_ETHERNET); break;
    default:                                 pf->set_source(boat::v1::PDU_TRANSPORT_UNSPECIFIED); break;
  }
}

// ── handlers ──────────────────────────────────────────────────────────────────

grpc::Status PduServiceImpl::SendPdu(
    grpc::ServerContext* context,
    const boat::v1::SendPduRequest* request,
    boat::v1::SendPduResponse* response) {

  const auto& pf = request->pdu();
  if (pf.pdu_id() == 0) {
    return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT, "pdu_id must be non-zero");
  }

  const std::vector<uint8_t> payload(pf.payload().begin(), pf.payload().end());
  const bool accepted = ctx_.pdu_router.SendPdu(pf.pdu_id(), payload);

  {
    RpcEvent ev;
    ev.timestamp_ns = NowNsPdu();
    ev.method     = "PduService/SendPdu";
    ev.peer       = context->peer();
    ev.event_type = "DATA";
    ev.call_type  = "UNARY";
    std::ostringstream ss;
    ss << "pdu_id=0x" << std::hex << pf.pdu_id()
       << std::dec << "  len=" << payload.size()
       << (accepted ? "" : "  [no route]");
    ev.summary = ss.str();
    ctx_.audit_log.Push(std::move(ev));
  }

  if (!accepted) {
    return grpc::Status(grpc::StatusCode::NOT_FOUND,
                        "No route configured for pdu_id or send failed");
  }
  response->set_accepted(true);
  return grpc::Status::OK;
}

grpc::Status PduServiceImpl::SubscribePdus(
    grpc::ServerContext* context,
    const boat::v1::SubscribePdusRequest* request,
    grpc::ServerWriter<boat::v1::PduFrame>* writer) {

  std::vector<uint32_t> pdu_ids(request->pdu_ids().begin(),
                                 request->pdu_ids().end());

  const std::string peer = context->peer();

  {
    std::ostringstream ss;
    if (pdu_ids.empty()) {
      ss << "pdu_ids=(all)";
    } else {
      ss << "pdu_ids=[";
      for (std::size_t i = 0; i < pdu_ids.size(); ++i) {
        if (i) ss << ',';
        ss << "0x" << std::hex << pdu_ids[i];
      }
      ss << ']';
    }
    RpcEvent ev;
    ev.timestamp_ns = NowNsPdu();
    ev.method     = "PduService/SubscribePdus";
    ev.peer       = peer;
    ev.event_type = "SUBSCRIBE_OPEN";
    ev.call_type  = "SERVER_STREAM";
    ev.summary    = ss.str();
    ctx_.audit_log.Push(std::move(ev));
  }

  std::mutex                      queue_mutex;
  std::condition_variable         queue_cv;
  std::vector<boat::v1::PduFrame> queue;

  const auto sub_id = ctx_.pdu_router.Subscribe(
      pdu_ids,
      [&queue_mutex, &queue_cv, &queue](const boat::hil::PduFrame& f) {
        boat::v1::PduFrame proto;
        PduFrameToProto(f, &proto);
        {
          std::lock_guard<std::mutex> lock(queue_mutex);
          queue.push_back(std::move(proto));
        }
        queue_cv.notify_one();
      });

  while (!context->IsCancelled()) {
    std::vector<boat::v1::PduFrame> pending;
    {
      std::unique_lock<std::mutex> lock(queue_mutex);
      queue_cv.wait_for(lock, std::chrono::milliseconds(50),
                        [&queue] { return !queue.empty(); });
      pending.swap(queue);
    }
    for (const auto& proto : pending) {
      if (!writer->Write(proto)) {
        ctx_.pdu_router.Unsubscribe(sub_id);
        return grpc::Status::OK;
      }
      RpcEvent ev;
      ev.timestamp_ns = NowNsPdu();
      ev.method     = "PduService/SubscribePdus";
      ev.peer       = peer;
      ev.event_type = "DATA";
      ev.call_type  = "SERVER_STREAM";
      std::ostringstream ss;
      ss << "pdu_id=0x" << std::hex << proto.pdu_id()
         << std::dec << "  len=" << proto.payload().size();
      ev.summary = ss.str();
      ctx_.audit_log.Push(std::move(ev));
    }
  }

  ctx_.pdu_router.Unsubscribe(sub_id);
  return grpc::Status::OK;
}

grpc::Status PduServiceImpl::ConfigureRoute(
    grpc::ServerContext* context,
    const boat::v1::ConfigureRouteRequest* request,
    boat::v1::ConfigureRouteResponse* response) {

  const boat::hil::PduRoute route = ProtoToRoute(request->route());

  if (route.pdu_id == 0) {
    return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT, "pdu_id must be non-zero");
  }
  if (route.transport == boat::hil::PduTransport::kUnspecified) {
    return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT,
                        "transport must be CAN or ETHERNET");
  }

  ctx_.pdu_router.AddRoute(route);

  {
    RpcEvent ev;
    ev.timestamp_ns = NowNsPdu();
    ev.method     = "PduService/ConfigureRoute";
    ev.peer       = context->peer();
    ev.event_type = "CONFIG";
    ev.call_type  = "UNARY";
    std::ostringstream ss;
    ss << "pdu_id=0x" << std::hex << route.pdu_id
       << "  transport=" << (route.transport == boat::hil::PduTransport::kCan ? "CAN" : "ETH")
       << "  iface=" << route.iface;
    ev.summary = ss.str();
    ctx_.audit_log.Push(std::move(ev));
  }

  response->set_ok(true);
  return grpc::Status::OK;
}

grpc::Status PduServiceImpl::ListRoutes(
    grpc::ServerContext*,
    const boat::v1::ListRoutesRequest*,
    boat::v1::ListRoutesResponse* response) {

  for (const auto& r : ctx_.pdu_router.ListRoutes()) {
    RouteToProto(r, response->add_routes());
  }
  return grpc::Status::OK;
}

}  // namespace boat::gateway
