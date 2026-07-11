# syntax=docker/dockerfile:1.7
# Multi-stage build for checkout-service.
# Stage 1: build a venv with uv so we get deterministic, cached installs.
# Stage 2: copy the venv + app code into a slim runtime image.

# -- builder ----------------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy

# uv is the only build-time dep we need from the network.
COPY --from=ghcr.io/astral-sh/uv:0.10.4 /uv /usr/local/bin/uv

WORKDIR /build

# Layer 1: dependencies only. This stays cached when only app code changes.
COPY pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv /build/.venv && \
    uv pip install --python /build/.venv/bin/python \
        "fastapi>=0.111.0" \
        "uvicorn[standard]>=0.30.0" \
        "httpx>=0.27.0" \
        "grpcio>=1.64.0" \
        "protobuf>=5.27.0" \
        "opentelemetry-api>=1.27.0" \
        "opentelemetry-sdk>=1.27.0" \
        "opentelemetry-instrumentation-fastapi>=0.48b0" \
        "opentelemetry-instrumentation-httpx>=0.48b0" \
        "opentelemetry-instrumentation-grpc>=0.48b0" \
        "opentelemetry-exporter-otlp>=1.27.0" \
        "opentelemetry-distro>=0.48b0" \
        "pydantic>=2.7.0"

# Layer 2: app source includes the committed generated and typed gRPC stubs.
COPY app ./app

# -- runtime ----------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app

# Non-root user
RUN groupadd --system --gid 1001 app && \
    useradd  --system --uid 1001 --gid app --no-create-home app

WORKDIR /app

# Copy the prepared venv + source
COPY --from=builder --chown=app:app /build/.venv /app/.venv
COPY --from=builder --chown=app:app /build/app    /app/app

USER app

EXPOSE 8003

# The application initializes FastAPI, HTTPX, and gRPC OTel instrumentation in its lifespan.
# Running modules avoids non-relocatable console-script shebangs from the builder venv.
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8003"]
