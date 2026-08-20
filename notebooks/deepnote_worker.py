#!/usr/bin/env python3
"""
notebooks/deepnote_worker.py
==============================================================================
DEEPNOTE GPU WORKER PIPELINE — COMPLETE READY-TO-RUN NOTEBOOK / SCRIPT
==============================================================================
Designed for Deepnote (Student / Team) & Google Colab environments.
Connects to VPS PostgreSQL, executes Discovery -> Extraction -> Embedding -> 
Clustering -> Consensus -> Markdown Wiki Compilation -> pgvector Indexing.
==============================================================================
"""

import os
import sys
import subprocess
import asyncio
import time
from pathlib import Path

# ==============================================================================
# 1. ENVIRONMENT CONFIGURATION & SECRETS
# ==============================================================================
# Set your VPS connection details here, or in Deepnote's Environment Variables tab
VPS_HOST = os.getenv("VPS_PUBLIC_HOST", "YOUR_VPS_IP_OR_DOMAIN")
os.environ["VPS_PUBLIC_HOST"] = VPS_HOST
os.environ["CONTROL_API_URL"] = os.getenv("CONTROL_API_URL", f"https://{VPS_HOST}/api/v1")
os.environ["CONTROL_API_KEY"] = os.getenv("CONTROL_API_KEY", "YOUR_CONTROL_API_KEY")
os.environ["DATABASE_URL"] = os.getenv(
    "DATABASE_URL",
    f"postgresql://gpu_worker:YOUR_PASSWORD@{VPS_HOST}:5432/knowledge_base?sslmode=require"
)
os.environ["LOCAL_LLM_MODEL"] = os.getenv("LOCAL_LLM_MODEL", "Qwen/Qwen2.5-14B-Instruct-AWQ")
os.environ["LOCAL_LLM_API_BASE"] = os.getenv("LOCAL_LLM_API_BASE", "http://127.0.0.1:8000/v1")
os.environ["EMBEDDING_MODEL_NAME"] = os.getenv("EMBEDDING_MODEL_NAME", "BAAI/bge-m3")

# Optional: Microsoft Graph / OneDrive Credentials
os.environ["AZURE_TENANT_ID"] = os.getenv("AZURE_TENANT_ID", "")
os.environ["AZURE_CLIENT_ID"] = os.getenv("AZURE_CLIENT_ID", "")
os.environ["AZURE_CLIENT_SECRET"] = os.getenv("AZURE_CLIENT_SECRET", "")
os.environ["ONEDRIVE_DRIVE_ID"] = os.getenv("ONEDRIVE_DRIVE_ID", "")

# Mode: Set to True for continuous background polling loop, False for single batch run
CONTINUOUS_MODE = False
POLL_INTERVAL_SECONDS = 30


# ==============================================================================
# 2. AUTOMATIC DEPENDENCY SETUP
# ==============================================================================
print("📦 [1/5] Checking and installing GPU dependencies...")
REQUIRED_PACKAGES = [
    "asyncpg",
    "FlagEmbedding",
    "torch",
    "transformers",
    "scikit-learn",
    "umap-learn",
    "hdbscan",
    "litellm",
    "pydantic",
    "httpx",
    "pyyaml",
    "psycopg2-binary",
    "tqdm"
]

try:
    import asyncpg
    import FlagEmbedding
    import umap
    import hdbscan
    print("✅ Key dependencies already installed.")
except ImportError:
    print("⏳ Installing missing packages via pip...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q"] + REQUIRED_PACKAGES)
    print("✅ Installation complete.")


# =============================================================================
# 3. WORKSPACE & REPOSITORY PATH SETUP
# =============================================================================
REPO_PATH = Path("/work").resolve()  # Deepnote default root
if not (REPO_PATH / "workers").exists():
    # Fallback: use current working directory if it contains the repo
    cwd = Path.cwd().resolve()
    if (cwd / "workers").exists():
        REPO_PATH = cwd
    else:
        # Last resort: parent of this file or cwd
        REPO_PATH = Path(__file__).resolve().parent.parent if "__file__" in globals() else cwd

if str(REPO_PATH) not in sys.path:
    sys.path.insert(0, str(REPO_PATH))

print(f"📁 [2/5] Repository root set to: {REPO_PATH}")


