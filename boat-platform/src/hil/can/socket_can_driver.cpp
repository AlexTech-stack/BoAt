// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#include "can/socket_can_driver.h"

#ifdef __linux__

#include <cerrno>
#include <climits>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <fstream>
#include <string>
#include <utility>

#include <linux/can.h>
#include <linux/can/raw.h>
#include <linux/can/error.h>
#include <net/if.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <sys/uio.h>
#include <unistd.h>

#include "can/can_health.h"

namespace boat::hil {

namespace {
// ISO 11898-1 CAN FD payload lengths are quantized: above the classic 0-8
// byte range there are only 8 more valid lengths (12/16/20/24/32/48/64),
// encoded on the wire by DLC codes 9-15. CanFrame::dlc elsewhere in this
// codebase is always treated as a literal byte count (see core::Frame::dlc,
// which this is fed from), so a caller sending e.g. a 9-byte FD payload
// would otherwise put a bare `len=9` straight into the kernel canfd_frame --
// not a valid ISO length, so real CAN FD receivers decoding the DLC nibble
// per spec would read 12 bytes (3 of them garbage) instead of the 9 that
// were actually meant. Round up to the next valid length before writing;
// the gap gets zero-padded (padding byte value is implementation-defined
// by the spec, zero is as valid as any other choice here).
std::uint8_t RoundUpToValidCanFdLen(std::uint8_t len) {
  static constexpr std::uint8_t kValidLens[] = {0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64};
  for (std::uint8_t valid : kValidLens) {
    if (len <= valid) {
      return valid;
    }
  }
  return 64;
}
}  // namespace

SocketCanDriver::SocketCanDriver(std::string interface_name) : iface_(std::move(interface_name)) {}

int SocketCanDriver::RequestedRxBufBytes(std::string* warning_out) {
  const char* env = std::getenv("BOAT_CAN_RCVBUF");
  if (env == nullptr || env[0] == '\0') {
    return kDefaultRxBufBytes;
  }
  char* end = nullptr;
  errno = 0;
  const long parsed = std::strtol(env, &end, 10);
  if (end == env || *end != '\0' || errno == ERANGE || parsed < 0 || parsed > INT_MAX) {
    if (warning_out != nullptr) {
      *warning_out = std::string("BOAT_CAN_RCVBUF='") + env +
                     "' is not a valid byte count (0 = kernel default); using " +
                     std::to_string(kDefaultRxBufBytes);
    }
    return kDefaultRxBufBytes;
  }
  return static_cast<int>(parsed);
}

bool SocketCanDriver::Open() {
  Close();

  socket_fd_ = socket(AF_CAN, SOCK_RAW, CAN_RAW);
  if (socket_fd_ < 0) {
    return false;
  }

  const unsigned int if_index = if_nametoindex(iface_.c_str());
  if (if_index == 0) {
    Close();
    return false;
  }

  // Enable CAN FD frames on this socket. Falls back gracefully on classic-only interfaces.
  const int enable_fd = 1;
  (void)setsockopt(socket_fd_, SOL_CAN_RAW, CAN_RAW_FD_FRAMES, &enable_fd, sizeof(enable_fd));

  // Do NOT set CAN_RAW_RECV_OWN_MSGS: the gateway dispatches sent frames directly to
  // subscribers via CanBusRegistry::DispatchRx, so socket loopback is not needed and
  // would cause every sent frame to be double-delivered.

  // Enlarge the receive buffer. The kernel default (~208 KB on a stock Ubuntu)
  // is a few milliseconds of a loaded CAN FD bus, and the failure mode when it
  // overflows is a silent frame drop. BOAT_CAN_RCVBUF tunes it (0 = leave the
  // kernel default). The kernel caps the value at net.core.rmem_max, so read
  // back what was actually applied rather than pretending the request took.
  std::string rcvbuf_warning;
  const int requested_rcvbuf = RequestedRxBufBytes(&rcvbuf_warning);
  if (!rcvbuf_warning.empty()) {
    std::fprintf(stderr, "[can:%s] %s\n", iface_.c_str(), rcvbuf_warning.c_str());
  }
  if (requested_rcvbuf > 0) {
    (void)setsockopt(socket_fd_, SOL_SOCKET, SO_RCVBUF, &requested_rcvbuf,
                     sizeof(requested_rcvbuf));
    int effective = 0;
    socklen_t effective_len = sizeof(effective);
    if (getsockopt(socket_fd_, SOL_SOCKET, SO_RCVBUF, &effective, &effective_len) == 0 &&
        effective < requested_rcvbuf) {
      std::fprintf(stderr,
                   "[can:%s] SO_RCVBUF requested %d bytes but the kernel applied %d "
                   "(raise net.core.rmem_max for the full buffer)\n",
                   iface_.c_str(), requested_rcvbuf, effective);
    }
  }

  // Best-effort per-socket overflow reporting: when the kernel supports it,
  // every received message carries a cumulative drop counter as ancillary
  // data. Not all AF_CAN paths emit it, which is why GetBusHealth also reads
  // the device-level rx_dropped over netlink.
  const int enable_drop_reporting = 1;
  (void)setsockopt(socket_fd_, SOL_SOCKET, SO_RXQ_OVFL, &enable_drop_reporting,
                   sizeof(enable_drop_reporting));

  // Ask for error frames explicitly instead of relying on the default mask, so
  // bus-off and error-passive transitions are observable. ReadFrame()
  // classifies them, counts them and never returns one as data.
  const can_err_mask_t error_mask = CAN_ERR_TX_TIMEOUT | CAN_ERR_LOSTARB | CAN_ERR_CRTL |
                                    CAN_ERR_PROT | CAN_ERR_TRX | CAN_ERR_ACK |
                                    CAN_ERR_BUSOFF | CAN_ERR_BUSERROR | CAN_ERR_RESTARTED;
  (void)setsockopt(socket_fd_, SOL_CAN_RAW, CAN_RAW_ERR_FILTER, &error_mask,
                   sizeof(error_mask));

  struct timeval timeout {};
  timeout.tv_sec = 0;
  timeout.tv_usec = 100000;
  if (setsockopt(socket_fd_, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout)) < 0) {
    Close();
    return false;
  }

