// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#include "pdu_service_impl.h"

#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <mutex>
#include <sstream>
#include <vector>

#include "pdu/pdu_router.h"
#include "rpc_audit_log.h"
#include "subscriber_queue.h"

namespace boat::gateway {

PduServiceImpl::PduServiceImpl(GatewayContext& ctx) : ctx_(ctx) {}

boat::core::ServiceRef<boat::core::IPduRouter> PduServiceImpl::GetRouter() {
  return ctx_.plugin_manager.AcquireService<boat::core::IPduRouter>("pdu_router");
}

// ── helpers ───────────────────────────────────────────────────────────────────

static uint64_t NowNsPdu() {
  return static_cast<uint64_t>(
      std::chrono::duration_cast<std::chrono::nanoseconds>(
          std::chrono::system_clock::now().time_since_epoch()).count());
}

static boat::hil::SendType ProtoToSendType(boat::v1::SendType st) {
  switch (st) {
    case boat::v1::SEND_TYPE_CYCLIC:     return boat::hil::SendType::kCyclic;
    case boat::v1::SEND_TYPE_ON_CHANGE:  return boat::hil::SendType::kOnChange;
    case boat::v1::SEND_TYPE_MIXED:      return boat::hil::SendType::kMixed;
    default:                             return boat::hil::SendType::kNone;
  }
}

static boat::v1::SendType SendTypeToProto(boat::hil::SendType st) {
  switch (st) {
    case boat::hil::SendType::kCyclic:    return boat::v1::SEND_TYPE_CYCLIC;
    case boat::hil::SendType::kOnChange:  return boat::v1::SEND_TYPE_ON_CHANGE;
    case boat::hil::SendType::kMixed:     return boat::v1::SEND_TYPE_MIXED;
    default:                              return boat::v1::SEND_TYPE_NONE;
  }
}

static void ScheduleToProto(const boat::hil::PduSchedule& s,
                             boat::v1::PduSchedule* ps) {
  ps->set_send_type(SendTypeToProto(s.send_type));
  ps->set_cycle_ms(s.cycle_ms);
  ps->set_fast_ms(s.fast_ms);
  ps->set_repetitions(s.repetitions);
}

static boat::hil::PduRoute ProtoToRoute(const boat::v1::PduRoute& pr) {
  boat::hil::PduRoute r;
  r.pdu_id    = pr.pdu_id();
  r.iface     = pr.iface();
  r.vlan_id   = static_cast<uint16_t>(pr.vlan_id()   & 0x0FFF);
  r.can_id    = pr.can_id();
  r.can_flags = static_cast<uint8_t>(pr.can_flags() & 0xFF);
  r.ethertype = static_cast<uint16_t>(pr.ethertype() & 0xFFFF);
  r.src_ip.assign(pr.src_ip().begin(), pr.src_ip().end());
  r.dst_ip.assign(pr.dst_ip().begin(), pr.dst_ip().end());
  r.src_port  = static_cast<uint16_t>(pr.src_port() & 0xFFFF);
  r.dst_port  = static_cast<uint16_t>(pr.dst_port() & 0xFFFF);
  r.ttl       = pr.ttl() != 0 ? static_cast<uint8_t>(pr.ttl() & 0xFF) : 64;
  switch (pr.transport()) {
    case boat::v1::PDU_TRANSPORT_CAN:      r.transport = boat::hil::PduTransport::kCan;      break;
    case boat::v1::PDU_TRANSPORT_ETHERNET: r.transport = boat::hil::PduTransport::kEthernet; break;
    default:                               r.transport = boat::hil::PduTransport::kUnspecified; break;
  }
  if (pr.has_schedule()) {
    r.schedule.send_type  = ProtoToSendType(pr.schedule().send_type());
    r.schedule.cycle_ms   = pr.schedule().cycle_ms();
    r.schedule.fast_ms    = pr.schedule().fast_ms();
    r.schedule.repetitions = pr.schedule().repetitions();
  }
  return r;
}

static void RouteToProto(const boat::hil::PduRoute& r, boat::v1::PduRoute* pr) {
  pr->set_pdu_id(r.pdu_id);
  pr->set_iface(r.iface);
  pr->set_vlan_id(r.vlan_id);
  pr->set_can_id(r.can_id);
  pr->set_can_flags(r.can_flags);
  pr->set_ethertype(r.ethertype);
  pr->set_src_ip(r.src_ip.data(), r.src_ip.size());
  pr->set_dst_ip(r.dst_ip.data(), r.dst_ip.size());
  pr->set_src_port(r.src_port);
  pr->set_dst_port(r.dst_port);
  pr->set_ttl(r.ttl);
  switch (r.transport) {
    case boat::hil::PduTransport::kCan:      pr->set_transport(boat::v1::PDU_TRANSPORT_CAN);      break;
    case boat::hil::PduTransport::kEthernet: pr->set_transport(boat::v1::PDU_TRANSPORT_ETHERNET); break;
    default:                                 pr->set_transport(boat::v1::PDU_TRANSPORT_UNSPECIFIED); break;
  }
  if (r.schedule.send_type != boat::hil::SendType::kNone) {
    ScheduleToProto(r.schedule, pr->mutable_schedule());
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

static boat::hil::PduGroup ProtoToGroup(const boat::v1::PduGroup& pg) {
  boat::hil::PduGroup g;
  g.group_id = pg.group_id();
  g.name     = pg.name();
  g.enabled  = pg.enabled();
  g.pdu_ids.assign(pg.pdu_ids().begin(), pg.pdu_ids().end());
  return g;
}

static void GroupToProto(const boat::hil::PduGroup& g, boat::v1::PduGroup* pg) {
  pg->set_group_id(g.group_id);
  pg->set_name(g.name);
  pg->set_enabled(g.enabled);
  for (auto pid : g.pdu_ids) pg->add_pdu_ids(pid);
}

// ── handlers ──────────────────────────────────────────────────────────────────

grpc::Status PduServiceImpl::SendPdu(
    grpc::ServerContext* context,
    const boat::v1::SendPduRequest* request,
    boat::v1::SendPduResponse* response) {
  if (!GetRouter()) return grpc::Status(grpc::StatusCode::NOT_FOUND, "PduRouter plugin not loaded");

  const auto& pf = request->pdu();
  if (pf.pdu_id() == 0) {
    return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT, "pdu_id must be non-zero");
  }

  const std::vector<uint8_t> payload(pf.payload().begin(), pf.payload().end());
  const bool accepted = GetRouter()->SendPdu(pf.pdu_id(), payload);

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
       << (accepted ? "" : "  [no route or gated]");
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
  if (!GetRouter()) return grpc::Status(grpc::StatusCode::NOT_FOUND, "PduRouter plugin not loaded");

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

  /* Heap-owned and captured by value: PduRouter::DispatchPdu snapshots the
     callbacks under its lock and invokes them after releasing it, so a
     callback can still be in flight after Unsubscribe -- see subscriber_queue.h.
     Bounded: a slow subscriber loses the oldest PDUs, counted as
     grpc.pdus.subscribe.dropped. */
  auto shared = MakeSubscriberQueue<boat::v1::PduFrame>(
      &ctx_.stream_drops, stream_metrics::kPdusSubscribe,
      ctx_.stream_queue_capacity);

  // Scoped deliberately: the ref must NOT stay alive for the life of the
  // stream, or Unload would block until the client disconnects. Every later
  // use re-acquires and copes with the router having gone away.
  boat::core::IPduRouter::SubId sub_id = 0;
  {
    auto router = GetRouter();
    if (!router) {
      return grpc::Status(grpc::StatusCode::NOT_FOUND, "PduRouter plugin not loaded");
    }
    sub_id = router->Subscribe(
      pdu_ids,
      [shared](const boat::hil::PduFrame& f) {
        boat::v1::PduFrame proto;
        PduFrameToProto(f, &proto);
        std::size_t dropped = 0;
        {
          std::lock_guard<std::mutex> lock(shared->mutex);
          if (shared->closed) return;
          dropped = shared->Push(std::move(proto));
        }
        shared->ReportDrop(dropped);
        shared->cv.notify_one();
      });
  }

  while (!context->IsCancelled()) {
    std::deque<boat::v1::PduFrame> pending;
    {
      std::unique_lock<std::mutex> lock(shared->mutex);
      shared->cv.wait_for(lock, std::chrono::milliseconds(50),
                          [&shared] { return !shared->queue.empty(); });
      pending.swap(shared->queue);
    }
    for (const auto& proto : pending) {
      if (!writer->Write(proto)) {
        // Router may have been unloaded while the stream was open; the
        // subscription died with it, so there is nothing to unsubscribe.
        if (auto router = GetRouter()) router->Unsubscribe(sub_id);
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

  if (auto router = GetRouter()) router->Unsubscribe(sub_id);
  return grpc::Status::OK;
}

grpc::Status PduServiceImpl::ConfigureRoute(
    grpc::ServerContext* context,
    const boat::v1::ConfigureRouteRequest* request,
    boat::v1::ConfigureRouteResponse* response) {
  if (!GetRouter()) return grpc::Status(grpc::StatusCode::NOT_FOUND, "PduRouter plugin not loaded");

  boat::hil::PduRoute route = ProtoToRoute(request->route());

  if (route.pdu_id == 0) {
    return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT, "pdu_id must be non-zero");
  }
  if (route.transport == boat::hil::PduTransport::kUnspecified) {
    return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT,
                        "transport must be CAN or ETHERNET");
  }
  if (route.transport == boat::hil::PduTransport::kCan) {
    // Validate the wire field before ProtoToRoute narrows it to uint8; high
    // bits must not disappear silently.
    constexpr std::uint32_t kAllowedCanFlags = 0x37;
    if ((request->route().can_flags() & ~kAllowedCanFlags) != 0) {
      return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT,
                          "can_flags contains an unknown CAN flag bit");
    }
    const bool fd = (route.can_flags & boat::hil::kCanFdFlagFdf) != 0;
    const bool rtr = (route.can_flags & boat::hil::kCanFlagRtr) != 0;
    if (fd && rtr) {
      return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT,
                          "CAN FD and RTR are mutually exclusive");
    }
    if (route.can_id > 0x1FFFFFFF) {
      return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT,
                          "can_id must fit in 29 bits");
    }
    if ((route.can_flags & boat::hil::kCanFlagExtended) == 0 && route.can_id > 0x7FF) {
      route.can_flags |= boat::hil::kCanFlagExtended;
    }
  }

