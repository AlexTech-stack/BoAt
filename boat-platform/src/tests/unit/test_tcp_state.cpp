// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

/* Drive the TCP plugin's state machine without sockets.
 *
 * The plugin is linked directly into this test (the same way
 * test_tcp_options.cpp does) and every inbound segment is a real Ethernet
 * frame handed to the v9 on_frame callback. A captured frame publisher
 * records what the plugin would put back on the wire, so transitions and
 * emitted headers are both visible without a live peer.
 */

#include <catch2/catch_test_macros.hpp>

#include <array>
#include <cstdint>
#include <cstring>
#include <vector>

#include "boat/frame.h"
#include "boat/plugin.h"
#include "tcp_plugin.h"
#include "tcp_segment.h"

namespace {

using namespace boat::tcp;

struct CapturedSegment {
  uint16_t sport{0};
  uint16_t dport{0};
  uint32_t seq{0};
  uint32_t ack{0};
  uint8_t flags{0};
  std::vector<uint8_t> payload;
};

class TcpHarness {
 public:
  TcpHarness() {
    raw_ = boat_plugin_create();
    if (raw_ == nullptr) return;
    // Set these before initialize: that is exactly the ordering the gateway
    // uses, and it is also what keeps the test off a real AF_PACKET socket.
    raw_->vtable->set_frame_publisher(raw_->ctx, &TcpHarness::Publish, this);
    raw_->vtable->set_time_source(raw_->ctx, &TcpHarness::Now, this);
    raw_->vtable->initialize(raw_->ctx, R"({"keepalive_idle_ms":0})");
    plugin_ = static_cast<TcpPlugin*>(raw_->ctx);
  }

  ~TcpHarness() {
    if (raw_ != nullptr) boat_plugin_destroy(raw_);
  }

  TcpHarness(const TcpHarness&) = delete;
  TcpHarness& operator=(const TcpHarness&) = delete;

  BoatPlugin* raw() const { return raw_; }
  TcpPlugin* plugin() const { return plugin_; }

  void Listen(uint16_t port) {
    TcpListener listener;
    listener.listener_id = 1;
    listener.af = AF_INET;
    listener.bind_ip = {10, 0, 0, 2};
    listener.bind_port = port;
    listener.on_data = &TcpHarness::OnData;
    listener.on_event = &TcpHarness::OnEvent;
    listener.user_ctx = this;
    plugin_->listeners[listener.listener_id] = listener;
  }

  void Send(const std::vector<uint8_t>& ip_segment) {
    uint8_t dst[6] = {0x02, 0, 0, 0, 0, 1};
    uint8_t src[6] = {0x02, 0, 0, 0, 0, 2};
    auto frame = BoatFrameOwner::Ethernet("eth0", dst, src, 0x0800, 0,
                                          ip_segment);
    raw_->vtable->on_frame(raw_->ctx, frame.get());
  }

  void Tick() {
    raw_->vtable->on_tick(raw_->ctx, 0);
  }

  static uint64_t Now(void* ctx) {
    return static_cast<TcpHarness*>(ctx)->now_ns_;
  }

