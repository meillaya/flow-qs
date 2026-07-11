# syntax=docker/dockerfile:1

# Build stage: install dependencies with uv.
FROM python:3.12-slim AS builder

WORKDIR /app

# Install uv and copy project metadata.
RUN pip install --no-cache-dir uv==0.10.4

COPY pyproject.toml uv.lock README.md ./
COPY app ./app

# Create a virtual environment and install only runtime dependencies.
RUN uv sync --no-dev --no-install-project

# Runtime stage: slim Python image with the venv copied in.
FROM python:3.12-slim AS runtime

WORKDIR /app

# Create a non-root user.
RUN groupadd --gid 1000 synth && \
    useradd --uid 1000 --gid synth --home /app synth && \
    chown -R synth:synth /app

# Copy the virtual environment from the builder stage.
COPY --from=builder --chown=synth:synth /app/.venv ./.venv
COPY --chown=synth:synth app ./app

USER synth

ENV PATH="/app/.venv/bin:$PATH" \
    OTEL_SERVICE_NAME="synthetic-monitor" \
    OTEL_EXPORTER_OTLP_ENDPOINT="http://otel-collector.observability:4317" \
    OTEL_EXPORTER_OTLP_PROTOCOL="grpc" \
    OTEL_TRACES_EXPORTER="otlp" \
    OTEL_METRICS_EXPORTER="otlp" \
    OTEL_LOGS_EXPORTER="otlp" \
    PYTHONUNBUFFERED="1"

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
