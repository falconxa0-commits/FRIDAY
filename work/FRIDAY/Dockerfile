# ── Stage 1: Builder ──────────────────────────────────────────────────────────
# Includes build tools (gcc) that are NOT needed in the final image.
# This reduces the final image size and attack surface.
FROM python:3.11-slim AS builder

# Build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Install Python dependencies to a virtualenv that we can copy later
COPY requirements.txt .
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ── Stage 2: Runtime ──────────────────────────────────────────────────────────
# Minimal image — no gcc, no build tools, smaller attack surface.
FROM python:3.11-slim AS runtime

# Runtime dependencies only (curl for healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy the virtualenv from builder (includes all installed packages)
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Copy application code (respects .dockerignore)
COPY . .

# Create workspace directory
RUN mkdir -p /app/friday_workspace

# Create non-root user for security (prevents container escape → host root)
RUN addgroup --system friday && adduser --system --ingroup friday friday
RUN chown -R friday:friday /app
USER friday

# Environment defaults
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    BRAIN_PROVIDER=glm \
    AUTONOMY_PROFILE=GUEST \
    LOG_LEVEL=INFO

# Expose FastAPI port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run FastAPI by default
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
