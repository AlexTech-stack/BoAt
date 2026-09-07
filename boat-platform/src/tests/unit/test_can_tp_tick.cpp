// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

/* CanTp used to pace CFs and run its N_Bs/N_Cr watchdogs on a private thread
   against steady_clock, so it was invisible to the tick authority and every
   ISO 15765-2 timeout depended on host scheduling. It is now driven from
   on_tick and reads time through the v9 host clock.
 
   That is what makes these tests possible at all: a watchdog test used to mean
   sleeping for the real N_Bs (1000 ms by default). Here the clock is a variable
   and the whole suite runs in microseconds. */

#include <catch2/catch_test_macros.hpp>

#include <atomic>
#include <cstring>
#include <mutex>
#include <string>
#include <vector>

#include <boat/can_tp.h>

#include "core/can_tp_interface.h"
#include "core/plugin/plugin_manager.h"

namespace {

constexpr uint32_t kNsdu   = 0x700;
constexpr uint32_t kSource = 0x7E0;   // our CAN ID
constexpr uint32_t kTarget = 0x7E8;   // peer CAN ID

struct Sent {
  uint32_t can_id;
  std::vector<uint8_t> data;
};

/* Loads can_tp.so with a clock the test owns, and captures what it transmits. */
struct Harness {
  boat::core::PluginManager pm;
  std::atomic<std::uint64_t> now_ns{0};
  std::mutex mu;
  std::vector<Sent> sent;
  boat::core::ICanTp* tp{nullptr};

  Harness() {
    pm.SetTimeSource([this] { return now_ns.load(); });
    pm.SetFramePublisher([this](const BoatFrame& f) {
      if (f.bus_type != BOAT_BUS_CAN && f.bus_type != BOAT_BUS_CANFD) return;
      std::lock_guard<std::mutex> lock(mu);
      sent.push_back({f.meta.can.can_id,
                      std::vector<uint8_t>(f.payload, f.payload + f.payload_len)});
    });
    pm.Load(CAN_TP_SO, R"({"iface":"vcan0"})");
    tp = static_cast<boat::core::ICanTp*>(pm.FindService("can_tp:vcan0"));
  }

  ~Harness() { pm.ShutdownAll(); }

  void AdvanceMs(std::uint64_t ms) { now_ns.fetch_add(ms * 1'000'000ULL); }
  void Tick() { pm.TickAll(1); }

  std::vector<Sent> Drain() {
    std::lock_guard<std::mutex> lock(mu);
    auto out = sent;
    sent.clear();
    return out;
  }

  /* Deliver one CAN frame from the peer. */
  void Deliver(std::vector<uint8_t> payload) {
    BoatFrame f{};
    f.bus_type = BOAT_BUS_CAN;
    f.iface = "vcan0";
    f.meta.can.can_id = kTarget;
    f.meta.can.dlc = static_cast<uint8_t>(payload.size());
    f.payload = payload.data();
    f.payload_len = static_cast<uint32_t>(payload.size());
    pm.DispatchFrame(f);
  }

  static CanTpConfig Config() {
    CanTpConfig c{};
    c.nsdu_id     = kNsdu;
    c.source_addr = kSource;
    c.target_addr = kTarget;
    c.can_dlc     = 8;
    c.n_bs_ms     = 100;
    c.n_cr_ms     = 150;
    return c;
  }
};

}  // namespace

TEST_CASE("CanTp sends a single frame without needing a tick",
          "[unit][can_tp][tick]") {
  Harness h;
  REQUIRE(h.tp != nullptr);
  REQUIRE(h.tp->Configure(Harness::Config()) == 0);

  const std::vector<uint8_t> payload{0x01, 0x02, 0x03};
  REQUIRE(h.tp->Send(kNsdu, payload.data(), payload.size()) == 1);

  const auto out = h.Drain();
  REQUIRE(out.size() == 1);
  REQUIRE(out[0].can_id == kSource);
  REQUIRE((out[0].data[0] & 0xF0) == 0x00);   // Single Frame
  REQUIRE(out[0].data[1] == 0x01);
}

