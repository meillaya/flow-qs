FROM python:3.12-bookworm AS builder

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN pip install --no-cache-dir uv==0.10.4

COPY pyproject.toml .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --no-install-project

COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev

FROM python:3.12-slim-bookworm

WORKDIR /app

RUN groupadd -r app && useradd -r -g app app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder --chown=app:app /app/app /app/app
COPY --from=builder --chown=app:app /app/proto /app/proto

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    DB_PATH=/data/orders.db \
    OTEL_SERVICE_NAME=order-service \
    OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector.observability:4317 \
    OTEL_EXPORTER_OTLP_PROTOCOL=grpc \
    OTEL_TRACES_EXPORTER=otlp \
    OTEL_METRICS_EXPORTER=otlp \
    OTEL_LOGS_EXPORTER=otlp

RUN mkdir -p /data && chown app:app /data

USER app

EXPOSE 8004

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8004"]
