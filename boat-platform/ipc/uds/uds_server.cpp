#include "ipc/uds/uds_server.h"

#include <spdlog/spdlog.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include <cstdio>
#include <string>

#include "boat/v1/control.pb.h"
#include "ipc/uds/uds_framing.h"

namespace boat::ipc {

UdsServer::UdsServer(std::string socket_path, CommandHandler handler)
    : socket_path_(std::move(socket_path)), handler_(std::move(handler)) {}

UdsServer::~UdsServer() { Stop(); }

bool UdsServer::Start() {
  if (running_.exchange(true)) {
    return true;
  }

  listen_fd_ = ::socket(AF_UNIX, SOCK_STREAM, 0);
  if (listen_fd_ < 0) {
    running_.store(false);
    return false;
  }

  ::unlink(socket_path_.c_str());
  sockaddr_un addr{};
  addr.sun_family = AF_UNIX;
  std::snprintf(addr.sun_path, sizeof(addr.sun_path), "%s", socket_path_.c_str());

  if (::bind(listen_fd_, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) < 0 ||
      ::listen(listen_fd_, SOMAXCONN) < 0) {
    ::close(listen_fd_);
    listen_fd_ = -1;
    running_.store(false);
    return false;
  }

  accept_thread_ = std::thread(&UdsServer::AcceptLoop, this);
  spdlog::info("UDS server started at {}", socket_path_);
  return true;
}

void UdsServer::Stop() {
  if (!running_.exchange(false)) {
    return;
  }

  if (listen_fd_ >= 0) {
    ::shutdown(listen_fd_, SHUT_RDWR);
    ::close(listen_fd_);
    listen_fd_ = -1;
  }

  if (accept_thread_.joinable()) {
    accept_thread_.join();
  }

  for (auto& thread : client_threads_) {
    if (thread.joinable()) {
      thread.join();
    }
  }
  client_threads_.clear();

  ::unlink(socket_path_.c_str());
  spdlog::info("UDS server stopped at {}", socket_path_);
}

void UdsServer::AcceptLoop() {
  while (running_.load()) {
    const int client_fd = ::accept(listen_fd_, nullptr, nullptr);
    if (client_fd < 0) {
      continue;
    }
    client_threads_.emplace_back(&UdsServer::ClientLoop, this, client_fd);
  }
}

void UdsServer::ClientLoop(int fd) {
  std::string payload;
  while (running_.load() && ReadFrame(fd, payload)) {
    boat::v1::UdsControlMessage msg;
    if (!msg.ParseFromString(payload)) {
      boat::v1::UdsControlResponse bad;
      bad.set_ok(false);
      bad.set_message("invalid control message");
      std::string out;
      bad.SerializeToString(&out);
      WriteFrame(fd, out);
      continue;
    }

    boat::v1::UdsControlResponse response = handler_(msg, fd);
    std::string out;
    response.SerializeToString(&out);
    if (!WriteFrame(fd, out)) {
      break;
    }
  }

  ::close(fd);
}

}  // namespace boat::ipc
