#include "can_tp_service_impl.h"

#include <chrono>
#include <cstring>
#include <sstream>
#include <vector>

#include "rpc_audit_log.h"

namespace boat::gateway {

namespace {
uint64_t NowNsCanTp() {
  return static_cast<uint64_t>(
      std::chrono::duration_cast<std::chrono::nanoseconds>(
          std::chrono::system_clock::now().time_since_epoch()).count());
}

constexpr char kCanTpServicePrefix[] = "can_tp:";
}  // namespace

CanTpServiceImpl::CanTpServiceImpl(GatewayContext& ctx) : ctx_(ctx) {}

boat::core::ICanTp* CanTpServiceImpl::GetCanTp(const std::string& iface,
                                               grpc::StatusCode* status_out,
                                               std::string* message_out) {
  if (!iface.empty()) {
    auto* found = static_cast<boat::core::ICanTp*>(
        ctx_.plugin_manager.FindService(kCanTpServicePrefix + iface));
    if (found == nullptr) {
      *status_out = grpc::StatusCode::NOT_FOUND;
      *message_out = "no CanTp plugin loaded for iface '" + iface + "'";
    }
    return found;
  }

  // No iface given: fall back to "the only loaded instance", same as
  // before multi-instance support existed. Ambiguous if more than one.
  std::vector<std::string> ifaces;
  for (const auto& name : ctx_.plugin_manager.ListServices()) {
    if (name.rfind(kCanTpServicePrefix, 0) == 0) {
      ifaces.push_back(name.substr(std::strlen(kCanTpServicePrefix)));
    }
  }
  if (ifaces.empty()) {
    *status_out = grpc::StatusCode::NOT_FOUND;
    *message_out = "CanTp plugin not loaded";
    return nullptr;
  }
  if (ifaces.size() > 1) {
    std::ostringstream ss;
    ss << "multiple CanTp instances loaded (";
    for (std::size_t i = 0; i < ifaces.size(); ++i) {
      if (i != 0) ss << ", ";
      ss << ifaces[i];
    }
    ss << "); specify --iface to disambiguate";
    *status_out = grpc::StatusCode::FAILED_PRECONDITION;
    *message_out = ss.str();
    return nullptr;
  }
  return static_cast<boat::core::ICanTp*>(
      ctx_.plugin_manager.FindService(kCanTpServicePrefix + ifaces.front()));
}

std::vector<std::pair<std::string, boat::core::ICanTp*>> CanTpServiceImpl::GetAllCanTp() {
  std::vector<std::pair<std::string, boat::core::ICanTp*>> result;
  for (const auto& name : ctx_.plugin_manager.ListServices()) {
    if (name.rfind(kCanTpServicePrefix, 0) != 0) continue;
    auto* can_tp = static_cast<boat::core::ICanTp*>(ctx_.plugin_manager.FindService(name));
    if (can_tp != nullptr) {
      result.emplace_back(name.substr(std::strlen(kCanTpServicePrefix)), can_tp);
    }
  }
  return result;
}

grpc::Status CanTpServiceImpl::Configure(
    grpc::ServerContext* context,
    const boat::v1::ConfigureRequest* request,
    boat::v1::ConfigureResponse* response) {
  grpc::StatusCode status_code = grpc::StatusCode::OK;
  std::string status_message;
  auto* can_tp = GetCanTp(request->iface(), &status_code, &status_message);
  if (can_tp == nullptr) {
    return grpc::Status(status_code, status_message);
  }

  const auto& pc = request->config();
  if (pc.nsdu_id() == 0) {
    return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT, "nsdu_id must be non-zero");
  }
  if ((pc.source_addr() == 0) != (pc.target_addr() == 0)) {
    return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT,
                        "source_addr and target_addr must either both be 0 "
                        "(fall back to nsdu_id) or both be non-zero");
  }
  if (pc.can_dlc() != 8 && pc.can_dlc() != 64) {
    return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT, "can_dlc must be 8 or 64");
  }
  if (pc.block_size() > 0xFF) {
    return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT, "block_size must fit in a uint8 (0-255)");
  }
  if (pc.st_min() > 0xFF) {
    return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT, "st_min must fit in a uint8 (0-255)");
  }

  CanTpConfig cfg{};
  cfg.nsdu_id              = pc.nsdu_id();
  cfg.source_addr          = pc.source_addr();
  cfg.target_addr          = pc.target_addr();
  cfg.rx_buffer_size       = pc.rx_buffer_size() != 0 ? pc.rx_buffer_size() : 4095;
  cfg.block_size           = static_cast<uint8_t>(pc.block_size());
  cfg.st_min               = static_cast<uint8_t>(pc.st_min());
  cfg.can_dlc              = static_cast<uint8_t>(pc.can_dlc());
  cfg.extended_addressing  = pc.extended_addressing();

  if (can_tp->Configure(cfg) != 0) {
    return grpc::Status(grpc::StatusCode::INTERNAL, "CanTp Configure() failed");
  }

  response->set_ok(true);
  response->set_iface(can_tp->GetIface());

  {
    RpcEvent ev;
    ev.timestamp_ns = NowNsCanTp();
    ev.method     = "CanTpService/Configure";
    ev.peer       = context->peer();
    ev.event_type = "DATA";
    ev.call_type  = "UNARY";
    std::ostringstream ss;
    ss << "nsdu_id=0x" << std::hex << pc.nsdu_id()
       << " source_addr=0x" << pc.source_addr()
       << " target_addr=0x" << pc.target_addr()
       << std::dec << " iface=" << response->iface();
    ev.summary = ss.str();
    ctx_.audit_log.Push(std::move(ev));
  }

  return grpc::Status::OK;
}

