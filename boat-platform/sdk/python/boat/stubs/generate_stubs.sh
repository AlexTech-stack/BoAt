#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_SDK_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
REPO_ROOT="$(cd "${PYTHON_SDK_DIR}/../.." && pwd)"
PROTO_DIR="${REPO_ROOT}/proto"
OUT_DIR="${SCRIPT_DIR}"

python -m grpc_tools.protoc \
  -I"${PROTO_DIR}" \
  --python_out="${OUT_DIR}" \
  --grpc_python_out="${OUT_DIR}" \
  "${PROTO_DIR}/boat/v1/common.proto" \
  "${PROTO_DIR}/boat/v1/control.proto" \
  "${PROTO_DIR}/boat/v1/fault.proto" \
  "${PROTO_DIR}/boat/v1/metrics.proto" \
  "${PROTO_DIR}/boat/v1/plugin.proto" \
  "${PROTO_DIR}/boat/v1/replay.proto" \
  "${PROTO_DIR}/boat/v1/scenario.proto" \
  "${PROTO_DIR}/boat/v1/signal.proto" \
  "${PROTO_DIR}/boat/v1/simulation.proto" \
  "${PROTO_DIR}/boat/v1/trace.proto"
