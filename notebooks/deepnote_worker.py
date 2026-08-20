import os
import sys
import time
import asyncio
import inspect
import subprocess
import shutil
from pathlib import Path

# ==================================================
# 0. ENVIRONMENT & SECRETS CONFIGURATION
# ==================================================
try:
    import google.colab
    from google.colab import userdata
    HAS_COLAB = True
except Exception:
    HAS_COLAB = False

GITHUB_TOKEN = None
if HAS_COLAB:
    try:
        GITHUB_TOKEN = userdata.get("GITHUB_TOKEN")
    except Exception:
        pass
if not GITHUB_TOKEN:
    GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")


CONTINUOUS_MODE = False
POLL_INTERVAL_SECONDS = 30


# ==================================================
# 0.5. ENVIRONMENT VALIDATION
# ==================================================
REQUIRED_ENV_VARS = [
    "DATABASE_URL", "CONTROL_API_URL", "API_TOKEN",
    "AZURE_TENANT_ID", "AZURE_CLIENT_ID", 
    "AZURE_CLIENT_SECRET", "ONEDRIVE_DRIVE_ID"
]

missing_vars = [var for var in REQUIRED_ENV_VARS if not os.getenv(var)]
if missing_vars:
    print("⚠️ Warning: The following environment variables are not set:")
    for var in missing_vars:
        print(f"   - {var}")
    print("Some functionality may be limited. Please set these in Colab secrets or environment.")
    # We don't fail here because some variables might be optional for certain modes
    # but we warn the user.


# ==================================================
# 1. REPOSITORY CLONE & ENVIRONMENT SETUP
# ==================================================
def _setup_environment():
    """Set up the environment for Colab/Deepnote: clone repo if needed, fix paths."""
    IN_COLAB = HAS_COLAB
    IN_DEEPNOTE = os.path.exists("/work")

    repo_base = "github.com/davidsilwal/rag-pipeline.git"
    repo_url = f"https://{GITHUB_TOKEN}@{repo_base}" if GITHUB_TOKEN else f"https://{repo_base}"

    if IN_COLAB:
        print("🔧 Detected Google Colab environment")
        repo_dir = Path("/content/rag-pipeline")

        if repo_dir.exists():
            if (repo_dir / ".git").exists():
                print("✅ Valid repository found. Pulling latest changes...")
                try:
                    subprocess.run(["git", "-C", str(repo_dir), "pull"], check=True, capture_output=True, text=True)
                except subprocess.CalledProcessError as e:
                    print(f"⚠️ Git pull failed ({e.stderr.strip()}). Re-cloning...")
                    shutil.rmtree(repo_dir, ignore_errors=True)
            else:
                print(f"⚠️ Removing invalid directory at {repo_dir}...")
                shutil.rmtree(repo_dir, ignore_errors=True)

        if not repo_dir.exists():
            print("📥 Cloning repository...")
            clone_cmd = ["git", "clone", repo_url, str(repo_dir)]
            result = subprocess.run(clone_cmd, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"\n❌ Git clone failed (Exit Code {result.returncode}):")
                print(result.stderr)
                raise RuntimeError("Failed to clone repository.")

        os.chdir(repo_dir)
        if str(repo_dir) not in sys.path:
            sys.path.insert(0, str(repo_dir))
        print(f"📁 Working directory set to: {repo_dir}")
        return repo_dir

    elif IN_DEEPNOTE:
        print("🔧 Detected Deepnote environment")
        repo_dir = Path("/work").resolve()
        if not (repo_dir / "workers").exists():
            cwd = Path.cwd()
            if (cwd / "workers").exists():
                repo_dir = cwd
            else:
                for parent in [cwd] + list(cwd.parents):
                    if (parent / "workers").exists():
                        repo_dir = parent
                        break
        if str(repo_dir) not in sys.path:
            sys.path.insert(0, str(repo_dir))
        print(f"📁 Working directory set to: {repo_dir}")
        return repo_dir

    else:
        repo_dir = Path.cwd()
        if str(repo_dir) not in sys.path:
            sys.path.insert(0, str(repo_dir))
        return repo_dir

REPO_PATH = _setup_environment()


# ==================================================
# 2. AUTOMATIC DEPENDENCY SETUP
# ==================================================
print("\n📦 [1/5] Checking and installing dependencies...")
REQUIRED_PACKAGES = [
    "asyncpg", "FlagEmbedding", "torch", "transformers",
    "scikit-learn", "umap-learn", "hdbscan", "litellm",
    "pydantic", "httpx", "pyyaml", "psycopg2-binary", "tqdm", "nest_asyncio"
]

try:
    import asyncpg, FlagEmbedding, umap, hdbscan, nest_asyncio
    print("✅ Key dependencies already installed.")
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q"] + REQUIRED_PACKAGES)
    import nest_asyncio
    print("✅ Installation complete.")

nest_asyncio.apply()


