#pragma once

#include <atomic>
#include <condition_variable>
#include <deque>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include <boat/plugin.h>

/*
 * Lightweight CAN I/O plugin — direct SocketCAN bridge.
 *
 * Config (JSON in BOAT_NODE_PLUGINS):
 *   {"ifaces":["vcan0","can1"]}
 *
 * OUTPUT path (on_frame):
 *   BoatFrame → can_frame / canfd_frame → write(socket) → hardware
 *
 * INPUT path (RX thread → queue → on_tick drain):
 *   canfd_frame ← read(socket) → RxFrame → queue
 *     → on_tick pops queue → BoatFrameOwner (with BOAT_CAN_FLAG_SELF_SENT)
 *     → frame_publish_fn → SetFramePublisher → registry
 *       → SELF_SENT flag prevents FrameForwarderPlugin from re-sending
 */

struct CanIoPlugin {
  /* Per-interface socket + RX thread. */
  struct Iface {
    std::string            iface;
    int                    socket_fd{-1};
    std::thread            rx_thread;
    std::atomic<bool>      running{false};
  };

  /* Received frame queued from RX thread to on_tick. */
  struct RxFrame {
    std::uint32_t can_id;
    std::uint8_t  dlc;
    std::uint8_t  flags;
    bool          is_fd;
    std::uint64_t timestamp_ns;
    std::uint8_t  data[64];
  };

  std::vector<std::unique_ptr<Iface>> ifaces;

  /* Thread-safe RX queue — filled by Iface RX threads, drained in on_tick. */
  std::deque<RxFrame>   rx_queue;
  std::mutex            rx_mutex;
  std::condition_variable rx_cv;

  BoatFramePublishFn frame_publish_fn{nullptr};
  void*              frame_publisher_ctx{nullptr};
};

extern "C" BoatPlugin* boat_plugin_create();
extern "C" void       boat_plugin_destroy(BoatPlugin* plugin);
extern "C" uint32_t   boat_plugin_abi_version();
