#include "trace_service_impl.h"

#include <algorithm>
#include <cstring>
#include <exception>
#include <string>

namespace boat::gateway {
namespace {
std::size_t ParseToken(const std::string& token) {
  if (token.empty()) {
    return 0;
  }
  return static_cast<std::size_t>(std::stoull(token));
}

grpc::Status MapTraceException(const std::exception& ex) {
  const std::string message = ex.what();
  if (message.find("not found") != std::string::npos || message.find("missing") != std::string::npos) {
    return grpc::Status(grpc::StatusCode::NOT_FOUND, message);
  }
  if (message.find("invalid") != std::string::npos || message.find("out of bounds") != std::string::npos) {
    return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT, message);
  }
  return grpc::Status(grpc::StatusCode::INTERNAL, message);
}
}  // namespace

TraceServiceImpl::TraceServiceImpl(GatewayContext& ctx) : ctx_(ctx) {}

grpc::Status TraceServiceImpl::GetTrace(grpc::ServerContext*, const boat::v1::GetTraceRequest* request,
                                        boat::v1::TraceResponse* response) {
  bool mapped = false;
  try {
    auto bytes = ctx_.trace_store.ReadTraceMmap(request->trace_id());
    mapped = true;
    const auto* ptr = bytes.data();
    std::size_t offset = 0;
    while (offset + sizeof(boat::store::TraceRecordHeader) <= bytes.size()) {
      boat::store::TraceRecordHeader header{};
      std::memcpy(&header, ptr + offset, sizeof(header));
      offset += sizeof(header);
      if (offset + header.payload_size > bytes.size()) {
        break;
      }
      auto* event = response->add_events();
      event->set_trace_id(request->trace_id());
      event->set_tick(header.tick);
      event->set_category(std::to_string(header.event_type));
      event->set_payload(std::string(reinterpret_cast<const char*>(ptr + offset), header.payload_size));
      offset += header.payload_size;
    }
    response->mutable_page()->set_total_size(static_cast<std::uint32_t>(response->events_size()));
    ctx_.trace_store.UnmapTrace(request->trace_id());
    return grpc::Status::OK;
  } catch (const std::exception& ex) {
    if (mapped) {
      ctx_.trace_store.UnmapTrace(request->trace_id());
    }
    return MapTraceException(ex);
  } catch (...) {
    if (mapped) {
      ctx_.trace_store.UnmapTrace(request->trace_id());
    }
    return grpc::Status(grpc::StatusCode::INTERNAL, "unexpected get trace error");
  }
}

grpc::Status TraceServiceImpl::ListTraces(grpc::ServerContext*, const boat::v1::ListTracesRequest* request,
                                          boat::v1::TraceResponse* response) {
  try {
    const auto traces = ctx_.trace_store.ListAllTraces();
    const std::size_t offset = ParseToken(request->page().page_token());
    const std::size_t page_size = request->page().page_size() == 0 ? traces.size() : request->page().page_size();
    const std::size_t end = std::min(traces.size(), offset + page_size);
    for (std::size_t i = offset; i < end; ++i) {
      auto* event = response->add_events();
      event->set_trace_id(traces[i].id);
      event->set_tick(traces[i].start_tick);
      event->set_category("trace_record");
      event->set_payload(traces[i].storage_path);
    }
    response->mutable_page()->set_total_size(static_cast<std::uint32_t>(traces.size()));
    if (end < traces.size()) {
      response->mutable_page()->set_next_page_token(std::to_string(end));
    }
    return grpc::Status::OK;
  } catch (const std::exception& ex) {
    return MapTraceException(ex);
  } catch (...) {
    return grpc::Status(grpc::StatusCode::INTERNAL, "unexpected list traces error");
  }
}

grpc::Status TraceServiceImpl::StreamTrace(grpc::ServerContext* context, const boat::v1::StreamTraceRequest* request,
                                           grpc::ServerWriter<boat::v1::TraceEvent>* writer) {
  bool mapped = false;
  try {
    auto bytes = ctx_.trace_store.ReadTraceMmap(request->trace_id());
    mapped = true;
    const auto* ptr = bytes.data();
    std::size_t offset = 0;
    while (!context->IsCancelled() && offset + sizeof(boat::store::TraceRecordHeader) <= bytes.size()) {
      boat::store::TraceRecordHeader header{};
      std::memcpy(&header, ptr + offset, sizeof(header));
      offset += sizeof(header);
      if (offset + header.payload_size > bytes.size()) {
        break;
      }
      boat::v1::TraceEvent event;
      event.set_trace_id(request->trace_id());
      event.set_tick(header.tick);
      event.set_category(std::to_string(header.event_type));
      event.set_payload(std::string(reinterpret_cast<const char*>(ptr + offset), header.payload_size));
      if (!writer->Write(event)) {
        break;
      }
      offset += header.payload_size;
    }
    ctx_.trace_store.UnmapTrace(request->trace_id());
    return grpc::Status::OK;
  } catch (const std::exception& ex) {
    if (mapped) {
      ctx_.trace_store.UnmapTrace(request->trace_id());
    }
    return MapTraceException(ex);
  } catch (...) {
    if (mapped) {
      ctx_.trace_store.UnmapTrace(request->trace_id());
    }
    return grpc::Status(grpc::StatusCode::INTERNAL, "unexpected stream trace error");
  }
}

}  // namespace boat::gateway
