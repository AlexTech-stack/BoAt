#include "health_service_impl.h"

#include <chrono>
#include <thread>

namespace boat::gateway {

grpc::Status HealthServiceImpl::Check(
    grpc::ServerContext* /*context*/,
    const grpc::health::v1::HealthCheckRequest* /*request*/,
    grpc::health::v1::HealthCheckResponse* response) {
  response->set_status(grpc::health::v1::HealthCheckResponse::SERVING);
  return grpc::Status::OK;
}

grpc::Status HealthServiceImpl::Watch(
    grpc::ServerContext* context,
    const grpc::health::v1::HealthCheckRequest* /*request*/,
    grpc::ServerWriter<grpc::health::v1::HealthCheckResponse>* writer) {
  grpc::health::v1::HealthCheckResponse response;
  response.set_status(grpc::health::v1::HealthCheckResponse::SERVING);
  writer->Write(response);
  while (!context->IsCancelled()) {
    // Block until the client cancels or the context is done.
    std::this_thread::sleep_for(std::chrono::seconds(1));
  }
  return grpc::Status::OK;
}

}  // namespace boat::gateway
