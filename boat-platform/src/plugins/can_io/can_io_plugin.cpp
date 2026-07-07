#include "can_io_plugin.h"

#include <cstdio>
#include <cstring>
#include <ctime>

#include <linux/can.h>
#include <linux/can/raw.h>
#include <net/if.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>

namespace {

/* ── Helper: open a CAN socket bound to iface ──────────────────────────── */
int OpenCanSocket(const std::string& iface) {
  const int fd = socket(AF_CAN, SOCK_RAW, CAN_RAW);
  if (fd < 0) return -1;

  const unsigned int idx = if_nametoindex(iface.c_str());
  if (idx == 0) { close(fd); return -1; }

  const int enable_fd = 1;
  (void)setsockopt(fd, SOL_CAN_RAW, CAN_RAW_FD_FRAMES, &enable_fd, sizeof(enable_fd));

  // 100ms recv timeout so the RX thread can check running_ periodically.
  struct timeval tv{};
  tv.tv_usec = 100000;
  setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));

  struct sockaddr_can addr{};
  addr.can_family = AF_CAN;
  addr.can_ifindex = static_cast<int>(idx);
  if (bind(fd, reinterpret_cast<struct sockaddr*>(&addr), sizeof(addr)) < 0) {
    close(fd);
    return -1;
  }
  return fd;
}

/* ── Helper: parse JSON config {"ifaces":["vcan0","can1"]} ─────────────── */
std::vector<std::string> ParseIfaceConfig(const char* json) {
  std::vector<std::string> result;
  if (json == nullptr) return result;

  // Find "[...]" and extract comma-separated quoted strings.
  const char* bracket = std::strchr(json, '[');
  if (bracket == nullptr) return result;

  const char* end = std::strchr(bracket, ']');
  if (end == nullptr) return result;

  std::string content(bracket + 1, end - bracket - 1);
  std::size_t pos = 0;
  while (true) {
    // Find opening quote
    auto q1 = content.find('"', pos);
    if (q1 == std::string::npos) break;
    auto q2 = content.find('"', q1 + 1);
    if (q2 == std::string::npos) break;
    result.push_back(content.substr(q1 + 1, q2 - q1 - 1));
    pos = q2 + 1;
  }
  return result;
}

/* ── RX thread function ───────────────────────────────────────────────── */
void RxThreadLoop(CanIoPlugin::Iface* state,
                  CanIoPlugin* plugin) {
  while (state->running.load()) {
    struct canfd_frame raw{};
    const ssize_t bytes = read(state->socket_fd, &raw, sizeof(raw));
    if (bytes < 0) continue;  // timeout

    if (bytes != static_cast<ssize_t>(sizeof(struct can_frame)) &&
        bytes != static_cast<ssize_t>(sizeof(struct canfd_frame))) {
      continue;
    }

    const bool is_fd = (bytes == static_cast<ssize_t>(sizeof(struct canfd_frame)));
    const std::uint8_t dlc = raw.len <= CANFD_MAX_DLEN ? raw.len : CANFD_MAX_DLEN;

    CanIoPlugin::RxFrame frame{};
    frame.can_id = raw.can_id;
    frame.dlc    = dlc;
    frame.flags  = is_fd ? raw.flags : 0;
    frame.is_fd  = is_fd;

    struct timespec ts{};
    clock_gettime(CLOCK_REALTIME, &ts);
    frame.timestamp_ns = static_cast<std::uint64_t>(ts.tv_sec) * 1'000'000'000ULL + ts.tv_nsec;

    if (dlc > 0) std::memcpy(frame.data, raw.data, dlc);

    {
      std::lock_guard<std::mutex> lock(plugin->rx_mutex);
      plugin->rx_queue.push_back(frame);
    }
    plugin->rx_cv.notify_one();
  }
}

/* ── Plugin vtable callbacks ──────────────────────────────────────────── */

int canio_initialize(void* ctx, const char* config_json) {
  auto* plugin = static_cast<CanIoPlugin*>(ctx);
  if (plugin == nullptr) return -1;

  auto ifaces = ParseIfaceConfig(config_json);
  if (ifaces.empty()) {
    std::fprintf(stderr, "[CanIo] no ifaces specified in config: %s\n",
                 config_json ? config_json : "(null)");
    return -1;
  }

  for (const auto& name : ifaces) {
    int fd = OpenCanSocket(name);
    if (fd < 0) {
      std::fprintf(stderr, "[CanIo] failed to open CAN interface '%s'\n",
                   name.c_str());
      continue;
    }
    auto iface = std::make_unique<CanIoPlugin::Iface>();
    iface->iface     = name;
    iface->socket_fd = fd;
    iface->running.store(true);
    iface->rx_thread = std::thread(RxThreadLoop, iface.get(), plugin);
    plugin->ifaces.push_back(std::move(iface));
    std::fprintf(stderr, "[CanIo] opened '%s' (fd=%d)\n", name.c_str(), fd);
  }

  if (plugin->ifaces.empty()) {
    std::fprintf(stderr, "[CanIo] no interfaces could be opened\n");
    return -1;
  }
  return 0;
}

