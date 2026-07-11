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
RUN groupadd --gid 1000 incidents && \
    useradd --uid 1000 --gid incidents --home /app incidents && \
    mkdir -p /data /postmortems && \
    chown -R incidents:incidents /app /data /postmortems

# Copy the virtual environment from the builder stage.
COPY --from=builder --chown=incidents:incidents /app/.venv ./.venv
COPY --chown=incidents:incidents app ./app

USER incidents

ENV PATH="/app/.venv/bin:$PATH" \
    OTEL_SERVICE_NAME="incident-engine" \
    OTEL_EXPORTER_OTLP_ENDPOINT="http://otel-collector.observability:4317" \
    OTEL_EXPORTER_OTLP_PROTOCOL="grpc" \
    OTEL_TRACES_EXPORTER="otlp" \
    OTEL_EXPORTER_OTLP_TRACES_TIMEOUT="2" \
    OTEL_METRICS_EXPORTER="none" \
    OTEL_LOGS_EXPORTER="otlp" \
    OTEL_EXPORTER_OTLP_LOGS_TIMEOUT="2" \
    PYTHONUNBUFFERED="1"

EXPOSE 8088

CMD ["opentelemetry-instrument", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8088"]
