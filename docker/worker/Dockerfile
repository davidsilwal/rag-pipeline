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

# Pre-cache embedding model to avoid runtime downloads
ARG HF_TOKEN=""
ENV HF_HOME=/hf_cache \
    TRANSFORMERS_CACHE=/hf_cache \
    SENTENCE_TRANSFORMERS_HOME=/hf_cache
RUN mkdir -p /hf_cache && \
    python - <<'PY' || true
from huggingface_hub import snapshot_download
import os
token = os.getenv("HF_TOKEN", "")
try:
    snapshot_download(
        repo_id="BAAI/bge-m3",
        local_dir="/hf_cache/models--BAAI--bge-m3",
        local_dir_use_symlinks=False,
        resume_download=True,
        token=token or None,
    )
    print("✅ Pre-cached BAAI/bge-m3")
except Exception as e:
    print("⚠️ Model pre-cache failed:", e)
PY

# Default command: canonical multi-worker task loop (plan §9).
# The notebook path (notebooks/deepnote_worker.py) remains for Colab/Deepnote.
CMD ["python", "-m", "workers.runner"]