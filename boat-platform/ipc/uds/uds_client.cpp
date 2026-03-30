#include "ipc/uds/uds_client.h"

#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include <cstdio>

#include "ipc/uds/uds_framing.h"

namespace boat::ipc {

namespace {

void PopulateCommand(boat::v1::UdsControlMessage* msg, UdsCommand cmd) {
  switch (cmd) {
    case UdsCommand::START:
      msg->mutable_start();
      break;
    case UdsCommand::PAUSE:
      msg->mutable_pause();
      break;
    case UdsCommand::STEP:
      msg->mutable_step();
      break;
    case UdsCommand::RESET:
      msg->mutable_reset();
      break;
    case UdsCommand::STOP:
      msg->mutable_stop();
      break;
    case UdsCommand::INJECT_FAULT:
      msg->mutable_inject_fault();
      break;
    case UdsCommand::QUERY_STATE:
      msg->mutable_query_state();
      break;
  }
}

}  // namespace

bool UdsClient::Connect(const std::string& socket_path) {
  Disconnect();
  fd_ = ::socket(AF_UNIX, SOCK_STREAM, 0);
  if (fd_ < 0) {
    return false;
  }

  sockaddr_un addr{};
  addr.sun_family = AF_UNIX;
  std::snprintf(addr.sun_path, sizeof(addr.sun_path), "%s", socket_path.c_str());
  if (::connect(fd_, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) < 0) {
    Disconnect();
    return false;
  }
  return true;
}

boat::v1::UdsControlResponse UdsClient::SendCommand(UdsCommand cmd, const std::string& payload_bytes) {
  boat::v1::UdsControlMessage message;
  PopulateCommand(&message, cmd);
  message.set_payload_bytes(payload_bytes);
  return SendMessage(message);
}

boat::v1::UdsControlResponse UdsClient::SendMessage(const boat::v1::UdsControlMessage& message) {
  boat::v1::UdsControlResponse response;
  if (fd_ < 0) {
    response.set_ok(false);
    response.set_message("not connected");
    return response;
  }

  std::string out;
  message.SerializeToString(&out);
  if (!WriteFrame(fd_, out)) {
    response.set_ok(false);
    response.set_message("write failed");
    return response;
  }

  std::string in;
  if (!ReadFrame(fd_, in) || !response.ParseFromString(in)) {
    response.Clear();
    response.set_ok(false);
    response.set_message("read failed");
  }
  return response;
}

boat::v1::UdsControlResponse UdsClient::SendStepCommand(uint32_t ticks, const std::string& payload_bytes) {
  boat::v1::UdsControlMessage message;
  message.mutable_step()->set_ticks(ticks);
  message.set_payload_bytes(payload_bytes);
  return SendMessage(message);
}

boat::v1::UdsControlResponse UdsClient::SendInjectFaultCommand(const std::string& fault_payload,
                                                               const std::string& payload_bytes) {
  boat::v1::UdsControlMessage message;
  message.mutable_inject_fault()->set_payload(fault_payload);
  message.set_payload_bytes(payload_bytes);
  return SendMessage(message);
}

boat::v1::UdsControlResponse UdsClient::SendQueryStateCommand(const std::string& payload_bytes) {
  boat::v1::UdsControlMessage message;
  message.mutable_query_state();
  message.set_payload_bytes(payload_bytes);
  return SendMessage(message);
}

void UdsClient::Disconnect() {
  if (fd_ >= 0) {
    ::shutdown(fd_, SHUT_RDWR);
    ::close(fd_);
    fd_ = -1;
  }
}

}  // namespace boat::ipc