# ==================================================
# 3. DYNAMIC DISCOVERY RESOLVER
# ==================================================
def resolve_discovery_execution(repo_path: Path):
    """Dynamically detects whether discovery is a function, class, or module."""
    try:
        import workers.gpu_worker.discovery as discovery_mod
    except ImportError as e:
        print(f"⚠️ Could not import discovery module: {e}")
        return None

    # 1. Direct function call matches
    for candidate_fn in ["discover_path", "discover_files", "discover_workspace", "scan_path", "discover"]:
        if hasattr(discovery_mod, candidate_fn):
            fn = getattr(discovery_mod, candidate_fn)
            if callable(fn):
                print(f"🔎 Found discovery function: `{candidate_fn}`")
                return fn(str(repo_path))

    # 2. Class-based matches
    for attr_name in dir(discovery_mod):
        if not attr_name.startswith("_"):
            obj = getattr(discovery_mod, attr_name)
            if inspect.isclass(obj):
                instance = obj()
                for method_name in ["discover_path", "discover_files", "scan", "run", "discover"]:
                    if hasattr(instance, method_name) and callable(getattr(instance, method_name)):
                        print(f"🔎 Found discovery class method: `{attr_name}.{method_name}`")
                        return getattr(instance, method_name)(str(repo_path))

    print("⚠️ No standard discovery entry point found. Available symbols:", [a for a in dir(discovery_mod) if not a.startswith("_")])
    return None


# ==================================================
# 4. PIPELINE BATCH EXECUTION
# ==================================================
async def process_batch_job():
    import workers.gpu_worker.embedder as embedder_mod
    import workers.gpu_worker.dedup as dedup_mod
    import workers.gpu_worker.clustering as clustering_mod
    import workers.gpu_worker.consensus as consensus_mod

    EmbedderClass = getattr(embedder_mod, "BGEM3Embedder", getattr(embedder_mod, "Embedder", None))
    run_dedup = getattr(dedup_mod, "run_dedup", getattr(dedup_mod, "dedup", None))
    run_clustering = getattr(clustering_mod, "run_clustering", getattr(clustering_mod, "cluster", None))
    run_consensus = getattr(consensus_mod, "run_consensus", getattr(consensus_mod, "consensus", None))

    db_url = os.environ["DATABASE_URL"]
    if "YOUR_VPS_IP_OR_DOMAIN" in db_url or "YOUR_PASSWORD" in db_url:
        print("⚠️ Error: DATABASE_URL contains placeholder values. Update Section 0 with actual PostgreSQL connection credentials.")
        return False

    print("\n🚀 [2/5] Connecting to PostgreSQL...")
    try:
        pool = await asyncpg.create_pool(db_url, min_size=1, max_size=5)
    except Exception as e:
        print(f"❌ Failed to connect to database: {e}")
        return False

    try:
        print("\n🔍 [3/5] Running discovery on workspace...")
        manifest = resolve_discovery_execution(REPO_PATH)

        if EmbedderClass:
            embedder = EmbedderClass(model_name=os.environ["EMBEDDING_MODEL_NAME"], use_gpu=True)
        else:
            raise ImportError("Could not locate BGEM3Embedder class in workers.gpu_worker.embedder")

        async with pool.acquire() as conn:
            print("⚡ [4/5] Processing discovered records...")
            sources = await conn.fetch("SELECT source_id, file_path FROM sources WHERE status = 'discovered' LIMIT 50")

            if not sources:
                print("ℹ️ No records with status 'discovered' found in `sources` table.")

            for src in sources:
                source_id = str(src["source_id"])
                units = await conn.fetch(
                    "SELECT unit_id, clean_text, content_hash FROM units WHERE source_id = $1",
                    src["source_id"]
                )
                if not units:
                    continue

                texts = [u["clean_text"] for u in units]
                embed_result = embedder.embed_batch(texts)
                if inspect.iscoroutine(embed_result):
                    dense_vecs, sparse_weights = await embed_result
                else:
                    dense_vecs, sparse_weights = embed_result

                for u, dense, sparse in zip(units, dense_vecs, sparse_weights):
                    await conn.execute(
                        "INSERT INTO embed_cache (content_hash, dense_vector, sparse_weights) "
                        "VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                        u["content_hash"], dense, sparse
                    )

                await conn.execute("UPDATE sources SET status = 'extracted' WHERE source_id = $1", src["source_id"])

                if run_dedup:
                    dedup_res = run_dedup(source_id)
                    if inspect.iscoroutine(dedup_res): await dedup_res

                if run_clustering:
                    cluster_res = run_clustering(source_id)
                    if inspect.iscoroutine(cluster_res): await cluster_res

            print("🧠 [5/5] Running consensus...")
            if run_consensus:
                cons_res = run_consensus()
                if inspect.iscoroutine(cons_res): await cons_res

        print("\n🎉 Batch processing completed successfully!")
        return True
    finally:
        await pool.close()


if __name__ == "__main__":
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            task = loop.create_task(process_batch_job())
        else:
            loop.run_until_complete(process_batch_job())
    except RuntimeError:
        asyncio.run(process_batch_job())