FROM python:3.12-slim

WORKDIR /app

# Install build tools required by uvicorn[standard] (uvloop/httptools).
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install uv.
RUN pip install --no-cache-dir uv

# Install dependencies into a virtual environment.
COPY pyproject.toml .
RUN uv venv && uv pip install -r pyproject.toml

# Copy application code.
COPY app ./app

EXPOSE 8000

# Make the virtual environment commands available on PATH so that
# opentelemetry-instrument can discover uvicorn.
ENV PATH="/app/.venv/bin:$PATH"

# Use the OpenTelemetry Python distro to auto-instrument FastAPI and requests.
CMD ["opentelemetry-instrument", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
