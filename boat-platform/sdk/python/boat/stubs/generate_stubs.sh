#!/usr/bin/env bash
# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_SDK_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
REPO_ROOT="$(cd "${PYTHON_SDK_DIR}/../.." && pwd)"
PROTO_DIR="${REPO_ROOT}/proto"
OUT_DIR="${SCRIPT_DIR}"

# Every .proto in the tree, not a hand-maintained list: a list silently skips
# a newly added proto, and leaves a stale generated module behind when one is
# deleted. Sorted so the protoc invocation is reproducible.
mapfile -t PROTO_FILES < <(find "${PROTO_DIR}/boat/v1" -name '*.proto' | sort)

if [[ ${#PROTO_FILES[@]} -eq 0 ]]; then
  echo "No .proto files found under ${PROTO_DIR}/boat/v1" >&2
  exit 1
fi

# Drop generated modules whose .proto no longer exists, so a deleted service
# cannot keep importing.
for generated in "${OUT_DIR}"/boat/v1/*_pb2.py "${OUT_DIR}"/boat/v1/*_pb2_grpc.py; do
  [[ -e "${generated}" ]] || continue
  base="$(basename "${generated}")"
  stem="${base%_pb2_grpc.py}"
  stem="${stem%_pb2.py}"
  if [[ ! -f "${PROTO_DIR}/boat/v1/${stem}.proto" ]]; then
    echo "Removing stale stub: ${base}"
    rm -f "${generated}"
  fi
done

python3 -m grpc_tools.protoc \
  -I"${PROTO_DIR}" \
  --python_out="${OUT_DIR}" \
  --grpc_python_out="${OUT_DIR}" \
  "${PROTO_FILES[@]}"