  struct sockaddr_can addr {};
  addr.can_family = AF_CAN;
  addr.can_ifindex = static_cast<int>(if_index);
  if (bind(socket_fd_, reinterpret_cast<struct sockaddr*>(&addr), sizeof(addr)) < 0) {
    Close();
    return false;
  }

  rx_dropped_.store(0);
  rx_error_frames_.store(0);
  bus_off_events_.store(0);
  last_drop_counter_.store(0);
  {
    std::lock_guard<std::mutex> lock(health_mutex_);
    last_bus_state_ = "unknown";
  }

  return true;
}

void SocketCanDriver::HandleErrorFrame(std::uint32_t can_id, const std::uint8_t* data,
                                       std::size_t len) {
  const CanErrorInfo info = ClassifyCanError(can_id, data, len);
  rx_error_frames_.fetch_add(1);

  std::string new_state;
  if (info.bus_off) {
    bus_off_events_.fetch_add(1);
    new_state = "busoff";
  } else if (info.restarted) {
    new_state = "active";
  } else if (info.error_passive) {
    new_state = "error-passive";
  } else if (info.error_warning) {
    new_state = "error-warning";
  }
  if (new_state.empty()) {
    return;
  }

  std::lock_guard<std::mutex> lock(health_mutex_);
  // Rate-limited by construction: only transitions are logged, so a bus that
  // stays bus-off produces one line rather than one per error frame.
  if (new_state != last_bus_state_) {
    std::fprintf(stderr,
                 "[can:%s] controller state: %s -> %s (error frames=%llu, bus-off events=%llu)\n",
                 iface_.c_str(), last_bus_state_.c_str(), new_state.c_str(),
                 static_cast<unsigned long long>(rx_error_frames_.load()),
                 static_cast<unsigned long long>(bus_off_events_.load()));
    last_bus_state_ = new_state;
  }
}

