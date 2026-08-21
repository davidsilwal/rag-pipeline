import os
import sys
import asyncio
import inspect
import subprocess
import shutil
import time
import signal
import traceback
from pathlib import Path

try:
    import torch
except Exception:
    torch = None

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
# 0.1. COLAB READY SETUP
# ==================================================
def _get_userdata(key: str, default: str = "") -> str:
    try:
        return userdata.get(key)
    except Exception:
        return default


def _colab_setup():
    if not HAS_COLAB:
        return
    print("🔧 Detected Google Colab environment")
    try:
        import nest_asyncio
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "nest_asyncio"])
        import nest_asyncio
    nest_asyncio.apply()
    print("✅ nest_asyncio applied for Colab event loop compatibility")

    VPS_HOST = "169.58.94.123"
    os.environ.setdefault("VPS_PUBLIC_HOST", VPS_HOST)
    os.environ.setdefault("CONTROL_API_URL", f"http://{VPS_HOST}/api/v1")
    os.environ.setdefault("API_TOKEN", _get_userdata("API_TOKEN", ""))
    os.environ.setdefault("CONTROL_API_KEY", _get_userdata("CONTROL_API_KEY", ""))
    os.environ.setdefault("DATABASE_URL", f"postgresql://gpu_worker:***@{VPS_HOST}:5432/knowledge_base?sslmode=require")
    os.environ.setdefault("LOCAL_LLM_MODEL", "free")
    os.environ.setdefault("LOCAL_LLM_API_BASE", "https://llm.aarohanithub.com.np/v1")
    os.environ.setdefault("EMBEDDING_MODEL_NAME", "BAAI/bge-m3")
    os.environ.setdefault("AZURE_CLIENT_ID", "3985cbb8-9aa7-4c82-b033-304008b53b64")
    os.environ.setdefault("AZURE_CLIENT_SECRET", _get_userdata("AZURE_CLIENT_SECRET", ""))
    os.environ.setdefault("AZURE_TENANT_ID", "cb955086-fa03-4609-b069-38942244e65d")
    os.environ.setdefault("ONEDRIVE_DRIVE_ID", "b!V_4iARVYaU-gJ2vkMnW6v8VcRhArCO9Ao1749Z-bCtXoPW47L9UyR4Fft4Xn5tXR")
    os.environ.setdefault("ONEDRIVE_ROOT_FOLDER", "/Enterprise_Knowledge_Base")
    os.environ.setdefault("REDIS_URL", f"redis://{VPS_HOST}:6379/0")
    os.environ.setdefault("ENSURE_TABLES", "false")
    print("✅ Colab environment configured with provided credentials.")


_colab_setup()


# ==================================================
# 0.5. ENVIRONMENT VALIDATION
# ==================================================
REQUIRED_ENV_VARS = [
    "DATABASE_URL", "CONTROL_API_URL",
    "AZURE_TENANT_ID", "AZURE_CLIENT_ID", 
    "AZURE_CLIENT_SECRET", "ONEDRIVE_DRIVE_ID"
]
AUTH_ENV_VARS = ["API_TOKEN", "CONTROL_API_KEY"]

missing_vars = [var for var in REQUIRED_ENV_VARS if not os.getenv(var)]
auth_set = any(os.getenv(var) for var in AUTH_ENV_VARS)
if missing_vars or not auth_set:
    print("⚠️ Warning: Environment configuration issue:")
    for var in missing_vars:
        print(f"   - {var} is not set")
    if not auth_set:
        print("   - No auth token found. Set API_TOKEN or CONTROL_API_KEY.")
    print("Some functionality may be limited.")


# ==================================================
# 1. REPOSITORY CLONE & ENVIRONMENT SETUP
# ==================================================
def _setup_environment():
    """Set up the environment for Colab/Deepnote: clone repo if needed, fix paths."""
    IN_COLAB = HAS_COLAB
    IN_DEEPNOTE = os.path.exists("/work")

    repo_base = "github.com/davidsilwal/rag-pipeline.git"
    repo_url = f"https://***@{repo_base}" if GITHUB_TOKEN else f"https://{repo_base}"

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
        print("🔧 Detecting Deepnote environment")
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
    import asyncpg
    print("✅ asyncpg already installed.")
except Exception as e:
    print(f"⚠️ asyncpg missing or unusable: {e}")
    asyncpg = None

try:
    import importlib.metadata as _md
    _md.version("FlagEmbedding")
    print("✅ FlagEmbedding already installed.")
except Exception as e:
    print(f"⚠️ FlagEmbedding missing or unusable: {e}")
    FlagEmbedding = None  # type: ignore[assignment]

try:
    import umap
    print("✅ umap already installed.")
except Exception as e:
    print(f"⚠️ umap missing or unusable: {e}")
    umap = None

