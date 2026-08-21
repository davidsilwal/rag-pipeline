FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    build-essential \
 && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt ./worker-requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r worker-requirements.txt

# Source
COPY . .

# Runtime defaults
ENV GPU_WORKER_CUDA=0
ENV CUDA_VISIBLE_DEVICES=
ENV EMBEDDING_MODEL_NAME=BAAI/bge-m3

HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD ["python", "-c", "import sys; sys.exit(0)"]

CMD ["python", "notebooks/deepnote_worker.py"]