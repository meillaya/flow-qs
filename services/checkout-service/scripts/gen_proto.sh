#!/usr/bin/env bash
# Regenerate gRPC Python stubs from proto/*.proto.
# Usage: scripts/gen_proto.sh
#
# Idempotent. The generated *_pb2_grpc.py files use absolute imports by
# default, which don't work when the stubs live in a package directory.
# This script patches them to relative imports (one-time per regen).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PROTO_DIR="${ROOT_DIR}/proto"
OUT_DIR="${ROOT_DIR}/proto_gen"

mkdir -p "${OUT_DIR}"
touch "${OUT_DIR}/__init__.py"

# Pick a python that has grpcio-tools available. Prefer the project venv.
if [[ -x "${ROOT_DIR}/.venv/bin/python" ]]; then
    PYTHON="${ROOT_DIR}/.venv/bin/python"
else
    PYTHON="$(command -v python3)"
fi

"${PYTHON}" -m grpc_tools.protoc \
    -I "${PROTO_DIR}" \
    --python_out="${OUT_DIR}" \
    --grpc_python_out="${OUT_DIR}" \
    "${PROTO_DIR}/inventory.proto" \
    "${PROTO_DIR}/payment.proto"

# Fixup: protoc emits `import X_pb2 as X__pb2` which fails for packaged
# imports. Switch to `from . import X_pb2 as X__pb2`.
for f in "${OUT_DIR}"/*_pb2_grpc.py; do
    if [[ -f "${f}" ]]; then
        "${PYTHON}" - "${f}" <<'PY'
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
text = path.read_text()
new = re.sub(
    r"^import (\w+_pb2) as (\w+__pb2)$",
    r"from . import \1 as \2",
    text,
    flags=re.MULTILINE,
)
if new != text:
    path.write_text(new)
PY
    fi
done

echo "ok: stubs regenerated in ${OUT_DIR}"