  if (!route.dst_ip.empty() || !route.src_ip.empty()) {
    const bool v4 = route.src_ip.size() == 4 && route.dst_ip.size() == 4;
    const bool v6 = route.src_ip.size() == 16 && route.dst_ip.size() == 16;
    if (!v4 && !v6) {
      return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT,
                          "IP routes require src_ip and dst_ip of equal length "
                          "(4 bytes for IPv4, 16 for IPv6)");
    }
  }

  GetRouter()->AddRoute(route);

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
    if (route.schedule.send_type != boat::hil::SendType::kNone) {
      ss << "  schedule=";
      switch (route.schedule.send_type) {
        case boat::hil::SendType::kCyclic:    ss << "cyclic:" << route.schedule.cycle_ms << "ms"; break;
        case boat::hil::SendType::kOnChange:  ss << "onchange"; break;
        case boat::hil::SendType::kMixed:     ss << "mixed:" << route.schedule.cycle_ms << "ms"; break;
        default: break;
      }
    }
    ev.summary = ss.str();
    ctx_.audit_log.Push(std::move(ev));
  }

  response->set_ok(true);
  return grpc::Status::OK;
}

grpc::Status PduServiceImpl::ConfigureContainer(
    grpc::ServerContext* context,
    const boat::v1::ConfigureContainerRequest* request,
    boat::v1::ConfigureContainerResponse* response) {
  if (!GetRouter()) return grpc::Status(grpc::StatusCode::NOT_FOUND, "PduRouter plugin not loaded");

  const auto& c = request->container();
  if (c.container_id() == 0) {
    return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT,
                        "container_id must be non-zero");
  }
  if (c.pdu_ids_size() == 0) {
    return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT,
                        "container must include at least one pdu_id");
  }
  if (c.dst_ip().empty()) {
    return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT,
                        "dst_ip is required for container transport");
  }

  boat::hil::PduContainerDef def;
  def.container_id = c.container_id();
  def.iface        = c.iface();
  def.src_ip.assign(c.src_ip().begin(),  c.src_ip().end());
  def.dst_ip.assign(c.dst_ip().begin(),  c.dst_ip().end());
  def.src_port  = static_cast<uint16_t>(c.src_port() & 0xFFFF);
  def.dst_port  = static_cast<uint16_t>(c.dst_port() & 0xFFFF);
  def.ttl       = c.ttl() != 0 ? static_cast<uint8_t>(c.ttl() & 0xFF) : 64;
  def.vlan_id   = static_cast<uint16_t>(c.vlan_id() & 0x0FFF);
  def.pdu_ids.assign(c.pdu_ids().begin(), c.pdu_ids().end());

  GetRouter()->AddContainer(def);

  {
    RpcEvent ev;
    ev.timestamp_ns = NowNsPdu();
    ev.method     = "PduService/ConfigureContainer";
    ev.peer       = context->peer();
    ev.event_type = "CONFIG";
    ev.call_type  = "UNARY";
    std::ostringstream ss;
    ss << "container_id=" << def.container_id
       << "  pdu_ids=[";
    for (std::size_t i = 0; i < def.pdu_ids.size(); ++i) {
      if (i) ss << ',';
      ss << "0x" << std::hex << def.pdu_ids[i];
    }
    ss << "]  iface=" << def.iface;
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
  if (!GetRouter()) return grpc::Status(grpc::StatusCode::NOT_FOUND, "PduRouter plugin not loaded");

  for (const auto& r : GetRouter()->ListRoutes()) {
    RouteToProto(r, response->add_routes());
  }
  return grpc::Status::OK;
}