bool SocketCanDriver::ReadFrame(CanFrame& out_frame) {
  if (socket_fd_ < 0) {
    return false;
  }

  // canfd_frame is large enough to hold both classic and FD frames.
  struct canfd_frame raw {};
  struct iovec iov {};
  iov.iov_base = &raw;
  iov.iov_len = sizeof(raw);

  alignas(struct cmsghdr) std::uint8_t control[CMSG_SPACE(sizeof(std::uint32_t))] = {};
  struct msghdr message {};
  message.msg_iov = &iov;
  message.msg_iovlen = 1;
  message.msg_control = control;
  message.msg_controllen = sizeof(control);

  const ssize_t bytes = recvmsg(socket_fd_, &message, 0);
  if (bytes < 0) {
    if (errno == EAGAIN || errno == EWOULDBLOCK) {
      return false;
    }
    return false;
  }

  // Best-effort per-socket overflow count (SO_RXQ_OVFL). The cmsg carries a
  // cumulative counter, so accumulate the delta; a kernel that never emits it
  // simply leaves this at zero, and GetBusHealth's netlink device counters are
  // the visible number.
  for (struct cmsghdr* cmsg = CMSG_FIRSTHDR(&message); cmsg != nullptr;
       cmsg = CMSG_NXTHDR(&message, cmsg)) {
    if (cmsg->cmsg_level == SOL_SOCKET && cmsg->cmsg_type == SO_RXQ_OVFL &&
        cmsg->cmsg_len >= CMSG_LEN(sizeof(std::uint32_t))) {
      std::uint32_t counter = 0;
      std::memcpy(&counter, CMSG_DATA(cmsg), sizeof(counter));
      const std::uint64_t previous = last_drop_counter_.exchange(counter);
      if (counter > previous) {
        rx_dropped_.fetch_add(counter - previous);
      }
    }
  }

  // Accept either a classic frame (sizeof(can_frame)) or an FD frame.
  if (bytes != static_cast<ssize_t>(sizeof(struct can_frame)) &&
      bytes != static_cast<ssize_t>(sizeof(struct canfd_frame))) {
    return false;
  }

  // Error frames are not data: classify, count, log transitions, drop. Without
  // this the CAN_ERR_FLAG masking below would deliver them as a bogus numeric
  // ID with a payload made of error bits.
  if ((raw.can_id & CAN_ERR_FLAG) != 0) {
    HandleErrorFrame(raw.can_id, raw.data, sizeof(raw.data));
    return false;
  }

  const bool is_fd = (bytes == static_cast<ssize_t>(sizeof(struct canfd_frame)));
  const std::uint8_t data_len = (raw.len <= CANFD_MAX_DLEN) ? raw.len : CANFD_MAX_DLEN;

  // raw.can_id carries CAN_EFF_FLAG/CAN_RTR_FLAG/CAN_ERR_FLAG in its top
  // bits per SocketCAN convention -- mask them off so out_frame.can_id is a
  // plain numeric ID, matching WriteFrame's assumption (`can_id > 0x7FF`
  // implies extended) and every comparison against a plain configured ID
  // elsewhere in the system (e.g. CanTp's source_addr/target_addr). Without
  // this, every received 29-bit-ID frame's can_id silently carried
  // CAN_EFF_FLAG (0x80000000) and could never match a plain-valued
  // target_addr.
  out_frame.can_id = raw.can_id & ((raw.can_id & CAN_EFF_FLAG) ? CAN_EFF_MASK : CAN_SFF_MASK);
  out_frame.dlc    = data_len;
  out_frame.flags  = is_fd ? raw.flags : 0;
  std::memset(out_frame.data, 0, sizeof(out_frame.data));
  std::memcpy(out_frame.data, raw.data, data_len);

  struct timespec ts {};
  if (clock_gettime(CLOCK_REALTIME, &ts) == 0) {
    out_frame.timestamp_ns =
        static_cast<std::uint64_t>(ts.tv_sec) * 1000000000ULL + static_cast<std::uint64_t>(ts.tv_nsec);
  } else {
    out_frame.timestamp_ns = 0;
  }

  return true;
}

