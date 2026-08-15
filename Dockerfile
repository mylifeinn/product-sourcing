# Dockerfile for Product Sourcing Pipeline
FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Set working directory
WORKDIR /app

# Copy dependency files first (for caching)
COPY pyproject.toml ./

# Install dependencies
RUN uv sync --frozen --no-dev

# Copy application code
COPY src/ ./src/
COPY templates/ ./templates/
COPY config.yaml ./

# Create data directory
RUN mkdir -p /app/data

# Expose port
EXPOSE 8085

# Default command
CMD ["uv", "run", "python", "-m", "sourcing.web.app"]