  std::vector<CapturedSegment> sent;
  std::vector<uint8_t> received;
  int connected{0};
  int closed{0};
  int rst{0};
  int errors{0};
  uint64_t now_ns_{1'000'000'000};

 private:
  static void OnData(void* ctx, int, const uint8_t* data, uint32_t len) {
    auto* self = static_cast<TcpHarness*>(ctx);
    self->received.insert(self->received.end(), data, data + len);
  }

  static void OnEvent(void* ctx, int, int event) {
    auto* self = static_cast<TcpHarness*>(ctx);
    if (event == TCP_EVENT_CONNECTED) ++self->connected;
    if (event == TCP_EVENT_CLOSED) ++self->closed;
    if (event == TCP_EVENT_RST) ++self->rst;
    if (event == TCP_EVENT_ERROR) ++self->errors;
  }

  static void Publish(void* ctx, const BoatFrame* frame) {
    auto* self = static_cast<TcpHarness*>(ctx);
    if (frame == nullptr || frame->payload_len < 40) return;
    const uint8_t* ip = frame->payload;
    const uint8_t ip_hdr = static_cast<uint8_t>((ip[0] & 0x0F) * 4);
    if (frame->payload_len < ip_hdr + 20) return;
    const uint8_t* tcp = ip + ip_hdr;
    const uint8_t tcp_hdr = static_cast<uint8_t>((tcp[12] >> 4) * 4);
    if (frame->payload_len < ip_hdr + tcp_hdr) return;

    CapturedSegment seg;
    seg.sport = static_cast<uint16_t>((tcp[0] << 8) | tcp[1]);
    seg.dport = static_cast<uint16_t>((tcp[2] << 8) | tcp[3]);
    seg.seq = (static_cast<uint32_t>(tcp[4]) << 24) |
              (static_cast<uint32_t>(tcp[5]) << 16) |
              (static_cast<uint32_t>(tcp[6]) << 8) |
              static_cast<uint32_t>(tcp[7]);
    seg.ack = (static_cast<uint32_t>(tcp[8]) << 24) |
              (static_cast<uint32_t>(tcp[9]) << 16) |
              (static_cast<uint32_t>(tcp[10]) << 8) |
              static_cast<uint32_t>(tcp[11]);
    seg.flags = tcp[13];
    const std::size_t tcp_len = frame->payload_len - ip_hdr;
    if (tcp_len > tcp_hdr) {
      seg.payload.assign(tcp + tcp_hdr, tcp + tcp_len);
    }
    self->sent.push_back(std::move(seg));
  }

  BoatPlugin* raw_{nullptr};
  TcpPlugin* plugin_{nullptr};
};

std::vector<uint8_t> Segment(uint32_t seq, uint32_t ack, uint16_t flags,
                             const std::vector<uint8_t>& data = {},
                             uint16_t window = 65535,
                             uint16_t sport = 1234) {
  const std::array<uint8_t, 4> src{10, 0, 0, 1};
  const std::array<uint8_t, 4> dst{10, 0, 0, 2};
  return BuildIp4TcpSegment(src.data(), dst.data(), sport, 8080, seq, ack,
                            data.empty() ? nullptr : data.data(),
                            static_cast<uint32_t>(data.size()), flags,
                            window);
}

constexpr uint32_t kClientSeq = 1000;

TEST_CASE("TCP passive open reaches ESTABLISHED",
          "[unit][tcp][state][handshake]") {
  TcpHarness h;
  REQUIRE(h.raw() != nullptr);
  h.Listen(8080);

  h.Send(Segment(kClientSeq, 0, TCP_FLAG_SYN));
  REQUIRE(h.sent.size() == 1);
  REQUIRE(h.plugin()->connections.size() == 1);
  const auto& syn_ack = h.sent[0];
  REQUIRE((syn_ack.flags & (TCP_FLAG_SYN | TCP_FLAG_ACK)) ==
          (TCP_FLAG_SYN | TCP_FLAG_ACK));
  REQUIRE(syn_ack.ack == kClientSeq + 1);

  const auto& conn = h.plugin()->connections.begin()->second;
  REQUIRE(conn.state == TCP_SYN_RCVD);
  const uint32_t server_next = conn.my_seq;

  h.Send(Segment(kClientSeq + 1, server_next, TCP_FLAG_ACK));
  REQUIRE(conn.state == TCP_ESTABLISHED);
  REQUIRE(h.connected == 1);
}

TEST_CASE("TCP drops a segment with a bad checksum",
          "[unit][tcp][state][checksum]") {
  TcpHarness h;
  REQUIRE(h.raw() != nullptr);
  h.Listen(8080);

  auto syn = Segment(kClientSeq, 0, TCP_FLAG_SYN);
  syn[36] ^= 0xFF;  // TCP checksum inside the IPv4 header
  h.Send(syn);
  REQUIRE(h.plugin()->connections.empty());
  REQUIRE(h.sent.empty());
}

TEST_CASE("TCP RST during SYN_RCVD returns the half-open connection to LISTEN",
          "[unit][tcp][state][handshake][rst]") {
  TcpHarness h;
  REQUIRE(h.raw() != nullptr);
  h.Listen(8080);
  h.Send(Segment(kClientSeq, 0, TCP_FLAG_SYN));
  REQUIRE(h.plugin()->connections.size() == 1);
  const auto& conn = h.plugin()->connections.begin()->second;

  h.Send(Segment(conn.my_ack, 0, TCP_FLAG_RST));
  REQUIRE(h.plugin()->connections.empty());
  REQUIRE(h.plugin()->listeners.size() == 1);
  REQUIRE(h.rst == 1);
}

TEST_CASE("TCP delivers in-order data and advances rcv.nxt",
          "[unit][tcp][state][data]") {
  TcpHarness h;
  REQUIRE(h.raw() != nullptr);
  h.Listen(8080);
  h.Send(Segment(kClientSeq, 0, TCP_FLAG_SYN));
  const auto& conn = h.plugin()->connections.begin()->second;
  h.Send(Segment(kClientSeq + 1, conn.my_seq, TCP_FLAG_ACK));
  REQUIRE(conn.state == TCP_ESTABLISHED);

  const std::vector<uint8_t> payload{0xDE, 0xAD, 0xBE, 0xEF};
  h.Send(Segment(kClientSeq + 1, conn.my_seq, TCP_FLAG_ACK | TCP_FLAG_PSH,
                 payload));
  REQUIRE(h.received == payload);
  REQUIRE(conn.my_ack == kClientSeq + 1 + payload.size());
  REQUIRE(!h.sent.empty());
}

TEST_CASE("TCP peer FIN moves ESTABLISHED to CLOSE_WAIT",
          "[unit][tcp][state][close]") {
  TcpHarness h;
  REQUIRE(h.raw() != nullptr);
  h.Listen(8080);
  h.Send(Segment(kClientSeq, 0, TCP_FLAG_SYN));
  const auto& conn = h.plugin()->connections.begin()->second;
  h.Send(Segment(kClientSeq + 1, conn.my_seq, TCP_FLAG_ACK));

  h.Send(Segment(kClientSeq + 1, conn.my_seq, TCP_FLAG_FIN | TCP_FLAG_ACK));
  REQUIRE(conn.state == TCP_CLOSE_WAIT);
  REQUIRE(h.closed == 1);
}

TEST_CASE("TCP retransmits an unacked data segment on the retry timer",
          "[unit][tcp][state][retransmit]") {
  TcpHarness h;
  REQUIRE(h.raw() != nullptr);

  TcpConnection conn;
  conn.conn_id = 7;
  conn.af = AF_INET;
  conn.src_ip = {10, 0, 0, 2};
  conn.dst_ip = {10, 0, 0, 1};
  conn.src_port = 8080;
  conn.dst_port = 1234;
  conn.state = TCP_ESTABLISHED;
  conn.my_seq = 100;
  conn.my_ack = kClientSeq + 1;
  conn.peer_window = 65535;
  conn.mss = 1460;
  conn.send_buffer = {1, 2, 3};
  conn.last_activity_ns = h.now_ns_;
  h.plugin()->connections[conn.conn_id] = conn;

  h.Tick();
  REQUIRE(h.sent.size() == 1);
  REQUIRE(h.sent[0].payload == std::vector<uint8_t>({1, 2, 3}));
  const auto& live = h.plugin()->connections[7];
  REQUIRE(!live.unacked_segment.empty());
  const uint64_t retry_at = live.retransmit_at_ns;

  h.now_ns_ = retry_at;
  h.Tick();
  REQUIRE(h.sent.size() == 2);
  REQUIRE(h.sent[1].payload == std::vector<uint8_t>({1, 2, 3}));
  REQUIRE(h.sent[1].seq == h.sent[0].seq);
}

TEST_CASE("TCP samples RTT from an acknowledged data segment",
          "[unit][tcp][state][rtt]") {
  TcpHarness h;
  REQUIRE(h.raw() != nullptr);
  TcpConnection conn;
  conn.conn_id = 8;
  conn.af = AF_INET;
  conn.src_ip = {10, 0, 0, 2};
  conn.dst_ip = {10, 0, 0, 1};
  conn.src_port = 8080;
  conn.dst_port = 1234;
  conn.state = TCP_ESTABLISHED;
  conn.my_seq = 100;
  conn.my_ack = kClientSeq + 1;
  conn.peer_window = 65535;
  conn.mss = 1460;
  conn.send_buffer = {1, 2, 3, 4};
  conn.last_activity_ns = h.now_ns_;
  conn.rtt.Reset(1000);
  h.plugin()->connections[conn.conn_id] = conn;

  h.Tick();
  const auto& live = h.plugin()->connections[8];
  REQUIRE(live.rtt_in_flight);
  h.now_ns_ += 50'000'000;  // 50 ms
  h.Send(Segment(kClientSeq + 1, live.my_seq, TCP_FLAG_ACK));

  REQUIRE_FALSE(live.rtt_in_flight);
  REQUIRE(live.rtt.have_sample);
  REQUIRE(live.rtt.srtt_ms == 50.0);
  REQUIRE(live.rtt.rto_ms == TcpRttEstimator::kMinRtoMs);
}

TEST_CASE("TCP does not take an RTT sample from a retransmitted segment",
          "[unit][tcp][state][rtt][karn]") {
  TcpHarness h;
  REQUIRE(h.raw() != nullptr);
  TcpConnection conn;
  conn.conn_id = 10;
  conn.af = AF_INET;
  conn.src_ip = {10, 0, 0, 2};
  conn.dst_ip = {10, 0, 0, 1};
  conn.src_port = 8080;
  conn.dst_port = 1234;
  conn.state = TCP_ESTABLISHED;
  conn.my_seq = 100;
  conn.my_ack = kClientSeq + 1;
  conn.peer_window = 65535;
  conn.mss = 1460;
  conn.send_buffer = {1, 2, 3};
  conn.last_activity_ns = h.now_ns_;
  conn.rtt.Reset(1000);
  h.plugin()->connections[conn.conn_id] = conn;

  h.Tick();
  const auto& live = h.plugin()->connections[10];
  h.now_ns_ = live.retransmit_at_ns;
  h.Tick();
  REQUIRE(live.rtt_retransmitted);

  h.Send(Segment(kClientSeq + 1, live.my_seq, TCP_FLAG_ACK));
  REQUIRE_FALSE(live.rtt_in_flight);
  REQUIRE_FALSE(live.rtt.have_sample);
}

TEST_CASE("TCP caps the number of connections a SYN flood can create",
          "[unit][tcp][state][resource]") {
  TcpHarness h;
  REQUIRE(h.raw() != nullptr);
  h.Listen(8080);
  h.plugin()->max_connections = 1;

  h.Send(Segment(kClientSeq, 0, TCP_FLAG_SYN));
  REQUIRE(h.plugin()->connections.size() == 1);
  h.Send(Segment(kClientSeq, 0, TCP_FLAG_SYN, {}, 65535, 1235));
  REQUIRE(h.plugin()->connections.size() == 1);
}

TEST_CASE("TCP reaps CLOSED connections on tick",
          "[unit][tcp][state][resource]") {
  TcpHarness h;
  REQUIRE(h.raw() != nullptr);

  TcpConnection conn;
  conn.conn_id = 9;
  conn.state = TCP_CLOSED;
  h.plugin()->connections[conn.conn_id] = conn;

  h.Tick();
  REQUIRE(h.plugin()->connections.empty());
}

TEST_CASE("TCP bounds out-of-order data to the advertised receive window",
          "[unit][tcp][state][resource]") {
  TcpHarness h;
  REQUIRE(h.raw() != nullptr);
  h.Listen(8080);
  h.plugin()->rx_window = 4;
  h.Send(Segment(kClientSeq, 0, TCP_FLAG_SYN));
  const auto& conn = h.plugin()->connections.begin()->second;
  h.Send(Segment(kClientSeq + 1, conn.my_seq, TCP_FLAG_ACK));
  REQUIRE(conn.state == TCP_ESTABLISHED);

  // Starts one byte past rcv.nxt and runs 8 bytes: outside the 4-byte window.
  h.Send(Segment(kClientSeq + 2, conn.my_seq, TCP_FLAG_ACK | TCP_FLAG_PSH,
                 {1, 2, 3, 4, 5, 6, 7, 8}));
  REQUIRE(conn.receive_buffer.empty());
  REQUIRE(conn.receive_buffer_bytes == 0);
}

TEST_CASE("TCP bounds the number of out-of-order buffer entries",
          "[unit][tcp][state][resource]") {
  TcpHarness h;
  REQUIRE(h.raw() != nullptr);
  h.Listen(8080);
  h.plugin()->max_receive_buffer_entries = 1;
  h.Send(Segment(kClientSeq, 0, TCP_FLAG_SYN));
  const auto& conn = h.plugin()->connections.begin()->second;
  h.Send(Segment(kClientSeq + 1, conn.my_seq, TCP_FLAG_ACK));
  h.plugin()->rx_window = 1024;

  h.Send(Segment(kClientSeq + 2, conn.my_seq, TCP_FLAG_ACK | TCP_FLAG_PSH,
                 {1, 2}));
  h.Send(Segment(kClientSeq + 4, conn.my_seq, TCP_FLAG_ACK | TCP_FLAG_PSH,
                 {3, 4}));
  REQUIRE(conn.receive_buffer.size() == 1);
}

}  // namespace
