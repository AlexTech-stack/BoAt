#include "pdu/pdu_router.h"

#include <chrono>
#include <cstring>

namespace boat::hil {

// ── Construction / destruction ────────────────────────────────────────────────

PduRouter::PduRouter(CanBusRegistry& can, EthernetBusRegistry& eth)
    : can_(can), eth_(eth) {
  // Subscribe to all frames on both registries; PDU matching is done internally.
  can_sub_id_ = can_.Subscribe(
      "",
      [this](const CanFrame& frame, const std::string& iface) {
        OnCanFrame(frame, iface);
      });

  eth_sub_id_ = eth_.Subscribe(
      "", 0,
      [this](const EthernetFrame& frame, const std::string& iface) {
        OnEthernetFrame(frame, iface);
      });

  subscribed_ = true;
}

PduRouter::~PduRouter() { Stop(); }

// ── Route management ──────────────────────────────────────────────────────────

void PduRouter::AddRoute(const PduRoute& route) {
  std::lock_guard<std::mutex> lock(routes_mutex_);
  routes_[route.pdu_id] = route;
  // Default ethertype when caller leaves it as 0.
  if (routes_[route.pdu_id].ethertype == 0) {
    routes_[route.pdu_id].ethertype = 0x88B5;
  }
}

std::vector<PduRoute> PduRouter::ListRoutes() const {
  std::lock_guard<std::mutex> lock(routes_mutex_);
  std::vector<PduRoute> out;
  out.reserve(routes_.size());
  for (const auto& [id, r] : routes_) {
    (void)id;
    out.push_back(r);
  }
  return out;
}

// ── Send ──────────────────────────────────────────────────────────────────────

bool PduRouter::SendPdu(uint32_t pdu_id, const std::vector<uint8_t>& payload) {
  PduRoute route;
  {
    std::lock_guard<std::mutex> lock(routes_mutex_);
    const auto it = routes_.find(pdu_id);
    if (it == routes_.end()) return false;
    route = it->second;
  }

  if (route.transport == PduTransport::kCan) {
    const uint32_t can_id = route.can_id != 0 ? route.can_id : pdu_id;
    CanFrame frame{};
    frame.can_id = can_id;
    frame.dlc    = static_cast<uint8_t>(std::min(payload.size(), std::size_t{64}));
    std::memcpy(frame.data, payload.data(), frame.dlc);
    return can_.SendFrame(route.iface, frame);
  }

  if (route.transport == PduTransport::kEthernet) {
    // Framing: [4 bytes PDU ID big-endian] + payload
    EthernetFrame frame;
    frame.ethertype = route.ethertype;
    frame.vlan_id   = route.vlan_id;
    frame.payload.resize(4 + payload.size());
    frame.payload[0] = static_cast<uint8_t>(pdu_id >> 24);
    frame.payload[1] = static_cast<uint8_t>(pdu_id >> 16);
    frame.payload[2] = static_cast<uint8_t>(pdu_id >>  8);
    frame.payload[3] = static_cast<uint8_t>(pdu_id & 0xFF);
    std::memcpy(frame.payload.data() + 4, payload.data(), payload.size());
    return eth_.SendFrame(route.iface, frame);
  }

  return false;
}

// ── Subscribe / Unsubscribe ───────────────────────────────────────────────────

PduRouter::SubId PduRouter::Subscribe(std::vector<uint32_t> pdu_ids,
                                      RxCallback cb) {
  std::lock_guard<std::mutex> lock(subs_mutex_);
  const SubId id = next_sub_id_++;
  subscriptions_[id] = Subscription{std::move(pdu_ids), std::move(cb)};
  return id;
}

void PduRouter::Unsubscribe(SubId id) {
  std::lock_guard<std::mutex> lock(subs_mutex_);
  subscriptions_.erase(id);
}

// ── Stop ──────────────────────────────────────────────────────────────────────

void PduRouter::Stop() {
  if (subscribed_) {
    can_.Unsubscribe(can_sub_id_);
    eth_.Unsubscribe(eth_sub_id_);
    subscribed_ = false;
  }
}

// ── Internal frame handlers ───────────────────────────────────────────────────

void PduRouter::OnCanFrame(const CanFrame& frame, const std::string& iface) {
  // Find a CAN route whose effective CAN ID matches.
  std::vector<PduRoute> matches;
  {
    std::lock_guard<std::mutex> lock(routes_mutex_);
    for (const auto& [id, r] : routes_) {
      (void)id;
      if (r.transport != PduTransport::kCan) continue;
      if (!r.iface.empty() && r.iface != iface) continue;
      const uint32_t eff_can_id = r.can_id != 0 ? r.can_id : r.pdu_id;
      if (eff_can_id == frame.can_id) matches.push_back(r);
    }
  }
  for (const auto& r : matches) {
    PduFrame pdu;
    pdu.pdu_id       = r.pdu_id;
    pdu.payload.assign(frame.data, frame.data + frame.dlc);
    pdu.timestamp_ns = frame.timestamp_ns;
    pdu.source       = PduTransport::kCan;
    pdu.iface        = iface;
    DispatchPdu(pdu);
  }
}

void PduRouter::OnEthernetFrame(const EthernetFrame& frame,
                                const std::string& iface) {
  if (frame.payload.size() < 4) return;

  // Extract PDU ID from first 4 bytes (big-endian framing).
  const uint32_t pdu_id =
      (static_cast<uint32_t>(frame.payload[0]) << 24) |
      (static_cast<uint32_t>(frame.payload[1]) << 16) |
      (static_cast<uint32_t>(frame.payload[2]) <<  8) |
       static_cast<uint32_t>(frame.payload[3]);

  // Verify a route exists for this combination.
  bool matched = false;
  {
    std::lock_guard<std::mutex> lock(routes_mutex_);
    const auto it = routes_.find(pdu_id);
    if (it != routes_.end()) {
      const PduRoute& r = it->second;
      if (r.transport == PduTransport::kEthernet &&
          (r.iface.empty() || r.iface == iface) &&
          r.ethertype == frame.ethertype &&
          r.vlan_id   == frame.vlan_id) {
        matched = true;
      }
    }
  }
  if (!matched) return;

  PduFrame pdu;
  pdu.pdu_id = pdu_id;
  pdu.payload.assign(frame.payload.begin() + 4, frame.payload.end());
  pdu.timestamp_ns = frame.timestamp_ns;
  pdu.source       = PduTransport::kEthernet;
  pdu.iface        = iface;
  DispatchPdu(pdu);
}

void PduRouter::DispatchPdu(const PduFrame& pdu) {
  std::vector<RxCallback> to_call;
  {
    std::lock_guard<std::mutex> lock(subs_mutex_);
    for (const auto& [id, sub] : subscriptions_) {
      (void)id;
      if (sub.pdu_ids.empty()) {
        to_call.push_back(sub.cb);
      } else {
        for (uint32_t fid : sub.pdu_ids) {
          if (fid == pdu.pdu_id) { to_call.push_back(sub.cb); break; }
        }
      }
    }
  }
  for (const auto& cb : to_call) cb(pdu);
}

}  // namespace boat::hil