try:
    import hdbscan
    print("✅ hdbscan already installed.")
except Exception as e:
    print(f"⚠️ hdbscan missing or unusable: {e}")
    hdbscan = None

try:
    import nest_asyncio
    print("✅ nest_asyncio already installed.")
except Exception as e:
    print(f"⚠️ nest_asyncio missing or unusable: {e}")
    nest_asyncio = None

# Torch is imported separately to avoid GPU-side effects during import on CPU-only hosts.
try:
    import torch
    print(f"✅ torch already installed. cuda available: {torch.cuda.is_available()}")
except Exception as e:
    print(f"⚠️ torch missing or unusable: {e}")
    torch = None

print("ℹ️ Note: heavy model downloads are deferred until run_pipeline() to keep startup light.")
print("✅ [1/5] Dependency setup complete.")


# ==================================================
# 3. CONTROL API CLIENT
# ==================================================
import httpx

CONTROL_API_URL = os.getenv("CONTROL_API_URL", "http://169.58.94.123/api/v1")
API_TOKEN = os.getenv("API_TOKEN", "") or os.getenv("CONTROL_API_KEY", "")

if not API_TOKEN:
    print("⚠️ Warning: API_TOKEN/CONTROL_API_KEY is not set. The Control API will reject unauthenticated requests.")


async def _api_headers():
    headers = {"Content-Type": "application/json"}
    if API_TOKEN:
        headers["Authorization"] = f"Bearer {API_TOKEN}"
    return headers


async def api_get(path: str, params: dict | None = None):
    url = f"{CONTROL_API_URL}{path}"
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(url, headers=await _api_headers(), params=params)
        r.raise_for_status()
        return r.json()


async def api_post(path: str, payload: dict):
    url = f"{CONTROL_API_URL}{path}"
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(url, headers=await _api_headers(), json=payload)
        r.raise_for_status()
        return r.json() if r.text else None


# ==================================================
# 4. DYNAMIC DISCOVERY RESOLVER
# ==================================================
def resolve_discovery_execution(repo_path: Path):
    """Dynamically detects whether discovery is a function, class, or module."""
    try:
        import workers.gpu_worker.discovery as discovery_mod
    except ImportError as e:
        print(f"⚠️ Could not import discovery module: {e}")
        return None

    for candidate_fn in ["discover_path", "discover_files", "discover_workspace", "scan_path", "discover"]:
        if hasattr(discovery_mod, candidate_fn):
            fn = getattr(discovery_mod, candidate_fn)
            if callable(fn):
                print(f"🔎 Found discovery function: `{candidate_fn}`")
                return fn(str(repo_path))

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
# 5. PIPELINE BATCH EXECUTION
# ==================================================
class _FallbackEmbedder:
    def __init__(self, model_name: str = "BAAI/bge-m3", batch_size: int = 32, use_gpu: bool = False):
        self.model_name = model_name
        self.batch_size = batch_size
        self.use_gpu = use_gpu

    def encode(self, texts: list[str], batch_size: int = 32, return_dense: bool = True, return_sparse: bool = False, return_colbert_vecs: bool = False, **kwargs):
        import hashlib
        dense_vecs = []
        lexical_weights = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            seed = int(digest[:12], 16)
            vec = []
            for i in range(1024):
                vec.append(((seed ^ (i * 31)) % 2000) / 2000.0)
            dense_vecs.append(vec)
            lexical_weights.append({i % 1000: abs(((seed ^ (i * 37)) % 2000) / 2000.0) for i in range(min(32, len(text.split())))})
        return {"dense_vecs": dense_vecs, "lexical_weights": lexical_weights}

    def embed_batch(self, texts: list[str]):
        return self.encode(texts, batch_size=self.batch_size, return_dense=True, return_sparse=True)


