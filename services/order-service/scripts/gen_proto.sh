#!/usr/bin/env bash
# Regenerate typed Python gRPC stubs from the canonical gateway contract.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${ROOT_DIR}/.venv/bin/python"
OUT_DIR="${ROOT_DIR}/app/proto"

"${PYTHON}" -m grpc_tools.protoc \
    -I "${ROOT_DIR}/proto" \
    --python_out="${OUT_DIR}" \
    --pyi_out="${OUT_DIR}" \
    --grpc_python_out="${OUT_DIR}" \
    "${ROOT_DIR}/proto/payment.proto"

sed -i 's/^import payment_pb2 as payment__pb2$/from . import payment_pb2 as payment__pb2/' \
    "${OUT_DIR}/payment_pb2_grpc.py"
sed -i 's/, metaclass=_enum_type_wrapper.EnumTypeWrapper//' \
    "${OUT_DIR}/payment_pb2.pyi"