bool SocketCanDriver::WriteFrame(const CanFrame& frame) {
  if (socket_fd_ < 0) {
    return false;
  }

  const std::uint32_t ext_flag = (frame.can_id > 0x7FF) ? CAN_EFF_FLAG : 0U;
  if (frame.flags & kCanFdFlagFdf) {
    // CAN FD frame
    struct canfd_frame raw {};  // zero-initializes raw.data, so any padding added below is 0x00
    raw.can_id = frame.can_id | ext_flag;
    const std::uint8_t requested_len = frame.dlc <= CANFD_MAX_DLEN ? frame.dlc : CANFD_MAX_DLEN;
    raw.len    = RoundUpToValidCanFdLen(requested_len);
    raw.flags  = frame.flags;
    std::memcpy(raw.data, frame.data, requested_len);
    const ssize_t bytes = write(socket_fd_, &raw, sizeof(raw));
    return bytes == static_cast<ssize_t>(sizeof(raw));
  } else {
    // Classic CAN frame
    struct can_frame raw {};
    raw.can_id  = frame.can_id | ext_flag;
    raw.can_dlc = frame.dlc <= CAN_MAX_DLEN ? frame.dlc : CAN_MAX_DLEN;
    std::memcpy(raw.data, frame.data, raw.can_dlc);
    const ssize_t bytes = write(socket_fd_, &raw, sizeof(raw));
    return bytes == static_cast<ssize_t>(sizeof(raw));
  }
}

void SocketCanDriver::Close() {
  if (socket_fd_ >= 0) {
    close(socket_fd_);
    socket_fd_ = -1;
  }
}

CanInterfaceInfo SocketCanDriver::GetInfo() const {
  CanInterfaceInfo info{};
  info.driver_name = "socketcan";
  info.bitrate = 0;

  const std::string base = "/sys/class/net/" + iface_ + "/";

  // MTU determines CAN FD capability (72 = FD, 16 = classic).
  std::ifstream mtu_file(base + "mtu");
  unsigned mtu = 0;
  if (mtu_file >> mtu) {
    info.fd_support = (mtu >= 72);
  }

  // Operational state: "up", "down", "unknown".
  std::ifstream state_file(base + "operstate");
  std::string state;
  if (state_file >> state) {
    info.state = state;
  } else {
    info.state = "unknown";
  }

  // Attempt to read the kernel driver name from the device's driver symlink.
  // Virtual interfaces (vcan) do not have a device/ subdirectory.
  std::string driver_link = base + "device/driver";
  std::ifstream driver_file(driver_link);
  if (driver_file) {
    // The symlink target looks like "../../../../../../bus/usb/drivers/peak_usb"
    // so we resolve it with readlink.
    char linkbuf[256] = {};
    const ssize_t len = readlink(driver_link.c_str(), linkbuf, sizeof(linkbuf) - 1);
    if (len > 0) {
      linkbuf[len] = '\0';
      std::string resolved(linkbuf);
      // Extract the last component (the driver name).
      const auto pos = resolved.rfind('/');
      if (pos != std::string::npos && pos + 1 < resolved.size()) {
        info.driver_name = resolved.substr(pos + 1);
      }
    }
  }

  return info;
}

CanBusHealth SocketCanDriver::GetBusHealth() const {
  CanBusHealth health;
  health.rx_dropped = rx_dropped_.load();
  health.error_frames = rx_error_frames_.load();
  health.bus_off_events = bus_off_events_.load();
  {
    std::lock_guard<std::mutex> lock(health_mutex_);
    health.state = last_bus_state_;
  }

  // The kernel is the authority on controller state and error counters; the
  // runtime error-frame tracking above is what remains when netlink reports
  // nothing (e.g. vcan, or a query failure).
  CanLinkStatus status;
  if (QueryCanLinkStatus(iface_, &status) && status.found) {
    if (status.has_stats) {
      health.device_rx_dropped = status.rx_dropped;
      health.device_rx_errors = status.rx_errors;
    }
    if (status.has_can_info) {
      if (status.state_code != 0xFFFFFFFFu) {
        health.state_code = status.state_code;
        health.state = status.state;
      }
      health.tec = status.tec;
      health.rec = status.rec;
    }
  }
  return health;
}

}  // namespace boat::hil

#endif
