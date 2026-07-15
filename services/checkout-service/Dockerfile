# syntax=docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e
# Multi-stage build for checkout-service.
# Stage 1: build a venv with uv so we get deterministic, cached installs.
# Stage 2: copy the venv + app code into a slim runtime image.

# -- builder ----------------------------------------------------------------
FROM python:3.12-slim@sha256:c3d81d25b3154142b0b42eb1e61300024426268edeb5b5a26dd7ddf64d9daf28 AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy

# uv is the only build-time dep we need from the network.
COPY --from=ghcr.io/astral-sh/uv:0.10.4@sha256:4cac394b6b72846f8a85a7a0e577c6d61d4e17fe2ccee65d9451a8b3c9efb4ac /uv /usr/local/bin/uv

WORKDIR /build

# Layer 1: dependencies only. This stays cached when only app code changes.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Layer 2: app source includes the committed generated and typed gRPC stubs.
COPY app ./app

# -- runtime ----------------------------------------------------------------
FROM python:3.12-slim@sha256:c3d81d25b3154142b0b42eb1e61300024426268edeb5b5a26dd7ddf64d9daf28 AS runtime

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
