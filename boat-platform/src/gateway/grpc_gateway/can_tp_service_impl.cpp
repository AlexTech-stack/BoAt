// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#include "can_tp_service_impl.h"

#include <chrono>
#include <condition_variable>
#include <cstring>
#include <mutex>
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
  if (pc.source_addr() == 0 || pc.target_addr() == 0) {
    return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT,
                        "source_addr and target_addr are both required and must be "
                        "non-zero -- for a single-ID session, pass the same value "
                        "for both explicitly");
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
  if (pc.pad_byte() > 0xFF) {
    return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT, "pad_byte must fit in a uint8 (0-255)");
  }
  if (pc.address_byte() > 0xFF) {
    return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT, "address_byte must fit in a uint8 (0-255)");
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
  cfg.n_bs_ms              = pc.n_bs_ms();  // 0 = ISO default; resolved in can_tp_configure()
  cfg.n_cr_ms              = pc.n_cr_ms();  // 0 = ISO default; resolved in can_tp_configure()
  cfg.addressing_mode      = static_cast<uint32_t>(pc.addressing_mode());
  cfg.address_byte         = static_cast<uint8_t>(pc.address_byte());  // 0 = derive from target_addr; resolved in can_tp_configure()
  cfg.brs                  = pc.brs();
  cfg.pad_byte             = static_cast<uint8_t>(pc.pad_byte());  // 0 = ISO/AUTOSAR default; resolved in can_tp_configure()

  const int32_t configure_result = can_tp->Configure(cfg);
  if (configure_result == -2) {
    std::ostringstream ss;
    ss << "nsdu_id=0x" << std::hex << pc.nsdu_id()
       << " has an active transfer in progress; wait for it to settle before "
          "re-configuring, or remove it first";
    return grpc::Status(grpc::StatusCode::FAILED_PRECONDITION, ss.str());
  }
  if (configure_result == -3) {
    std::ostringstream ss;
    ss << "target_addr=0x" << std::hex << pc.target_addr()
       << " is already used by another nsdu_id on this instance and the two "
          "can't be told apart on RX -- sharing a target_addr is only allowed "
          "when every connection using it has addressing_mode EXTENDED or "
          "MIXED with a distinct address_byte";
    return grpc::Status(grpc::StatusCode::FAILED_PRECONDITION, ss.str());
  }
  if (configure_result != 0) {
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
    session->set_n_bs_ms(s.n_bs_ms);
    session->set_n_cr_ms(s.n_cr_ms);
    session->set_brs(s.brs);
    session->set_pad_byte(s.pad_byte);
    session->set_addressing_mode(static_cast<boat::v1::CanTpAddressingMode>(s.addressing_mode));
    session->set_address_byte(s.address_byte);
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

grpc::Status CanTpServiceImpl::RemoveSession(
    grpc::ServerContext* context,
    const boat::v1::RemoveSessionRequest* request,
    boat::v1::RemoveSessionResponse* response) {
  grpc::StatusCode status_code = grpc::StatusCode::OK;
  std::string status_message;
  auto* can_tp = GetCanTp(request->iface(), &status_code, &status_message);
  if (can_tp == nullptr) {
    return grpc::Status(status_code, status_message);
  }

  const uint32_t nsdu_id = request->nsdu_id();
  const int32_t result = can_tp->Remove(nsdu_id);
  if (result == -1) {
    std::ostringstream ss;
    ss << "no N-SDU connection configured for nsdu_id=0x" << std::hex << nsdu_id;
    return grpc::Status(grpc::StatusCode::NOT_FOUND, ss.str());
  }
  if (result == -2) {
    std::ostringstream ss;
    ss << "nsdu_id=0x" << std::hex << nsdu_id
       << " has a multi-frame transfer in progress; wait for it to settle before removing";
    return grpc::Status(grpc::StatusCode::FAILED_PRECONDITION, ss.str());
  }

  response->set_ok(true);

  {
    RpcEvent ev;
    ev.timestamp_ns = NowNsCanTp();
    ev.method     = "CanTpService/RemoveSession";
    ev.peer       = context->peer();
    ev.event_type = "DATA";
    ev.call_type  = "UNARY";
    std::ostringstream ss;
    ss << "nsdu_id=0x" << std::hex << nsdu_id << std::dec << " iface=" << can_tp->GetIface();
    ev.summary = ss.str();
    ctx_.audit_log.Push(std::move(ev));
  }

  return grpc::Status::OK;
}

grpc::Status CanTpServiceImpl::Subscribe(
    grpc::ServerContext* context,
    const boat::v1::SubscribeRequest* request,
    grpc::ServerWriter<boat::v1::CanTpRxEvent>* writer) {
  std::vector<uint32_t> nsdu_ids(request->nsdu_ids().begin(), request->nsdu_ids().end());
  const std::string peer = context->peer();

  // Resolve the target instance(s): a specific iface, or every loaded
  // instance when none is given -- same "iface empty = all" convention
  // ListSessions already uses.
  std::vector<std::pair<std::string, boat::core::ICanTp*>> targets;
  if (!request->iface().empty()) {
    grpc::StatusCode status_code = grpc::StatusCode::OK;
    std::string status_message;
    auto* can_tp = GetCanTp(request->iface(), &status_code, &status_message);
    if (can_tp == nullptr) {
      return grpc::Status(status_code, status_message);
    }
    targets.emplace_back(request->iface(), can_tp);
  } else {
    targets = GetAllCanTp();
    if (targets.empty()) {
      return grpc::Status(grpc::StatusCode::NOT_FOUND, "CanTp plugin not loaded");
    }
  }

  {
    std::ostringstream ss;
    if (nsdu_ids.empty()) {
      ss << "nsdu_ids=(all)";
    } else {
      ss << "nsdu_ids=[";
      for (std::size_t i = 0; i < nsdu_ids.size(); ++i) {
        if (i) ss << ',';
        ss << "0x" << std::hex << nsdu_ids[i];
      }
      ss << ']';
    }
    ss << " targets=" << targets.size();
    RpcEvent ev;
    ev.timestamp_ns = NowNsCanTp();
    ev.method     = "CanTpService/Subscribe";
    ev.peer       = peer;
    ev.event_type = "SUBSCRIBE_OPEN";
    ev.call_type  = "SERVER_STREAM";
    ev.summary    = ss.str();
    ctx_.audit_log.Push(std::move(ev));
  }

  std::mutex                          queue_mutex;
  std::condition_variable             queue_cv;
  std::vector<boat::v1::CanTpRxEvent> queue;

  std::vector<std::pair<boat::core::ICanTp*, boat::core::ICanTp::SubId>> subs;
  subs.reserve(targets.size());
  for (auto& [iface, can_tp] : targets) {
    const auto sub_id = can_tp->Subscribe(
        nsdu_ids,
        [&queue_mutex, &queue_cv, &queue, iface](uint32_t nsdu_id, const std::vector<uint8_t>& payload) {
          boat::v1::CanTpRxEvent ev;
          ev.set_iface(iface);
          ev.set_nsdu_id(nsdu_id);
          ev.set_data(payload.data(), payload.size());
          ev.set_timestamp_ns(NowNsCanTp());
          {
            std::lock_guard<std::mutex> lock(queue_mutex);
            queue.push_back(std::move(ev));
          }
          queue_cv.notify_one();
        });
    subs.emplace_back(can_tp, sub_id);
  }

  while (!context->IsCancelled()) {
    std::vector<boat::v1::CanTpRxEvent> pending;
    {
      std::unique_lock<std::mutex> lock(queue_mutex);
      queue_cv.wait_for(lock, std::chrono::milliseconds(50),
                        [&queue] { return !queue.empty(); });
      pending.swap(queue);
    }
    for (const auto& ev : pending) {
      if (!writer->Write(ev)) {
        for (auto& [can_tp, sub_id] : subs) can_tp->Unsubscribe(sub_id);
        return grpc::Status::OK;
      }
      RpcEvent audit_ev;
      audit_ev.timestamp_ns = NowNsCanTp();
      audit_ev.method     = "CanTpService/Subscribe";
      audit_ev.peer       = peer;
      audit_ev.event_type = "DATA";
      audit_ev.call_type  = "SERVER_STREAM";
      std::ostringstream ss;
      ss << "nsdu_id=0x" << std::hex << ev.nsdu_id() << std::dec << " len=" << ev.data().size()
         << " iface=" << ev.iface();
      audit_ev.summary = ss.str();
      ctx_.audit_log.Push(std::move(audit_ev));
    }
  }

  for (auto& [can_tp, sub_id] : subs) can_tp->Unsubscribe(sub_id);
  return grpc::Status::OK;
}

grpc::Status CanTpServiceImpl::SubscribeErrors(
    grpc::ServerContext* context,
    const boat::v1::SubscribeRequest* request,
    grpc::ServerWriter<boat::v1::CanTpErrorEvent>* writer) {
  std::vector<uint32_t> nsdu_ids(request->nsdu_ids().begin(), request->nsdu_ids().end());
  const std::string peer = context->peer();

  // Same target resolution as Subscribe().
  std::vector<std::pair<std::string, boat::core::ICanTp*>> targets;
  if (!request->iface().empty()) {
    grpc::StatusCode status_code = grpc::StatusCode::OK;
    std::string status_message;
    auto* can_tp = GetCanTp(request->iface(), &status_code, &status_message);
    if (can_tp == nullptr) {
      return grpc::Status(status_code, status_message);
    }
    targets.emplace_back(request->iface(), can_tp);
  } else {
    targets = GetAllCanTp();
    if (targets.empty()) {
      return grpc::Status(grpc::StatusCode::NOT_FOUND, "CanTp plugin not loaded");
    }
  }

  {
    std::ostringstream ss;
    if (nsdu_ids.empty()) {
      ss << "nsdu_ids=(all)";
    } else {
      ss << "nsdu_ids=[";
      for (std::size_t i = 0; i < nsdu_ids.size(); ++i) {
        if (i) ss << ',';
        ss << "0x" << std::hex << nsdu_ids[i];
      }
      ss << ']';
    }
    ss << " targets=" << targets.size();
    RpcEvent ev;
    ev.timestamp_ns = NowNsCanTp();
    ev.method     = "CanTpService/SubscribeErrors";
    ev.peer       = peer;
    ev.event_type = "SUBSCRIBE_OPEN";
    ev.call_type  = "SERVER_STREAM";
    ev.summary    = ss.str();
    ctx_.audit_log.Push(std::move(ev));
  }

  std::mutex                             queue_mutex;
  std::condition_variable                queue_cv;
  std::vector<boat::v1::CanTpErrorEvent> queue;

  std::vector<std::pair<boat::core::ICanTp*, boat::core::ICanTp::SubId>> subs;
  subs.reserve(targets.size());
  for (auto& [iface, can_tp] : targets) {
    const auto sub_id = can_tp->SubscribeErrors(
        nsdu_ids,
        [&queue_mutex, &queue_cv, &queue, iface](const boat::core::CanTpErrorEvent& err) {
          boat::v1::CanTpErrorEvent ev;
          ev.set_iface(iface);
          ev.set_nsdu_id(err.nsdu_id);
          ev.set_result(static_cast<boat::v1::CanTpResult>(err.result));
          ev.set_message(err.message);
          ev.set_timestamp_ns(NowNsCanTp());
          {
            std::lock_guard<std::mutex> lock(queue_mutex);
            queue.push_back(std::move(ev));
          }
          queue_cv.notify_one();
        });
    subs.emplace_back(can_tp, sub_id);
  }

  while (!context->IsCancelled()) {
    std::vector<boat::v1::CanTpErrorEvent> pending;
    {
      std::unique_lock<std::mutex> lock(queue_mutex);
      queue_cv.wait_for(lock, std::chrono::milliseconds(50),
                        [&queue] { return !queue.empty(); });
      pending.swap(queue);
    }
    for (const auto& ev : pending) {
      if (!writer->Write(ev)) {
        for (auto& [can_tp, sub_id] : subs) can_tp->UnsubscribeErrors(sub_id);
        return grpc::Status::OK;
      }
      RpcEvent audit_ev;
      audit_ev.timestamp_ns = NowNsCanTp();
      audit_ev.method     = "CanTpService/SubscribeErrors";
      audit_ev.peer       = peer;
      audit_ev.event_type = "DATA";
      audit_ev.call_type  = "SERVER_STREAM";
      std::ostringstream ss;
      ss << "nsdu_id=0x" << std::hex << ev.nsdu_id() << std::dec
         << " result=" << boat::v1::CanTpResult_Name(ev.result()) << " iface=" << ev.iface();
      audit_ev.summary = ss.str();
      ctx_.audit_log.Push(std::move(audit_ev));
    }
  }

  for (auto& [can_tp, sub_id] : subs) can_tp->UnsubscribeErrors(sub_id);
  return grpc::Status::OK;
}

}  // namespace boat::gateway
