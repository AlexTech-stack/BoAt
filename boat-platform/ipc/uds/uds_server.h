#pragma once

#include <atomic>
#include <functional>
#include <string>
#include <thread>
#include <vector>

#include "boat/v1/control.pb.h"
#include "ipc/uds/uds_types.h"

namespace boat::ipc {

class UdsServer {
 public:
  using CommandHandler =
      std::function<boat::v1::UdsControlResponse(const boat::v1::UdsControlMessage& message, int client_fd)>;

  UdsServer(std::string socket_path, CommandHandler handler);
  ~UdsServer();

  bool Start();
  void Stop();

 private:
  void AcceptLoop();
  void ClientLoop(int fd);

  std::string socket_path_;
  CommandHandler handler_;
  std::atomic<bool> running_{false};
  int listen_fd_{-1};
  std::thread accept_thread_;
  std::vector<std::thread> client_threads_;
};

}  // namespace boat::ipc
