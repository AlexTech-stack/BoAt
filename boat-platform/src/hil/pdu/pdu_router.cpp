#include "pdu/pdu_router.h"

#include <chrono>
#include <cstring>

#include "pdu/ipdumcontainer.h"

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

// ── Container management ──────────────────────────────────────────────────────

void PduRouter::AddContainer(const PduContainerDef& def) {
  std::lock_guard<std::mutex> lock(containers_mutex_);
  // Remove old pdu_id→container mappings for this container_id if it existed.
  if (containers_.count(def.container_id)) {
    for (const auto& slot : containers_.at(def.container_id).slots) {
      pdu_to_container_.erase(slot.pdu_id);
    }
  }
  ContainerBuffer buf;
  buf.def = def;
  for (const uint32_t pid : def.pdu_ids) {
    buf.slots.push_back({pid, {}});
    pdu_to_container_[pid] = def.container_id;
  }
  containers_[def.container_id] = std::move(buf);
}

bool PduRouter::SendContainer(const PduContainerDef& def,
                               const std::vector<IpduMEntry>& entries) {
  if (entries.empty()) return false;
  const auto container = IpduMSerialize(entries);

  EthernetFrame frame;
  frame.vlan_id = def.vlan_id;

  if (def.dst_ip.size() == 4) {
    frame.ethertype = 0x0800;
    frame.payload   = BuildUdpIpv4(def.src_ip.data(), def.dst_ip.data(),
                                    def.src_port, def.dst_port,
                                    def.ttl, container);
  } else if (def.dst_ip.size() == 16) {
    frame.ethertype = 0x86DD;
    frame.payload   = BuildUdpIpv6(def.src_ip.data(), def.dst_ip.data(),
                                    def.src_port, def.dst_port,
                                    def.ttl, container);
  } else {
    return false;
  }
  frame.src_ip = def.src_ip;
  frame.dst_ip = def.dst_ip;
  return eth_.SendFrame(def.iface, frame);
}

// ── Send ──────────────────────────────────────────────────────────────────────

bool PduRouter::SendPdu(uint32_t pdu_id, const std::vector<uint8_t>& payload) {
  // Container path: multiplex with sibling PDUs into one Ethernet frame.
  {
    std::unique_lock<std::mutex> lock(containers_mutex_);
    const auto cit = pdu_to_container_.find(pdu_id);
    if (cit != pdu_to_container_.end()) {
      ContainerBuffer& buf = containers_.at(cit->second);
      for (auto& slot : buf.slots) {
        if (slot.pdu_id == pdu_id) { slot.payload = payload; break; }
      }
      std::vector<IpduMEntry> entries;
      for (const auto& slot : buf.slots) {
        if (!slot.payload.empty())
          entries.push_back({slot.pdu_id, slot.payload});
      }
      const PduContainerDef def = buf.def;
      lock.unlock();
      return SendContainer(def, entries);
    }
  }

  // Per-PDU route path (original behaviour).
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
    EthernetFrame frame;
    frame.vlan_id = route.vlan_id;

    if (!route.dst_ip.empty()) {
      // IP/UDP/IpduM path
      const auto container = IpduMSerialize({{pdu_id, payload}});
      if (route.dst_ip.size() == 4) {
        frame.ethertype = 0x0800;
        frame.payload   = BuildUdpIpv4(route.src_ip.data(), route.dst_ip.data(),
                                        route.src_port, route.dst_port,
                                        route.ttl, container);
      } else if (route.dst_ip.size() == 16) {
        frame.ethertype = 0x86DD;
        frame.payload   = BuildUdpIpv6(route.src_ip.data(), route.dst_ip.data(),
                                        route.src_port, route.dst_port,
                                        route.ttl, container);
      } else {
        return false;
      }
      frame.src_ip = route.src_ip;
      frame.dst_ip = route.dst_ip;
    } else {
      // Simulation-only path: [4-byte PDU ID big-endian] + payload
      frame.ethertype = route.ethertype;
      frame.payload.resize(4 + payload.size());
      frame.payload[0] = static_cast<uint8_t>(pdu_id >> 24);
      frame.payload[1] = static_cast<uint8_t>(pdu_id >> 16);
      frame.payload[2] = static_cast<uint8_t>(pdu_id >>  8);
      frame.payload[3] = static_cast<uint8_t>(pdu_id & 0xFF);
      std::memcpy(frame.payload.data() + 4, payload.data(), payload.size());
    }
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
  if (frame.ethertype == 0x0800 || frame.ethertype == 0x86DD) {
    // IP/UDP/IpduM path
    uint16_t src_port = 0, dst_port = 0;
    std::vector<IpduMEntry> entries;
    if (!ParseUdpIpPacket(frame.payload.data(), frame.payload.size(),
                          &src_port, &dst_port, entries)) return;

    for (const auto& entry : entries) {
      bool matched = false;
      {
        std::lock_guard<std::mutex> lock(routes_mutex_);
        const auto it = routes_.find(entry.pdu_id);
        if (it != routes_.end()) {
          const PduRoute& r = it->second;
          if (r.transport == PduTransport::kEthernet &&
              (r.iface.empty()     || r.iface    == iface)    &&
              (r.vlan_id == 0      || r.vlan_id  == frame.vlan_id) &&
              (r.dst_port == 0     || r.dst_port == dst_port)) {
            matched = true;
          }
        }
      }
      if (!matched) {
        std::lock_guard<std::mutex> lock(containers_mutex_);
        matched = pdu_to_container_.count(entry.pdu_id) > 0;
      }
      if (!matched) continue;
      PduFrame pdu;
      pdu.pdu_id       = entry.pdu_id;
      pdu.payload      = entry.payload;
      pdu.timestamp_ns = frame.timestamp_ns;
      pdu.source       = PduTransport::kEthernet;
      pdu.iface        = iface;
      DispatchPdu(pdu);
    }
    return;
  }

  // Simulation-only path: [4-byte PDU ID big-endian] + payload
  if (frame.payload.size() < 4) return;

  const uint32_t pdu_id =
      (static_cast<uint32_t>(frame.payload[0]) << 24) |
      (static_cast<uint32_t>(frame.payload[1]) << 16) |
      (static_cast<uint32_t>(frame.payload[2]) <<  8) |
       static_cast<uint32_t>(frame.payload[3]);

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
  pdu.pdu_id       = pdu_id;
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
