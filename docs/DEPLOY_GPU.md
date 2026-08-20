# GPU Worker Runtime Deployment Guide (Deepnote & Google Colab)

This document provides step-by-step instructions for deploying and running the **GPU Curation Worker** on **Deepnote** or **Google Colab**.

---

## 1. Deepnote Setup

1. **Create Project / Notebook**:
   * Open your Deepnote workspace and create a new project.
   * Under **Environment** / **Hardware**, select **GPU** (e.g. Nvidia T4, A10G, or L4).

2. **Environment Variables**:
   In your Deepnote project settings (Integrations -> Environment Variables), set:
   * `VPS_PUBLIC_HOST`: Public IP or DNS of your VPS control plane.
   * `DATABASE_URL`: `postgresql://gpu_worker:<PASSWORD>@<VPS_PUBLIC_HOST>:5432/knowledge_base?sslmode=require`
   * `CONTROL_API_KEY`: API Bearer key for FastAPI.
   * `EMBEDDING_MODEL_NAME`: `BAAI/bge-m3`

3. **Run Worker**:
   * Copy the code from [`notebooks/deepnote_worker.py`](../notebooks/deepnote_worker.py) into a notebook cell.
   * Toggle `CONTINUOUS_MODE = True` if you want it to continuously poll while your notebook is open, or `CONTINUOUS_MODE = False` for a single batch run.
   * Click **Run Cell** (`Shift + Enter`).

---

## 2. Pipeline Execution Steps

When executed, the worker automatically runs the following stages:
1. **Dependency Installation**: Auto-installs `FlagEmbedding`, `asyncpg`, `torch`, `umap-learn`, `hdbscan`, `litellm`.
2. **Project Discovery & Filtering**: Scans directories and filters boilerplate / build noise (`.git`, `node_modules`, `venv`, `lockfiles`).
3. **BGE-M3 Embeddings**: Encodes units with dense (1024d) + sparse embeddings on GPU and caches in PostgreSQL `embed_cache`.
4. **MinHash Deduplication**: Flags near-duplicate units and tombstones obsolete text.
5. **Topic Clustering**: Performs UMAP dimensionality reduction + HDBSCAN clustering.
6. **Consensus & Conflict Analysis**: Runs 3-way consensus and claims extraction.
