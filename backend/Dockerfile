# Switch from Alpine to Debian-based image for Cursor compatibility
FROM ghcr.io/astral-sh/uv:python3.11-bookworm

ENV ENV_MODE production
WORKDIR /app

# Install system dependencies including bash and development tools
RUN apt-get update && apt-get install -y \
    bash \
    curl \
    git \
    wget \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Set bash as default shell for better dev container compatibility
SHELL ["/bin/bash", "-c"]
ENV SHELL=/bin/bash

# Install Python dependencies
COPY pyproject.toml uv.lock ./
ENV UV_LINK_MODE=copy
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --quiet

# Copy application code
COPY . .

# Calculate optimal worker count based on 16 vCPUs
# Using (2*CPU)+1 formula for CPU-bound applications
ENV WORKERS=7
ENV THREADS=2
ENV WORKER_CONNECTIONS=2000

ENV PYTHONPATH=/app
EXPOSE 8000

# Gunicorn configuration with bash for better compatibility
CMD ["bash", "-c", "uv run gunicorn api:app \
  --workers $WORKERS \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8000 \
  --timeout 1800 \
  --graceful-timeout 600 \
  --keep-alive 1800 \
  --max-requests 0 \
  --max-requests-jitter 0 \
  --forwarded-allow-ips '*' \
  --worker-connections $WORKER_CONNECTIONS \
  --worker-tmp-dir /dev/shm \
  --preload \
  --log-level info \
  --access-logfile - \
  --error-logfile - \
  --capture-output \
  --enable-stdio-inheritance \
  --threads $THREADS"]