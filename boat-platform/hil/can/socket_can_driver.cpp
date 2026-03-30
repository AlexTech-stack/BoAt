#include "can/socket_can_driver.h"

#ifdef __linux__

#include <cerrno>
#include <cstring>
#include <ctime>
#include <utility>

#include <linux/can.h>
#include <linux/can/raw.h>
#include <net/if.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <unistd.h>

namespace boat::hil {

SocketCanDriver::SocketCanDriver(std::string interface_name) : iface_(std::move(interface_name)) {}

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

  return true;
}

bool SocketCanDriver::ReadFrame(CanFrame& out_frame) {
  if (socket_fd_ < 0) {
    return false;
  }

  struct can_frame raw {};
  const ssize_t bytes = read(socket_fd_, &raw, sizeof(raw));
  if (bytes < 0) {
    if (errno == EAGAIN || errno == EWOULDBLOCK) {
      return false;
    }
    return false;
  }
  if (bytes != static_cast<ssize_t>(sizeof(raw))) {
    return false;
  }

  out_frame.can_id = raw.can_id;
  out_frame.dlc = raw.can_dlc;
  std::memset(out_frame.data, 0, sizeof(out_frame.data));
  std::memcpy(out_frame.data, raw.data, raw.can_dlc);

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

  struct can_frame raw {};
  raw.can_id = frame.can_id;
  raw.can_dlc = frame.dlc > 8 ? 8 : frame.dlc;
  std::memcpy(raw.data, frame.data, raw.can_dlc);

  const ssize_t bytes = write(socket_fd_, &raw, sizeof(raw));
  return bytes == static_cast<ssize_t>(sizeof(raw));
}

void SocketCanDriver::Close() {
  if (socket_fd_ >= 0) {
    close(socket_fd_);
    socket_fd_ = -1;
  }
}

}  // namespace boat::hil

#endif
