FROM python:3.12-slim@sha256:c3d81d25b3154142b0b42eb1e61300024426268edeb5b5a26dd7ddf64d9daf28

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.10.4@sha256:4cac394b6b72846f8a85a7a0e577c6d61d4e17fe2ccee65d9451a8b3c9efb4ac /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .

RUN uv run --no-dev python -m grpc_tools.protoc \
    -I proto \
    --python_out=app/grpc_gen \
    --grpc_python_out=app/grpc_gen \
    proto/inventory.proto && \
    sed -i 's/^import inventory_pb2 as /from . import inventory_pb2 as /' app/grpc_gen/inventory_pb2_grpc.py

RUN groupadd --system --gid 1001 app \
    && useradd --system --uid 1001 --gid app --home /app app \
    && chown -R app:app /app

USER app

EXPOSE 8001 50051

CMD ["uv", "run", "--no-dev", "python", "-m", "app.main"]