# ==============================================================================
# 4. PIPELINE BATCH EXECUTION
# ==============================================================================
async def process_batch_job():
    """Executes a single end-to-end extraction and embedding batch against VPS."""
    import asyncpg
    from workers.gpu_worker.discovery import discover_path
    from workers.gpu_worker.embedder import BGEM3Embedder
    from workers.gpu_worker.dedup import run_dedup
    from workers.gpu_worker.clustering import run_clustering
    from workers.gpu_worker.consensus import run_consensus
    from workers.gpu_worker.claims_conflicts import run_claims_and_conflicts

    db_url = os.environ["DATABASE_URL"]
    if "YOUR_VPS_IP_OR_DOMAIN" in db_url or "YOUR_PASSWORD" in db_url:
        print("⚠️ Warning: Please replace default placeholders with your actual VPS DATABASE_URL!")
        return False

    print("\n🚀 [3/5] Connecting to VPS PostgreSQL...")
    try:
        pool = await asyncpg.create_pool(db_url, min_size=1, max_size=5)
        print("✅ Database pool connected.")
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        return False

    try:
        # Step A: Local Discovery
        print("\n🔍 [4/5] Scanning workspace files...")
        manifest = discover_path(str(REPO_PATH))
        print(f"   -> Discovered {len(manifest)} valid files after applying noise filters.")

        # Step B: Initialize Model
        print(f"\n🧠 Initializing {os.environ['EMBEDDING_MODEL_NAME']} on GPU...")
        embedder = BGEM3Embedder(
            model_name=os.environ["EMBEDDING_MODEL_NAME"],
            batch_size=32,
            use_gpu=True
        )

        # Step C: Fetch Queued Sources
        async with pool.acquire() as conn:
            sources = await conn.fetch(
                "SELECT source_id, file_path FROM sources WHERE status = 'discovered' ORDER BY created_at ASC LIMIT 50"
            )
            
            if not sources:
                print("ℹ️ No pending 'discovered' sources found in queue.")
                return True

            print(f"   -> Processing {len(sources)} queued sources...")

            for src in sources:
                source_id = str(src["source_id"])
                file_path = src["file_path"]
                print(f"\n📄 Processing: {file_path}")

                units = await conn.fetch(
                    "SELECT unit_id, clean_text, content_hash FROM units WHERE source_id = $1 ORDER BY unit_index ASC",
                    src["source_id"]
                )

                if not units:
                    print(f"   ⚠️ No canonical units extracted yet for source {source_id}, skipping embedding.")
                    continue

                texts = [u["clean_text"] for u in units]
                dense_vecs, sparse_weights = await embedder.embed_batch(texts)

                # Upsert into embed_cache
                for u, dense, sparse in zip(units, dense_vecs, sparse_weights):
                    await conn.execute(
                        """
                        INSERT INTO embed_cache (content_hash, dense_vector, sparse_weights)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (content_hash) DO NOTHING
                        """,
                        u["content_hash"],
                        dense,
                        sparse
                    )

                # Mark source as extracted/indexed
                await conn.execute(
                    "UPDATE sources SET status = 'extracted', updated_at = NOW() WHERE source_id = $1",
                    src["source_id"]
                )

                # Deduplication & Topic Clustering
                await run_dedup(source_id)
                clusters = await run_clustering(source_id)
                print(f"   ✅ Embedded {len(units)} units, formed {len(clusters)} topic clusters.")

                # Run 3-Way Consensus & Conflict detection
                await run_consensus()
                await run_claims_and_conflicts("general", units)

        print("\n🎉 [5/5] Batch processing cycle completed successfully!")
        return True

    finally:
        await pool.close()


# ==============================================================================
# 5. ENTRYPOINT & RUNNER LOOP
# ==============================================================================
async def main():
    if CONTINUOUS_MODE:
        print(f"🤖 Starting GPU Worker in continuous background polling mode (every {POLL_INTERVAL_SECONDS}s)...")
        print("💡 Press Stop / Interrupt in Deepnote anytime to halt execution.")
        while True:
            try:
                print(f"\n--- [{time.strftime('%Y-%m-%d %H:%M:%S')}] Polling for tasks ---")
                await process_batch_job()
            except Exception as exc:
                print(f"⚠️ Error during poll cycle: {exc}")
            
            print(f"⏳ Sleeping for {POLL_INTERVAL_SECONDS}s...")
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
    else:
        print("⚡ Running GPU Worker in single-run batch mode...")
        await process_batch_job()


if __name__ == "__main__":
    # Fix for Colab/Jupyter: nest_asyncio patches the running event loop
    try:
        import nest_asyncio
        nest_asyncio.apply()
    except ImportError:
        import subprocess, sys
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "nest_asyncio"])
        import nest_asyncio
        nest_asyncio.apply()
    asyncio.run(main())
