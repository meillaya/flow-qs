FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml .
RUN uv sync --no-dev

COPY . .

RUN uv run --no-dev python -m grpc_tools.protoc \
    -I proto \
    --python_out=app/grpc_gen \
    --grpc_python_out=app/grpc_gen \
    proto/inventory.proto && \
    sed -i 's/^import inventory_pb2 as /from . import inventory_pb2 as /' app/grpc_gen/inventory_pb2_grpc.py

EXPOSE 8001 50051

CMD ["uv", "run", "--no-dev", "python", "-m", "app.main"]
