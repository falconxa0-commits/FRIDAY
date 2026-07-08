FROM python:3.11-slim

# System dependencies — minimal set (no torch, no sentence-transformers)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
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