grpc::Status CanTpServiceImpl::Send(
    grpc::ServerContext* context,
    const boat::v1::SendRequest* request,
    boat::v1::SendResponse* response) {
  grpc::StatusCode status_code = grpc::StatusCode::OK;
  std::string status_message;
  auto* can_tp = GetCanTp(request->iface(), &status_code, &status_message);
  if (can_tp == nullptr) {
    return grpc::Status(status_code, status_message);
  }

  if (request->data().empty()) {
    return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT, "data must be non-empty");
  }

  const uint32_t nsdu_id = request->nsdu_id();
  if (!can_tp->HasConnection(nsdu_id)) {
    std::ostringstream ss;
    ss << "no N-SDU connection configured for nsdu_id=0x" << std::hex << nsdu_id
       << "; call Configure first";
    return grpc::Status(grpc::StatusCode::FAILED_PRECONDITION, ss.str());
  }

  const auto& data = request->data();
  const int32_t result = can_tp->Send(
      nsdu_id, reinterpret_cast<const uint8_t*>(data.data()),
      static_cast<uint32_t>(data.size()));

  {
    RpcEvent ev;
    ev.timestamp_ns = NowNsCanTp();
    ev.method     = "CanTpService/Send";
    ev.peer       = context->peer();
    ev.event_type = "DATA";
    ev.call_type  = "UNARY";
    std::ostringstream ss;
    ss << "nsdu_id=0x" << std::hex << nsdu_id
       << std::dec << " len=" << data.size() << " result=" << result;
    ev.summary = ss.str();
    ctx_.audit_log.Push(std::move(ev));
  }

  switch (result) {
    case 1:
      response->set_result(boat::v1::SEND_RESULT_SINGLE_FRAME);
      return grpc::Status::OK;
    case 0:
      response->set_result(boat::v1::SEND_RESULT_MULTI_FRAME_INITIATED);
      return grpc::Status::OK;
    default: {
      std::ostringstream ss;
      ss << "nsdu_id=0x" << std::hex << nsdu_id
         << " is busy with an in-progress multi-frame transmission";
      return grpc::Status(grpc::StatusCode::FAILED_PRECONDITION, ss.str());
    }
  }
}

namespace {
void AppendSessions(const std::string& iface, boat::core::ICanTp* can_tp,
                    boat::v1::ListSessionsResponse* response) {
  for (const auto& s : can_tp->ListSessions()) {
    auto* session = response->add_sessions();
    session->set_iface(iface);
    session->set_nsdu_id(s.nsdu_id);
    session->set_source_addr(s.source_addr);
    session->set_target_addr(s.target_addr);
    session->set_rx_buffer_size(s.rx_buffer_size);
    session->set_block_size(s.block_size);
    session->set_st_min(s.st_min);
    session->set_can_dlc(s.can_dlc);
    session->set_extended_addressing(s.extended_addressing);
    session->set_rx_state(s.rx_state);
    session->set_tx_state(s.tx_state);
  }
}
}  // namespace

grpc::Status CanTpServiceImpl::ListSessions(
    grpc::ServerContext* /*context*/,
    const boat::v1::ListSessionsRequest* request,
    boat::v1::ListSessionsResponse* response) {
  if (!request->iface().empty()) {
    grpc::StatusCode status_code = grpc::StatusCode::OK;
    std::string status_message;
    auto* can_tp = GetCanTp(request->iface(), &status_code, &status_message);
    if (can_tp == nullptr) {
      return grpc::Status(status_code, status_message);
    }
    AppendSessions(request->iface(), can_tp, response);
    return grpc::Status::OK;
  }

  for (auto& [iface, can_tp] : GetAllCanTp()) {
    AppendSessions(iface, can_tp, response);
  }
  return grpc::Status::OK;
}

}  // namespace boat::gateway
