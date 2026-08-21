FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/workspace
ENV PIP_NO_CACHE_DIR=1

WORKDIR /workspace

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY worker-requirements.txt /tmp/worker-requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /tmp/worker-requirements.txt

# Default command: start pipeline
CMD ["python", "-c", "from notebooks.deepnote_worker import run_pipeline; run_pipeline()"]