// ── RemoveRoute handler ──────────────────────────────────────────────────────

grpc::Status PduServiceImpl::RemoveRoute(
    grpc::ServerContext* context,
    const boat::v1::RemoveRouteRequest* request,
    boat::v1::RemoveRouteResponse* response) {
  if (!GetRouter()) return grpc::Status(grpc::StatusCode::NOT_FOUND, "PduRouter plugin not loaded");

  GetRouter()->RemoveRoute(request->pdu_id());

  {
    RpcEvent ev;
    ev.timestamp_ns = NowNsPdu();
    ev.method     = "PduService/RemoveRoute";
    ev.peer       = context->peer();
    ev.event_type = "CONFIG";
    ev.call_type  = "UNARY";
    std::ostringstream ss;
    ss << "pdu_id=0x" << std::hex << request->pdu_id();
    ev.summary = ss.str();
    ctx_.audit_log.Push(std::move(ev));
  }

  response->set_ok(true);
  return grpc::Status::OK;
}

// ── Group handlers ────────────────────────────────────────────────────────────

grpc::Status PduServiceImpl::ConfigureGroup(
    grpc::ServerContext* context,
    const boat::v1::ConfigureGroupRequest* request,
    boat::v1::ConfigureGroupResponse* response) {
  if (!GetRouter()) return grpc::Status(grpc::StatusCode::NOT_FOUND, "PduRouter plugin not loaded");

  const auto& pg = request->group();
  if (pg.group_id() == 0) {
    return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT,
                        "group_id must be non-zero");
  }
  if (pg.pdu_ids_size() == 0) {
    return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT,
                        "group must include at least one pdu_id");
  }

  GetRouter()->AddGroup(ProtoToGroup(pg));

  {
    RpcEvent ev;
    ev.timestamp_ns = NowNsPdu();
    ev.method     = "PduService/ConfigureGroup";
    ev.peer       = context->peer();
    ev.event_type = "CONFIG";
    ev.call_type  = "UNARY";
    std::ostringstream ss;
    ss << "group_id=" << pg.group_id() << "  pdu_ids=[";
    for (int i = 0; i < pg.pdu_ids_size(); ++i) {
      if (i) ss << ',';
      ss << "0x" << std::hex << pg.pdu_ids(i);
    }
    ss << "]";
    ev.summary = ss.str();
    ctx_.audit_log.Push(std::move(ev));
  }

  response->set_ok(true);
  return grpc::Status::OK;
}