TEST_CASE("CanTp N_Bs fires on the host clock, with no real waiting",
          "[unit][can_tp][tick][determinism]") {
  Harness h;
  REQUIRE(h.tp->Configure(Harness::Config()) == 0);

  std::vector<boat::core::CanTpErrorEvent> errors;
  h.tp->SubscribeErrors({}, [&errors](const boat::core::CanTpErrorEvent& e) {
    errors.push_back(e);
  });

  // 20 bytes needs a First Frame, then waits for Flow Control.
  const std::vector<uint8_t> payload(20, 0xAB);
  REQUIRE(h.tp->Send(kNsdu, payload.data(), payload.size()) == 0);

  auto out = h.Drain();
  REQUIRE(out.size() == 1);
  REQUIRE((out[0].data[0] & 0xF0) == 0x10);   // First Frame

  // Ticking without advancing the clock must not trip the watchdog.
  for (int i = 0; i < 50; ++i) h.Tick();
  REQUIRE(errors.empty());

  // Step past N_Bs (100 ms) in one go. No sleeping: the clock is a variable.
  h.AdvanceMs(101);
  h.Tick();

  REQUIRE(errors.size() == 1);
  REQUIRE(errors[0].nsdu_id == kNsdu);
  REQUIRE(errors[0].result == CANTP_N_TIMEOUT_BS);
  // The connection is released, so a fresh send is accepted.
  REQUIRE(h.tp->Send(kNsdu, payload.data(), payload.size()) == 0);
}

TEST_CASE("CanTp paces consecutive frames by STmin on the host clock",
          "[unit][can_tp][tick]") {
  Harness h;
  REQUIRE(h.tp->Configure(Harness::Config()) == 0);

  const std::vector<uint8_t> payload(20, 0xCD);
  REQUIRE(h.tp->Send(kNsdu, payload.data(), payload.size()) == 0);
  REQUIRE(h.Drain().size() == 1);                    // First Frame

  // Flow Control: continue, block size unlimited, STmin = 20 ms.
  h.Deliver({0x30, 0x00, 20});

  // 20 bytes is 6 in the First Frame plus two 7-byte Consecutive Frames.
  // The first CF goes out as soon as it is serviced.
  h.Tick();
  auto first_cf = h.Drain();
  REQUIRE(first_cf.size() == 1);
  REQUIRE((first_cf[0].data[0] & 0xF0) == 0x20);     // Consecutive Frame

  // Ticking alone does not release the next one -- only the clock does.
  for (int i = 0; i < 20; ++i) h.Tick();
  REQUIRE(h.Drain().empty());

  h.AdvanceMs(20);
  h.Tick();
  auto second_cf = h.Drain();
  REQUIRE(second_cf.size() == 1);
  REQUIRE((second_cf[0].data[0] & 0xF0) == 0x20);

  // That completes the transfer: no further frames however much time passes.
  h.AdvanceMs(500);
  for (int i = 0; i < 10; ++i) h.Tick();
  REQUIRE(h.Drain().empty());
}

TEST_CASE("CanTp streams unlimited-rate transfers within one tick",
          "[unit][can_tp][tick]") {
  // STmin = 0 means the peer asked for no separation at all. The old TX thread
  // streamed back-to-back; on_tick drains rather than pacing at the tick rate.
  Harness h;
  REQUIRE(h.tp->Configure(Harness::Config()) == 0);

  const std::vector<uint8_t> payload(60, 0xEE);      // 9 CFs after the FF
  REQUIRE(h.tp->Send(kNsdu, payload.data(), payload.size()) == 0);
  REQUIRE(h.Drain().size() == 1);

  h.Deliver({0x30, 0x00, 0x00});                     // continue, BS=0, STmin=0
  h.Tick();

  const auto out = h.Drain();
  REQUIRE(out.size() >= 8);
  for (const auto& f : out) REQUIRE((f.data[0] & 0xF0) == 0x20);
}
