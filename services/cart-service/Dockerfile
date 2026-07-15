FROM python:3.12-slim@sha256:c3d81d25b3154142b0b42eb1e61300024426268edeb5b5a26dd7ddf64d9daf28

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.10.4@sha256:4cac394b6b72846f8a85a7a0e577c6d61d4e17fe2ccee65d9451a8b3c9efb4ac /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy application code.
COPY app ./app

RUN groupadd --system --gid 1001 app \
    && useradd --system --uid 1001 --gid app --home /app app \
    && chown -R app:app /app

USER app

EXPOSE 8000

# Make the virtual environment commands available on PATH so that
# opentelemetry-instrument can discover uvicorn.
ENV PATH="/app/.venv/bin:$PATH"

# Use the OpenTelemetry Python distro to auto-instrument FastAPI and requests.
CMD ["opentelemetry-instrument", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