grpc::Status PduServiceImpl::EnableGroup(
    grpc::ServerContext* context,
    const boat::v1::EnableGroupRequest* request,
    boat::v1::EnableGroupResponse* response) {
  if (!GetRouter()) return grpc::Status(grpc::StatusCode::NOT_FOUND, "PduRouter plugin not loaded");

  GetRouter()->EnableGroup(request->group_id());

  {
    RpcEvent ev;
    ev.timestamp_ns = NowNsPdu();
    ev.method     = "PduService/EnableGroup";
    ev.peer       = context->peer();
    ev.event_type = "CONFIG";
    ev.call_type  = "UNARY";
    std::ostringstream ss;
    ss << "group_id=" << request->group_id();
    ev.summary = ss.str();
    ctx_.audit_log.Push(std::move(ev));
  }

  response->set_ok(true);
  return grpc::Status::OK;
}

grpc::Status PduServiceImpl::DisableGroup(
    grpc::ServerContext* context,
    const boat::v1::DisableGroupRequest* request,
    boat::v1::DisableGroupResponse* response) {
  if (!GetRouter()) return grpc::Status(grpc::StatusCode::NOT_FOUND, "PduRouter plugin not loaded");

  GetRouter()->DisableGroup(request->group_id());

  {
    RpcEvent ev;
    ev.timestamp_ns = NowNsPdu();
    ev.method     = "PduService/DisableGroup";
    ev.peer       = context->peer();
    ev.event_type = "CONFIG";
    ev.call_type  = "UNARY";
    std::ostringstream ss;
    ss << "group_id=" << request->group_id();
    ev.summary = ss.str();
    ctx_.audit_log.Push(std::move(ev));
  }

  response->set_ok(true);
  return grpc::Status::OK;
}

grpc::Status PduServiceImpl::ListGroups(
    grpc::ServerContext*,
    const boat::v1::ListGroupsRequest*,
    boat::v1::ListGroupsResponse* response) {
  if (!GetRouter()) return grpc::Status(grpc::StatusCode::NOT_FOUND, "PduRouter plugin not loaded");

  for (const auto& g : GetRouter()->ListGroups()) {
    GroupToProto(g, response->add_groups());
  }
  return grpc::Status::OK;
}

}  // namespace boat::gateway