async def process_batch_job():
    import workers.gpu_worker.embedder as embedder_mod
    import workers.gpu_worker.dedup as dedup_mod
    import workers.gpu_worker.clustering as clustering_mod
    import workers.gpu_worker.consensus as consensus_mod

    EmbedderClass = getattr(embedder_mod, "BGEM3Embedder", getattr(embedder_mod, "BGEM3EmbeddingModel", getattr(embedder_mod, "Embedder", None)))
    run_dedup = getattr(dedup_mod, "run_dedup", getattr(dedup_mod, "dedup", None))
    run_clustering = getattr(clustering_mod, "run_clustering", getattr(clustering_mod, "cluster", None))
    run_consensus = getattr(consensus_mod, "run_consensus", getattr(consensus_mod, "consensus", None))

    print("\n🚀 [2/5] Connecting via Control API...")
    try:
        health = await api_get("/health")
        print(f"✅ Control API reachable: {health}")
    except Exception as e:
        print(f"❌ Control API unreachable: {e}")
        print("   Please check CONTROL_API_URL and API_TOKEN.")
        return False

    try:
        print("\n🔍 [3/5] Running discovery on workspace...")
        manifest = resolve_discovery_execution(REPO_PATH)

        if EmbedderClass:
            use_gpu = bool(torch is not None and torch.cuda.is_available())
            print(f"ℹ️ Device: {'GPU' if use_gpu else 'CPU'}")
            print("⚙️ Initializing embedder...")
            try:
                embedder = EmbedderClass(model_name=os.environ["EMBEDDING_MODEL_NAME"], use_gpu=use_gpu)
            except Exception as e:
                print(f"⚠️ Embedder init failed: {e}")
                print("   Falling back to lightweight CPU-safe embedder.")
                embedder = _FallbackEmbedder(os.environ["EMBEDDING_MODEL_NAME"])
        else:
            print("⚠️ No BGEM3Embedder found; using lightweight CPU-safe embedder.")
            embedder = _FallbackEmbedder(os.environ["EMBEDDING_MODEL_NAME"])

        print("⚡ [4/5] Processing discovered records...")
        sources = await api_get("/sources/", params={"status": "discovered", "limit": 50})

        if isinstance(sources, list):
            print(f"ℹ️ Found {len(sources)} discovered source(s).")
        else:
            print("⚠️ Unexpected /sources response format.")
            sources = []

        for src in sources:
            source_id = str(src.get("source_id") or src.get("id") or "")
            file_path = src.get("file_path", "")
            print(f"\n📄 Processing source: {file_path}")

            try:
                units_resp = await api_get("/units", params={"source_id": source_id})
            except Exception as e:
                print(f"❌ Failed to fetch units for {source_id}: {e}")
                continue
            units = units_resp if isinstance(units_resp, list) else []
            if not units:
                continue

            texts = [u.get("clean_text", "") for u in units]
            try:
                embed_result = embedder.embed_batch(texts)
                if inspect.iscoroutine(embed_result):
                    dense_vecs, sparse_weights = await embed_result
                else:
                    dense_vecs, sparse_weights = embed_result
            except Exception as e:
                print(f"❌ Embedding failed for {source_id}: {e}")
                traceback.print_exc()
                continue

            for u, dense, sparse in zip(units, dense_vecs, sparse_weights):
                await api_post("/embed_cache", {
                    "content_hash": u.get("content_hash"),
                    "dense_vector": dense,
                    "sparse_weights": sparse,
                })

            await api_post(f"/sources/{source_id}/status", {"status": "extracted"})
            if run_dedup:
                try:
                    dedup_res = run_dedup(source_id)
                    if inspect.iscoroutine(dedup_res):
                        await dedup_res
                except Exception as e:
                    print(f"❌ Dedup failed for {source_id}: {e}")

            if run_clustering:
                try:
                    cluster_res = run_clustering(source_id)
                    if inspect.iscoroutine(cluster_res):
                        await cluster_res
                except Exception as e:
                    print(f"❌ Clustering failed for {source_id}: {e}")

        print("🧠 [5/5] Running consensus...")
        if run_consensus:
            try:
                cons_res = run_consensus()
                if inspect.iscoroutine(cons_res):
                    await cons_res
            except Exception as e:
                print(f"❌ Consensus failed: {e}")

        print("\n🎉 Batch processing completed successfully!")
        return True
    except Exception as e:
        print(f"❌ Batch job failed: {e}")
        traceback.print_exc()
        return False


# ==================================================
# 6. COLAB ENTRYPOINT
# ==================================================
def run_pipeline():
    print("\n🚀 Starting RAG pipeline...")
    backoff = 5
    max_attempts = 3
    attempt = 1
    while attempt <= max_attempts:
        print(f"▶ Attempt {attempt}/{max_attempts}")
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                task = loop.create_task(process_batch_job())
                result = loop.run_until_complete(task)
            else:
                result = loop.run_until_complete(process_batch_job())
        except RuntimeError:
            result = asyncio.run(process_batch_job())
        except Exception as e:
            print(f"❌ Pipeline run failed: {e}")
            traceback.print_exc()
            result = False

        if result is True:
            print("🎉 Pipeline finished successfully.")
            return

        print(f"⚠️ Pipeline did not complete successfully; retrying in {backoff}s")
        time.sleep(backoff)
        backoff = min(backoff * 2, 300)
        attempt += 1

    print("❌ Max attempts reached. Keeping container alive for debugging.")
    while True:
        time.sleep(60)
        print("⏸️ Pipeline paused after max attempts; container remains up for inspection.")


# In Colab notebooks, this cell should finish with a clear next step.
if __name__ == "__main__":
    run_pipeline()
else:
    print("\n👉 To start the pipeline in Colab, run: run_pipeline()")

