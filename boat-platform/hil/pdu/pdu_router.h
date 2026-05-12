#pragma once

#include <cstddef>
#include <functional>
#include <mutex>
#include <unordered_map>
#include <vector>

#include "pdu/pdu_types.h"
#include "can_bus_registry.h"
#include "ethernet_bus_registry.h"

namespace boat::hil {

/* Routes AUTOSAR-style PDUs over CAN and Ethernet transports.
 *
 * Routes are keyed by PDU ID.  Each route specifies which transport and
 * interface to use when sending, and how incoming frames on that interface
 * map back to PDU IDs.
 *
 * Ethernet PDU framing (custom, SOME/IP-inspired):
 *   Ethertype = route.ethertype (default 0x88B5)
 *   Payload   = [4 bytes PDU ID big-endian] + actual PDU payload
 *
 * CAN PDU framing:
 *   CAN ID = route.can_id if non-zero, else route.pdu_id
 *   CAN data = PDU payload (up to 8 bytes classic / 64 bytes FD)
 */
class PduRouter {
 public:
  PduRouter(CanBusRegistry& can, EthernetBusRegistry& eth);
  ~PduRouter();

  // Add or replace a routing rule.
  void AddRoute(const PduRoute& route);

  // Send a PDU; looks up the route and forwards to the appropriate registry.
  // Returns false if no route is configured for pdu_id or the send fails.
  bool SendPdu(uint32_t pdu_id, const std::vector<uint8_t>& payload);

  using RxCallback = std::function<void(const PduFrame&)>;
  using SubId = std::size_t;

  // pdu_ids: empty vector = subscribe to all PDUs.
  SubId Subscribe(std::vector<uint32_t> pdu_ids, RxCallback cb);
  void  Unsubscribe(SubId id);

  std::vector<PduRoute> ListRoutes() const;

  void Stop();

 private:
  void OnCanFrame(const CanFrame& frame, const std::string& iface);
  void OnEthernetFrame(const EthernetFrame& frame, const std::string& iface);
  void DispatchPdu(const PduFrame& pdu);

  CanBusRegistry&      can_;
  EthernetBusRegistry& eth_;

  mutable std::mutex routes_mutex_;
  std::unordered_map<uint32_t, PduRoute> routes_;  // keyed by pdu_id

  std::mutex subs_mutex_;
  struct Subscription {
    std::vector<uint32_t> pdu_ids;  // empty = all
    RxCallback cb;
  };
  std::unordered_map<SubId, Subscription> subscriptions_;
  SubId next_sub_id_{0};

  CanBusRegistry::RxCallbackId      can_sub_id_{0};
  EthernetBusRegistry::RxCallbackId eth_sub_id_{0};
  bool                               subscribed_{false};
};

}  // namespace boat::hil
