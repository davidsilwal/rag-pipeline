FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential git \
 && rm -rf /var/lib/apt/lists/*

COPY worker-requirements.txt /tmp/worker-requirements.txt
RUN pip install --prefix=/install -r /tmp/worker-requirements.txt

# Runtime
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV GPU_WORKER_CUDA=0
ENV CUDA_VISIBLE_DEVICES=
ENV EMBEDDING_MODEL_NAME=BAAI/bge-m3

WORKDIR /app

COPY --from=builder /install /usr/local
COPY notebooks /app/notebooks
COPY workers /app/workers
COPY migrations /app/migrations
COPY docker /app/docker
COPY worker-requirements.txt /app/worker-requirements.txt
COPY rag-policies /app/policies

# Keep image minimal; no build tools in final stage
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD ["python", "-c", "import sys; sys.exit(0)"]

CMD ["python", "-c", "from notebooks.deepnote_worker import run_pipeline; run_pipeline()"]