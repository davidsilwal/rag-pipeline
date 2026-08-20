# Control API Dockerfile
# Builds FastAPI Control Plane application — re-exports from package root
FROM python:3.12-alpine

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

WORKDIR /app

RUN apk add --no-cache git postgresql-client

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir pgvector

# Copy entire source tree (package root IS the FastAPI app)
COPY . .

# Create wiki volume
RUN mkdir -p /var/data/wiki

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import requests; requests.get('http://localhost:8000/api/v1/health', timeout=5)"]

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]