# ============================================================
# 識（shiki）- AI Agent Docker Image
# ============================================================

# --- Stage 1: Builder ---
FROM python:3.12-slim AS builder

WORKDIR /build

COPY requirements.txt .

RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# --- Stage 2: Runtime ---
FROM python:3.12-slim

# System dependencies: Playwright browser deps + Japanese fonts
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Playwright Chromium dependencies
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxkbcommon0 \
    libatspi2.0-0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    libwayland-client0 \
    # Japanese font support
    fonts-noto-cjk \
    # Misc
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy installed Python packages from builder
COPY --from=builder /install /usr/local

# Install Playwright Chromium browser
RUN playwright install chromium

# Create non-root user
RUN groupadd --gid 1000 shiki \
    && useradd --uid 1000 --gid shiki --shell /bin/bash --create-home shiki

# Create directories that need to persist or be writable
RUN mkdir -p /app/.ritsu /app/logs /app/static/images \
    && chown -R shiki:shiki /app

# Copy project files
COPY --chown=shiki:shiki . .

USER shiki

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["python", "main.py"]
