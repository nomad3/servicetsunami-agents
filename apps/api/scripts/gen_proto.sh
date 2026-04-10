#!/usr/bin/env bash
# Generate Python gRPC stubs from proto files.
# Works on both macOS (dev) and Linux (Docker).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
API_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROTO_DIR="$API_ROOT/proto"
OUT_DIR="$API_ROOT/app/generated"

mkdir -p "$OUT_DIR"

echo "Compiling proto files from $PROTO_DIR -> $OUT_DIR"

for proto_file in "$PROTO_DIR"/*.proto; do
    echo "  $(basename "$proto_file")"
    python -m grpc_tools.protoc \
        -I "$PROTO_DIR" \
        --python_out="$OUT_DIR" \
        --grpc_python_out="$OUT_DIR" \
        "$proto_file"
done

# Fix imports in generated _grpc.py files.
# grpc_tools generates bare imports like `import embedding_pb2 as ...`
# which don't work when the package is imported as `from app.generated import ...`.
# We rewrite them to `from app.generated import embedding_pb2 as ...`.
echo "Fixing imports in generated gRPC stubs..."

if [[ "$(uname)" == "Darwin" ]]; then
    # macOS sed requires '' for in-place with no backup
    SED_INPLACE=(sed -i '')
else
    SED_INPLACE=(sed -i)
fi

for grpc_file in "$OUT_DIR"/*_pb2_grpc.py; do
    [ -f "$grpc_file" ] || continue
    echo "  Fixing $(basename "$grpc_file")"
    # Replace `import X_pb2 as X__pb2` with `from app.generated import X_pb2 as X__pb2`
    "${SED_INPLACE[@]}" 's/^import \([a-z_]*_pb2\) as /from app.generated import \1 as /' "$grpc_file"
done

# Ensure __init__.py exists
touch "$OUT_DIR/__init__.py"

echo "Done. Generated files:"
ls -1 "$OUT_DIR"