void canio_on_tick(void* ctx, std::uint64_t /*tick*/) {
  auto* plugin = static_cast<CanIoPlugin*>(ctx);
  if (plugin == nullptr || plugin->frame_publish_fn == nullptr) return;

  // Drain the rx queue — batch publish all pending frames.
  std::vector<CanIoPlugin::RxFrame> batch;
  {
    std::lock_guard<std::mutex> lock(plugin->rx_mutex);
    batch.reserve(plugin->rx_queue.size());
    while (!plugin->rx_queue.empty()) {
      batch.push_back(std::move(plugin->rx_queue.front()));
      plugin->rx_queue.pop_front();
    }
  }

  for (const auto& rx : batch) {
    // Build payload from raw data.
    std::vector<std::uint8_t> payload(rx.data, rx.data + rx.dlc);

    // Tag with SELF_SENT so FrameForwarderPlugin skips re-sending to HW.
    std::uint8_t flags = rx.flags | BOAT_CAN_FLAG_SELF_SENT;

    auto owner = BoatFrameOwner::Can(
        "",      // iface — leave empty; registry's internal dispatch fills it
        rx.can_id, rx.dlc, flags, std::move(payload), rx.is_fd);

    plugin->frame_publish_fn(plugin->frame_publisher_ctx, owner.get());
  }
}

void canio_on_frame(void* ctx, const BoatFrame* frame) {
  auto* plugin = static_cast<CanIoPlugin*>(ctx);
  if (plugin == nullptr || frame == nullptr) return;
  if (frame->bus_type != BOAT_BUS_CAN && frame->bus_type != BOAT_BUS_CANFD) return;

  // Determine target interface from frame->iface.
  // If empty, send to ALL managed interfaces (broadcast).
  const std::string target = (frame->iface && frame->iface[0]) ? frame->iface : "";

  for (const auto& iface : plugin->ifaces) {
    if (!target.empty() && iface->iface != target) continue;

    const bool is_fd = (frame->bus_type == BOAT_BUS_CANFD);
    const std::uint32_t ext_flag = (frame->meta.can.can_id > 0x7FF) ? CAN_EFF_FLAG : 0U;

    if (is_fd) {
      struct canfd_frame raw{};
      raw.can_id = frame->meta.can.can_id | ext_flag;
      raw.len    = frame->meta.can.dlc <= CANFD_MAX_DLEN ? frame->meta.can.dlc : CANFD_MAX_DLEN;
      raw.flags  = frame->meta.can.flags;
      if (frame->payload && raw.len > 0)
        std::memcpy(raw.data, frame->payload, raw.len);
      write(iface->socket_fd, &raw, sizeof(raw));
    } else {
      struct can_frame raw{};
      raw.can_id  = frame->meta.can.can_id | ext_flag;
      raw.can_dlc = frame->meta.can.dlc <= CAN_MAX_DLEN ? frame->meta.can.dlc : CAN_MAX_DLEN;
      if (frame->payload && raw.can_dlc > 0)
        std::memcpy(raw.data, frame->payload, raw.can_dlc);
      write(iface->socket_fd, &raw, sizeof(raw));
    }

    if (!target.empty()) break;  // exact match → single interface
  }
}

void canio_shutdown(void* ctx) {
  auto* plugin = static_cast<CanIoPlugin*>(ctx);
  if (plugin == nullptr) return;

  for (auto& iface : plugin->ifaces) {
    iface->running.store(false);
    if (iface->rx_thread.joinable()) iface->rx_thread.join();
    if (iface->socket_fd >= 0) close(iface->socket_fd);
  }
  plugin->ifaces.clear();
}

void canio_set_frame_publisher(void* ctx, BoatFramePublishFn fn, void* pub_ctx) {
  auto* plugin = static_cast<CanIoPlugin*>(ctx);
  if (plugin == nullptr) return;
  plugin->frame_publish_fn    = fn;
  plugin->frame_publisher_ctx = pub_ctx;
}

const char* canio_declared_buses(void* /*ctx*/) {
  return "[\"can\",\"canfd\"]";
}

}  // namespace

extern "C" BoatPlugin* boat_plugin_create() {
  static BoatPluginVTable kVTable = [] {
    BoatPluginVTable vt{};
    vt.initialize           = &canio_initialize;
    vt.on_tick              = &canio_on_tick;
    vt.shutdown             = &canio_shutdown;
    vt.set_publisher        = nullptr;
    vt.set_bus_publisher    = nullptr;
    vt.set_pdu_publisher    = nullptr;
    vt.on_frame             = &canio_on_frame;
    vt.set_frame_publisher  = &canio_set_frame_publisher;
    vt.declared_buses       = &canio_declared_buses;
    return vt;
  }();

  auto* state  = new CanIoPlugin{};
  auto* plugin = new BoatPlugin{};
  plugin->vtable = &kVTable;
  plugin->ctx    = state;
  return plugin;
}

extern "C" void boat_plugin_destroy(BoatPlugin* plugin) {
  if (plugin == nullptr) return;
  delete static_cast<CanIoPlugin*>(plugin->ctx);
  delete plugin;
}

extern "C" uint32_t boat_plugin_abi_version() { return BOAT_PLUGIN_ABI_VERSION; }
