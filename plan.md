---
name: LLM Markdown Wiki Pipeline
overview: Self-contained architecture and implementation plan for a curated Markdown knowledge base. Small VPS (Postgres/Prefect/API by public IP) + Colab/Deepnote GPU worker (Graph OneDrive, extract, BGE-M3, GraphRAG, compile). No home PC, VPN, rclone, or VPS embedding inference.
isProject: false
---

# Enterprise Curated Knowledge Base — Production-Grade Plan

**Status:** Ready for implementation by any engineering agent  
**Version:** 2.2  
**Date:** 2026-08-20  

> **v2.2 changelog:** Fixed destructive recovery state machine (Edit 3), replaced committed secrets with placeholders (Edit 1), added Postgres TLS cert init (Edit 2), made `dense_vector` nullable so FTS works GPU-offline (Edit 4), moved HNSW index build after bulk load (Edit 5), added idempotency constraints + FK fixes (Edit 6), delta-token transactional ordering (Edit 7), multi-language FTS (Edit 8), moved tuning constants to `policies/` config (Edit 9), added GPU cost/latency budget (Edit 10), per-platform secret stores (Edit 11), Prefect exposure as accepted risk (Edit 12), Backup & DR (Edit 13), Git publish batching + LFS (Edit 14), reuse-vs-build note (Edit 15), observability (Edit 16), section renumber (Edit 17).

**Primary goal:** Convert heterogeneous unstructured corporate content (PDF, DOC/DOCX, MD, TXT, email/EML, chat, GitHub PRs/issues, tickets, FRD/SRS/ERD, diagrams, code, research, wiki) into a governed, deduplicated, high-fidelity **Git Markdown wiki** optimized for LLMs, with GraphRAG-organized topics, heading-topic consensus, and pgvector hybrid retrieval — strictly **without paid LLM or embedding APIs**.

---

## 1. Locked Decisions (Do Not Reopen)

| Decision | Choice / Constraint | Rationale / Operational Rule |
|---|---|---|
| **Corpus size** | Under 100 GB raw unstructured | Sized for medium-to-large enterprise team archives. |
| **External SaaS APIs allowed** | **OneDrive / Microsoft Graph only** (+ VPS host) | Zero external data leakage beyond enterprise tenant. |
| **Paid LLM / embed APIs** | **Strictly Forbidden** | No OpenAI, Anthropic, Azure OpenAI, Google AI Studio, Cohere, etc. |
| **Control plane** | **Small VPS** (4 GB RAM, 2 vCPU, 80–120 GB SSD) | Durable state: Postgres, pgvector, Prefect Server, Redis, FastAPI, Git wiki, FTS. |
| **Heavy compute** | **Google Colab or Deepnote GPU notebook** | Ephemeral, elastic GPU compute for extract, embed, cluster, GraphRAG, compile. |
| **Home PC** | **Not used** | Fully cloud/hosted; zero dependency on local workstation hardware. |
| **Networking** | **No Tailscale, no WireGuard** | Connect via **VPS public IP** + TLS/SSL + strong bearer secrets. |
| **File sync tool** | **rclone is Forbidden** | All OneDrive I/O strictly via Microsoft Graph API inside GPU notebook. |
| **VPS compute limits** | **No ML/Embedding inference on VPS** | VPS stores vectors & runs pgvector/FTS queries only; GPU computes embeddings. |
| **OneDrive on VPS** | **Forbidden** | VPS never holds Microsoft Graph credentials or OneDrive sync daemons. |
| **Source of truth** | **Git Markdown wiki** on VPS | Human-readable, version-controlled, auditable, LLM-optimized Markdown files. |
| **Index durability** | **Disposable projections** | Vector/Graph/FTS tables can be dropped and rebuilt from Git wiki anytime. |
| **Topic organization** | **3-Way Consensus** | HDBSCAN + GraphRAG Leiden communities + Heading-Topic correlation matrix. |
| **Embedding model** | **BAAI/bge-m3** (Dense 1024d + Sparse + Multi-Vector) | Runs exclusively on GPU worker; cached in Postgres by SHA-256 hash. |
| **LLM inference on GPU** | **LiteLLM → local vLLM / Ollama** (OpenAI-compatible) | Sized by GPU VRAM (Qwen2.5-7B/14B/32B or Llama-3.1-8B). |

---

## 2. Architecture Summary & System Topology

```text
               +---------------------------------------------+
               |        OneDrive (Raw Evidence Store)        |
               |  (PDF, DOCX, XLSX, MSG, MD, Scans, FRD/SRS)  |
               +---------------------------------------------+
                                      ^
                                      | Microsoft Graph API (HTTPS / OAuth2)
                                      v
+-----------------------------------------------------------------------------------+
| Colab / Deepnote GPU Worker (Ephemeral Compute Pool: 'gpu-curation')              |
|                                                                                   |
|  0. Discovery:  Recursive Walk -> Fingerprint -> Classify -> Noise Filter (§7)       |
|  1. Ingestion:   Microsoft Graph Delta Crawl -> MIME Detection -> ClamAV/Scan     |
|  2. Extraction:  Docling / MarkItDown / Tika / OCRmyPDF -> Canonical JSON Blocks |
|  3. Deduplication: SHA-256 Exact -> MinHash/SimHash LSH -> Near-Dup Matrix        |
|  4. Embedding:   BAAI/bge-m3 (FlagEmbedding) -> 1024d Dense + Sparse Lexical      |
|  5. Clustering:  UMAP + HDBSCAN -> Centroid & c-TF-IDF Topic Extraction           |
|  6. GraphRAG:    MS GraphRAG OSS via LiteLLM -> Entity/Relation Extraction        |
|  7. Consensus:   3-Way Matrix: HDBSCAN (0.40) + GraphRAG (0.35) + Headings (0.25)|
|  8. Compilation: Lossless Markdown Synthesizer (Coverage >=95%, 100% Citations)  |
|  9. Chunk Index: Heading-Aware Markdown Chunker -> bge-m3 -> Upsert to VPS       |
+-----------------------------------------------------------------------------------+
                                      |
                                      | Direct TCP (TLS Required, Bearer Tokens)
                                      | Ports: 5432 (Postgres), 4200 (Prefect), 443 (FastAPI)
                                      v
+-----------------------------------------------------------------------------------+
| Small VPS (2 vCPU, 4GB RAM, 100GB SSD) — Always-On Control Plane                  |
|                                                                                   |
|  +---------------------+   +--------------------+   +---------------------------+ |
|  | PostgreSQL 16       |   | Prefect 3.x Server |   | FastAPI Control API       | |
|  | - pgvector (HNSW)   |   | - Flow Queues      |   | - Validation & Webhooks   | |
|  | - pg_trgm & FTS GIN |   | - Work Pool UI     |   | - Caddy Reverse Proxy     | |
|  | - Metadata Registry |   | - Heartbeat Check  |   | - Hybrid RRF Searcher     | |
|  +---------------------+   +--------------------+   +---------------------------+ |
|                                      |                                            |
|                                      v                                            |
|  +------------------------------------------------------------------------------+ |
|  | Git Markdown Wiki Repository (/var/data/wiki) — True Knowledge Source        |
|  | - Human-readable, version-controlled domain, system, and requirement docs     |
|  +------------------------------------------------------------------------------+ |
+-----------------------------------------------------------------------------------+
                                      |
                                      v
+-----------------------------------------------------------------------------------+
| Downstream Consumers (OpenWiki / Internal Agents / Read-Only Client Apps)         |
|  - Queries Control API: FTS (default) or Hybrid Search (supplying query vector)  |
|  - Reads Git Markdown repository directly for continuous fine-tuning/RAG context  |
+-----------------------------------------------------------------------------------+
```

---

## 3. Five-Tier Layer Model & Provenance Chain

```text
[Layer 1: Immutable Evidence]   OneDrive raw files + Graph metadata + SHA-256 + ETag
             |
             v
[Layer 2: Canonical Units]      Normalized block JSON (page, bbox, char spans, headings)
             |
             v
[Layer 3: Analysis Ledger]      Embeddings, MinHash clusters, GraphRAG entities, conflicts
             |
             v
[Layer 4: Curated Git Wiki]     Authoritative Markdown pages, cited facts, frontmatter
             |
             v
[Layer 5: Retrieval Projections] pgvector HNSW chunks + PostgreSQL FTS (Rebuildable)
```

1. **Layer 1: Immutable Evidence**: Raw bytes stored in OneDrive. Identified by Microsoft Graph `item_id`, `eTag`, file path, and SHA-256 content hash. Never mutated.
2. **Layer 2: Canonical Structured Units**: Atomic content blocks extracted from sources (paragraphs, tables, code blocks, diagram descriptions) with exact provenance metadata (page number, bounding box, character spans, heading breadcrumb hierarchy).
3. **Layer 3: Analysis & Governance Ledger**: Deduplication clusters, disposition classifications (`authoritative`, `supporting`, `superseded`, `conflict`, `boilerplate`), BGE-M3 embeddings, HDBSCAN topics, GraphRAG knowledge graphs, research claim graphs, and conflict ledger.
4. **Layer 4: Curated Git Markdown Wiki**: The single authoritative source of truth for downstream LLMs and humans. Versioned in Git, strictly structured, peer-reviewed, and containing complete citation back-links.
5. **Layer 5: Retrieval Projections**: Disposable `wiki_chunks` tables indexed with pgvector HNSW and PostgreSQL GIN FTS. If vectors are corrupted or schema changes, Layer 5 is fully rebuilt from Layer 4 in minutes.

---

## 4. Hardware Sizing & Compute Profiles

### 4.1 VPS Control Plane (Always-On)
- **Specs**: 2 vCPU, 4 GB RAM, 80–120 GB NVMe SSD, Ubuntu 24.04 LTS.
- **Docker Resource Limits**:
  - `postgres` (with pgvector): `mem_limit: 1.8g`, `shared_buffers: 512MB`, `work_mem: 32MB`, `maintenance_work_mem: 128MB`.
  - `prefect-server`: `mem_limit: 800m`.
  - `fastapi-control-api`: `mem_limit: 400m` (uvicorn with 2 workers).
  - `redis`: `mem_limit: 200m`.
  - `caddy`: `mem_limit: 100m`.
- **System Total**: ~3.3 GB under peak load, leaving 700 MB for OS kernel cache.
- **Explicit VPS Blacklist**: Zero ML packages (`torch`, `transformers`, `onnxruntime`, `llama-cpp`, `vllm`, `ollama`, `docling`, `tesseract`, `rclone`).

### 4.2 GPU Worker Hardware Profiles & VRAM Allocation

| Profile | Target Platform | VRAM | LLM Engine & Model | Embedding Model | Extraction Concurrency |
|---|---|---|---|---|---|
| **Tier 1 (Budget/Free)** | Google Colab Free (T4) | 16 GB GDDR6 | Ollama / llama.cpp (`Qwen2.5-7B-Instruct-Q4_K_M` ~4.8 GB) | BAAI/bge-m3 (FP16 ~2.2 GB) | Docling (CPU 2-worker / GPU batch 4) |
| **Tier 2 (Pro/Default)** | Colab Pro / Deepnote (A10G / L4) | 24 GB GDDR6 | vLLM (`Qwen2.5-14B-Instruct-AWQ` ~8.5 GB, KV-cache ~6 GB) | BAAI/bge-m3 (FP16 ~2.2 GB) | Docling + OCRmyPDF (GPU batch 8) |
| **Tier 3 (High-Perf)** | Colab Pro+ / Deepnote (A100) | 40/80 GB HBM2 | vLLM (`Qwen2.5-32B-Instruct-GPTQ` or `Llama-3.1-70B-AWQ`) | BAAI/bge-m3 (FP16 ~2.2 GB) | Docling + Tesseract (GPU batch 16) |

### 4.3 Forbidden Technologies & Drop-In Replacements

| Forbidden | Reason for Ban | Strict Drop-In Replacement |
|---|---|---|
| OpenAI / Azure OpenAI / Anthropic / Gemini APIs | Cost, privacy & telemetry leaks | Local vLLM / Ollama behind LiteLLM on GPU worker |
| Azure Document Intelligence / AWS Textract | Expensive SaaS API lock-in | Docling + OCRmyPDF + Tesseract on GPU |
| Azure AI Search / Pinecone / Qdrant Cloud | Recurring SaaS cost & data egress | Self-hosted PostgreSQL 16 + pgvector HNSW + FTS on VPS |
| rclone / Direct OneDrive Mount on VPS | Unstable FUSE cache & VPS bloat | Microsoft Graph SDK / REST client in GPU notebook |
| Tailscale / WireGuard | Kernel module overhead on VPS | VPS Public IP + TLS 1.3 / SSL + Bearer Secrets |
| Home PC / Local Workstation Dependency | Non-reproducible local setup | Ephemeral cloud GPU (Colab / Deepnote) + VPS |

---

## 5. Network Architecture & Security Hardening

```text
[Colab / Deepnote GPU Worker]
  - Dynamic Outbound IP (Changes on reconnect)
  - MSAL In-Memory Token Cache
  - Direct TLS 1.3 Connections
       |
       |  1. HTTPS :443 (Bearer API_TOKEN) -> Caddy -> FastAPI Control API
       |  2. TCP+SSL :5432 (User 'gpu_worker' + SSL require) -> PostgreSQL
       |  3. HTTP :4200 (Prefect API Key Auth) -> Prefect Server
       v
[VPS Public IP: 203.0.113.10]
  - UFW Firewall: 22 (SSH rate-limited), 80/443 (Caddy), 4200 (Prefect), 5432 (Postgres SSL)
  - Fail2ban: 3 failed attempts -> 24h IP jail
  - PostgreSQL pg_hba.conf: hostssl all gpu_worker 0.0.0.0/0 scram-sha-256
```

### 5.0 PostgreSQL TLS Certificate Initialization (run once before first Postgres start)

Postgres is started with `ssl=on` pointing at cert files that must exist **before** the server boots. On a fresh `pg_data` volume they do not exist, so generate them first:

```bash
# migrations/init-certs.sh — run once, before `docker compose up postgres`
openssl req -new -x509 -days 825 -nodes \
  -out server.crt -keyout server.key \
  -subj "/CN=${VPS_PUBLIC_HOST}"
chmod 600 server.key   # postgres refuses world-readable keys
# Place both files where the postgres container mounts them (see §18.1 volumes).
```

Mount them into the container (read-only) so `-c ssl_cert_file=...` resolves:
```yaml
    volumes:
      - ./migrations/certs/server.crt:/var/lib/postgresql/data/server.crt:ro
      - ./migrations/certs/server.key:/var/lib/postgresql/data/server.key:ro
```

### 5.1 Connection Strings & Environment Variables

**GPU Notebook `.env` Configuration:**
```bash
# Control Plane Endpoints
VPS_PUBLIC_HOST="203.0.113.10" # or DNS A record
CONTROL_API_URL="https://${VPS_PUBLIC_HOST}/api/v1"
CONTROL_API_KEY="__GENERATE_openssl_rand_hex_32__"

# PostgreSQL Direct Connection (for High-Throughput Bulk COPY/UPSERT)
DATABASE_URL="postgresql://gpu_worker:***@${VPS_PUBLIC_HOST}:5432/knowledge_base?sslmode=require"

# Prefect Orchestrator
PREFECT_API_URL="http://${VPS_PUBLIC_HOST}:4200/api"
PREFECT_API_KEY="__GENERATE__"

# Microsoft Graph Azure App Registration (Secret Stored in Notebook Secret Store ONLY — never in repo)
AZURE_TENANT_ID="__TENANT_ID__"
AZURE_CLIENT_ID="__CLIENT_ID__"
AZURE_CLIENT_SECRET="__FROM_COLAB_SECRETS__"
ONEDRIVE_DRIVE_ID="__DRIVE_ID__"
ONEDRIVE_ROOT_FOLDER="/Enterprise_Knowledge_Base"

# GPU Worker Local Engine
LOCAL_LLM_API_BASE="http://127.0.0.1:8000/v1"
LOCAL_LLM_MODEL="Qwen/Qwen2.5-14B-Instruct-AWQ"
EMBEDDING_MODEL_NAME="BAAI/bge-m3"

# Optional: Self-hosted meta-search (SearXNG) on GPU session
SEARXNG_URL="http://127.0.0.1:8888"
```

---

## 6. Deep Internet Research Feature (Agentic, Loop-Driven)

### 6.1 Intent
Agentic **deep dive research on the internet** that:
- Starts from a **research brief** (user/agent question, wiki gap, orphan API, missing REQ, "what is X in industry?").
- Uses **graph engineering** (sources, claims, entities, contradictions).
- Uses **loop engineering** until coverage/confidence gates pass.
- Writes **cited** Markdown under `research/` with snapshots in evidence.
- **Binds** findings to docs/code entities without overwriting approved internal truth.
- Works for OpenWiki and agents via `POST /jobs/research/deepen`.

### 6.2 Research Brief Schema
```yaml
research_id: rsch_...
question: "..."
scope: # optional
  domains_allow: ["*.example.com", "ietf.org", "w3.org", ...]
  domains_deny: ["*"] # deny-all except allow if set
  max_pages: 100
  max_loops: 8
  language: en
seed_urls: []
must_compare_to_wiki: true # flag conflicts with internal pages
authority_floor: web_secondary # never auto-promote above internal approved
```

### 6.3 Graph Engineering (Research)

| Graph | Nodes | Edges | Purpose |
|---|---|---|---|
| **Source graph** | url, site, author, publisher, date | links_to, mirrors, same_content | Provenance |
| **Passage graph** | extracted passages | quotes, cites | Evidence atoms |
| **Claim graph** | atomic claims | supports, contradicts, refines, duplicates | Truth tracking |
| **Entity graph** | people, products, standards, orgs | related_to, defined_as | Alignment to wiki `_entities/` |
| **Query graph** | search queries / sub-questions | spawned_from, answered_by | Loop planning |
| **Conflict graph** | claim vs claim / claim vs wiki | conflicts_with | Review queue |

Deterministic first: fetch → extract text → hash → dedup. LLM extracts claims **with mandatory source passage ids**.

### 6.4 Loop Engineering (Research Deepen)
```text
Loop 0 Brief normalize — parse question; expand sub-questions; load wiki context (FTS/vectors via VPS)
Loop 1 Seed & search — seed_urls + meta-search/OSS queries; enqueue candidate URLs
Loop 2 Fetch & snapshot — download; store snapshot to OneDrive research_snapshots/; register source
Loop 3 Extract & clean — Trafilatura/etc.; boilerplate kill; language detect; units
Loop 4 Embed & cluster — BGE-M3; near-dup; topic clusters for this research_id
Loop 5 Claim mine — structured claims + evidence spans; entity link to wiki
Loop 6 Gap questions — LLM/graph: unanswered sub-questions, weak citations, contradictions → new queries
Loop 7 Deepen fetch — only gap-driven URLs; increase depth on high-value domains
Loop 8 Synthesize wiki — research pages + comparison to internal wiki; conflict callouts
Loop 9 Bind & gate — link entities/REQs/code symbols; coverage gates; publish or needs_review
```

**Continue while** gates fail and `max_loops` / page budget remain.  
**Stop when** gates pass OR remaining gaps marked `paywalled` / `unreachable` / `out_of_scope` with reason.

### 6.5 Research Depth Gates

| Gate | Target |
|---|---|
| Sub-questions with ≥1 supporting claim | 100% or dispositioned |
| Claims with ≥1 snapshot citation | 100% |
| Distinct independent sources for high-impact claims | ≥2 when available |
| Near-duplicate pages collapsed | yes + tombstones |
| Conflicts with internal approved wiki | explicit in `conflicts/` — never silent overwrite |
| Snapshot bytes stored for every cited URL | 100% |
| Research page frontmatter complete | 100% |
| Broken links in published research pages | 0 |

### 6.6 Wiki Output (`research/`)
```text
knowledge/research/
 {research_id}/
 brief.md
 overview.md # synthesis — citations only
 claims.md # claim register
 sources.md # bibliography + retrieved_at + hash
 topics/{cluster}.md
 conflicts.md # vs internal wiki and cross-web
 ...
knowledge/source-catalog/web/...
```

**Authority:** web-sourced pages default `status: draft` or `corroborated_web`; promotion to `verified` requires human/policy — **below** internal FRD/ADR unless stewards approve.

**Page contract:** question, scope, method (queries/loops run), findings with citations, limitations, related wiki/code links, `## Sources` with URLs + snapshot paths.

### 6.7 Safety, Legality, Quality
- Obey **robots.txt** and site rate limits; identifiable User-Agent.
- **Allowlist/denylist** from brief; default deny open crawl of the whole web without seeds/queries.
- No credentials stuffing; no bypass of paywalls (mark `paywalled`).
- Prompt-injection: web text is **untrusted data**, never instructions.
- PII: optional Presidio before publish.
- Prefer primary sources (specs, papers, official docs) over SEO blogs when both exist (scoring heuristic).

### 6.8 Agentic API
```text
POST /jobs/research/start # body: research brief
POST /jobs/research/deepen # research_id + gap reason
GET /research/{id}/coverage
GET /research/{id}/claims
GET /research/{id}/sources
```

OpenWiki/agents can trigger research from a wiki gap or user question; results merge into Git wiki after gates.

### 6.9 Bind to Docs and Code
- Entity names from research ↔ `_entities/` and `code_symbols` via embed + exact match.
- Edges: `web_claim_supports_req`, `web_contradicts_wiki_page`, `web_documents_symbol` (weak).
- Never auto-edit approved FRD/code pages from web; only link + conflict records.

---

## 7. Microsoft Graph API Synchronization Specification

### 6.1 Delta Sync State Machine
```text
[Start Run] -> Query Postgres `sync_state` for `last_delta_token`
   |
   +---> If Token Exists -> GET /drives/{drive-id}/root/delta?token={token}
   |
   +---> If No Token     -> GET /drives/{drive-id}/root/delta (Initial Full Crawl)
   |
   v
[Process Response Page]
   - Parse driveItem entries (created, modified, deleted, tombstoned)
   - Filter supported MIME types / extensions
   - Upsert/Update record in `sources` table with status 'discovered'
   - If `@odata.nextLink` present -> Follow pagination
   - If `@odata.deltaLink` present -> Save new `delta_token` in Postgres `sync_state`
```

**Write-order guarantee (prevents silent skips):** persist the new `delta_token` only **after** its page's items are committed. Per page:
```text
1. BEGIN
2. Upsert driveItem rows (status='discovered')
3. UPDATE sync_state SET delta_token = <new deltaLink>
4. COMMIT
```
Never persist `delta_token` before its page's items are committed — otherwise a crash after the token write but before item commit permanently skips those items.

### 6.2 Rate Limiting, Backoff & Streaming
- **429 Too Many Requests Handling**:
  - Respect `Retry-After` HTTP response header.
  - Implement Exponential Backoff with Jitter: $T_{\text{wait}} = \min(60, 2^{\text{attempt}} + \text{uniform}(0, 1))$.
- **Large File Download (> 4MB)**:
  - Extract `@microsoft.graph.downloadUrl` pre-authenticated short-lived URL.
  - Stream chunks (8 MB chunks) to local GPU ephemeral SSD `/tmp/ingest/{source_id}.raw`.
  - Compute SHA-256 on the fly during streaming download.

### 6.3 MIME Ingestion Matrix
| Category | File Extensions | Extraction Engine | Extraction Target |
|---|---|---|---|
| **Rich Documents** | `.pdf`, `.docx`, `.pptx`, `.epub` | `Docling` + `OCRmyPDF` | Structured JSON Blocks, Markdown tables, OCR bboxes |
| **Plain Text / Code** | `.md`, `.txt`, `.py`, `.sql`, `.json`, `.yaml` | Native UTF-8 Parser | Line-numbered canonical text units |
| **Tabular Data** | `.xlsx`, `.csv`, `.tsv` | `openpyxl` / `pandas` | Markdown formatted summary + Schema + Row chunks |
| **Comms & Tickets** | `.eml`, `.msg`, `.html` | `extract-msg` / `BeautifulSoup4` | Email thread metadata, clean body, attachments |
| **Scans / Images** | `.png`, `.jpg`, `.tiff` | `Tesseract OCR` + `Docling OCR` | Extracted text + Diagram layout bounding boxes |

---

## 7. Auto-Discovery & Project Classification Engine

The discovery engine is the **first stage** of the pipeline — it recursively walks a given input path (local filesystem directory, mounted drive, or OneDrive Graph folder) and automatically classifies every subfolder and file before anything enters the source registry. It is **language-agnostic** and works identically whether the input is a Python monorepo, a .NET solution, a Java Gradle project, a docs-only folder, or a mixed archive.

### 7.1 Discovery Flow

```text
[Input Path]
  (local dir / mounted drive / OneDrive Graph folder ID)
       |
       v
[Recursive Walker]
  - Walk all children depth-first
  - Apply global ignore rules (§7.3) at every level → prune entire subtrees
  - Collect surviving files into a flat manifest
       |
       v
[Project Fingerprinter]
  - At each directory level, detect project markers (§7.2)
  - Assign project_type, language_ecosystem, and project_root flag
  - Propagate project context DOWN to child files
       |
       v
[Content Classifier]
  - For each surviving file, assign content_class (§7.4) based on:
    1. File extension + MIME
    2. Parent directory name conventions
    3. Project context from fingerprinting
  - Tag with: content_class, extraction_priority, estimated_signal_density
       |
       v
[Discovery Manifest]
  - Write discovery_manifest.json to VPS (or upsert to `sources` table)
  - Each entry: {path, sha256, size, project_type, content_class, priority}
  - Feed directly into §8 pipeline Stage 1 (Ingest & Sync)
```

### 7.2 Project Fingerprinting Rules (Language-Agnostic)

The fingerprinter checks each directory for **marker files** to determine what kind of project it is. Multiple markers can coexist (e.g., a repo with both source code and docs).

| Marker File(s) | Detected `project_type` | `language_ecosystem` |
|---|---|---|
| `.git/` directory | `git_repository` | (inferred from other markers) |
| `.github/`, `.gitlab-ci.yml`, `Jenkinsfile`, `.circleci/` | `ci_cd_config` | — |
| `package.json` | `source_code` | `javascript/typescript` |
| `tsconfig.json`, `angular.json`, `next.config.*` | `source_code` | `typescript` |
| `requirements.txt`, `pyproject.toml`, `setup.py`, `Pipfile` | `source_code` | `python` |
| `*.sln`, `*.csproj`, `*.fsproj` | `source_code` | `dotnet` |
| `pom.xml`, `build.gradle`, `build.gradle.kts` | `source_code` | `java/kotlin` |
| `go.mod` | `source_code` | `go` |
| `Cargo.toml` | `source_code` | `rust` |
| `composer.json` | `source_code` | `php` |
| `Gemfile` | `source_code` | `ruby` |
| `mix.exs` | `source_code` | `elixir` |
| `Makefile`, `CMakeLists.txt` | `source_code` | `c/cpp` |
| `docker-compose.yml`, `Dockerfile` | `infrastructure` | `docker` |
| `*.tf`, `*.tfvars` | `infrastructure` | `terraform` |
| `Chart.yaml`, `values.yaml` | `infrastructure` | `helm/kubernetes` |
| `mkdocs.yml`, `docusaurus.config.*`, `_config.yml` (Jekyll), `book.toml` (mdBook) | `documentation_site` | — |
| `docs/`, `manual/`, `wiki/`, `guides/` (directory names) | `documentation_folder` | — |
| `README.md` alone (no source markers) | `standalone_docs` | — |
| `plan.md`, `ARCHITECTURE.md`, `ADR-*.md`, `RFC-*.md` | `design_documents` | — |
| `*.postman_collection.json`, `openapi.yaml`, `swagger.json` | `api_spec` | — |
| No recognized markers | `unclassified` | `unknown` |

**Propagation rule**: When a directory is identified as `git_repository`, all children inherit `repo_root = <that directory>`. The `language_ecosystem` detected at the root propagates down so that, e.g., a `src/utils/helper.py` file knows it belongs to a Python repo even without its own `pyproject.toml`.

### 7.3 Global Noise Exclusion Rules

These directories and files are **pruned at walk time** — the walker never descends into them and they never enter the source registry. The rules are language-agnostic and cover all major ecosystems.

#### 7.3.1 Directory Exclusion Patterns (Pruned Recursively)

```yaml
# Version control internals
- .git
- .svn
- .hg

# Dependency / package caches (language-agnostic)
- node_modules
- bower_components
- vendor                   # PHP Composer, Go vendor, Ruby bundler
- .bundle
- __pycache__
- .mypy_cache
- .pytest_cache
- .ruff_cache
- .tox
- .nox
- .venv
- venv
- env
- .env.local               # but NOT .env.example (that's config documentation)
- packages                 # NuGet restored packages (when inside .NET obj)

# Build output directories
- bin
- obj
- build
- dist
- out
- target                   # Java/Kotlin Maven/Gradle, Rust Cargo
- _build                   # Elixir Mix
- .next                    # Next.js build
- .nuxt                    # Nuxt.js build
- .output                  # Nitro/Nuxt output
- .turbo                   # Turborepo cache
- .parcel-cache
- .webpack
- .angular
- Debug
- Release
- x64
- x86
- .gradle
- .idea
- .vs
- .vscode                  # IDE settings (not documentation)

# Container / infrastructure artifacts
- .terraform
- .terragrunt-cache
- charts/*/charts          # Helm sub-chart dependencies
- coverage                 # Test coverage reports (auto-generated)
- .nyc_output
- htmlcov
- TestResults

# OS / editor junk
- .DS_Store
- Thumbs.db
- __MACOSX
- .Spotlight-V100
- .Trashes
```

#### 7.3.2 File Exclusion Patterns (Skipped Individually)

```yaml
# Lock files (dependency resolution artifacts, not human knowledge)
- "*.lock"                 # Cargo.lock, poetry.lock, Pipfile.lock, etc.
- package-lock.json
- yarn.lock
- pnpm-lock.yaml
- composer.lock
- Gemfile.lock
- go.sum
- flake.lock

# Compiled / binary artifacts
- "*.pyc"
- "*.pyo"
- "*.class"
- "*.o"
- "*.obj"
- "*.dll"
- "*.exe"
- "*.so"
- "*.dylib"
- "*.wasm"
- "*.min.js"
- "*.min.css"
- "*.map"                  # Source maps
- "*.bundle.js"

# Auto-generated files
- "*.designer.cs"          # .NET WinForms designer
- "*.generated.*"
- "*.g.cs"                 # .NET source generators
- "*.g.dart"               # Dart/Flutter codegen
- ".gitignore"             # Not knowledge content
- ".gitattributes"
- ".editorconfig"
- ".prettierrc*"
- ".eslintrc*"
- "*.tfstate"
- "*.tfstate.backup"

# Large media (unless in docs/assets — handled by §7.4)
- "*.mp4"
- "*.mov"
- "*.avi"
- "*.zip"
- "*.tar.gz"
- "*.rar"
- "*.iso"
```

#### 7.3.3 Configurable Override

All exclusion rules are defined in a single `discovery_rules.yaml` file in the repo root. Operators can:
- **Add** custom ignore patterns per deployment (e.g., `legacy_backups/`).
- **Whitelist** a normally-ignored directory if it contains documentation (e.g., `!vendor/internal-docs/`).
- **Adjust priority** weights for content classes.

```yaml
# discovery_rules.yaml
version: 1
ignore_dirs:
  - node_modules
  - .git
  - bin
  - obj
  # ... (full default list)

ignore_files:
  - "*.lock"
  - "*.pyc"
  # ... (full default list)

whitelist:
  - "vendor/internal-sdk/docs/**"    # Keep docs even inside vendor
  - ".github/ISSUE_TEMPLATE/**"      # GitHub issue templates are documentation

custom_project_markers:
  - marker: "CODEOWNERS"
    project_type: "git_repository"
```

### 7.4 Content Classification Taxonomy

After noise exclusion, every surviving file is assigned a `content_class` that determines its **extraction priority** and **pipeline routing**.

| `content_class` | Description | Extraction Priority | Examples |
|---|---|---|---|
| `architecture_doc` | System design, ADRs, RFCs, ERDs, C4 diagrams | **P0 — Critical** | `ARCHITECTURE.md`, `ADR-*.md`, `RFC-*.md`, `*.drawio`, `*.mermaid` |
| `requirements_spec` | FRDs, SRS, PRDs, user stories, acceptance criteria | **P0 — Critical** | `FRD-*.docx`, `SRS-*.pdf`, `requirements/*.md` |
| `api_specification` | OpenAPI, Swagger, GraphQL schemas, Postman collections | **P0 — Critical** | `openapi.yaml`, `swagger.json`, `*.graphql`, `schema.prisma` |
| `runbook_ops` | Operational runbooks, SOPs, incident playbooks | **P1 — High** | `runbooks/*.md`, `playbook-*.md`, `TROUBLESHOOTING.md` |
| `readme_overview` | Project/module READMEs, getting-started guides | **P1 — High** | `README.md`, `CONTRIBUTING.md`, `CHANGELOG.md` |
| `config_iac` | Infrastructure-as-code, deployment configs, CI/CD | **P1 — High** | `docker-compose.yml`, `*.tf`, `Dockerfile`, `*.k8s.yaml`, `.github/workflows/*.yml` |
| `source_code` | Application source files (business logic, not tests) | **P2 — Standard** | `*.py`, `*.ts`, `*.cs`, `*.java`, `*.go`, `*.rs` |
| `test_code` | Test files, fixtures, mocks, test helpers | **P3 — Low** | `*_test.go`, `test_*.py`, `*.spec.ts`, `*.test.js`, `__tests__/` |
| `data_schema` | Database migrations, SQL schemas, seed data | **P2 — Standard** | `migrations/*.sql`, `schema.sql`, `*.prisma`, `alembic/versions/` |
| `wiki_knowledge` | Internal wiki pages, knowledge base articles | **P0 — Critical** | `wiki/**/*.md`, `knowledge/**/*.md`, `docs/**/*.md` |
| `plan_design` | Project plans, roadmaps, sprint plans, design docs | **P1 — High** | `plan.md`, `ROADMAP.md`, `design/*.md` |
| `communication` | Exported emails, chat logs, meeting notes | **P2 — Standard** | `*.eml`, `*.msg`, `meeting-notes-*.md` |
| `research_reference` | Research papers, whitepapers, external references | **P2 — Standard** | `research/*.pdf`, `papers/*.pdf`, `references/` |
| `asset_media` | Diagrams, screenshots, images inside documentation | **P3 — Low** | `docs/assets/*.png`, `diagrams/*.svg` |
| `noise_generated` | Auto-generated, build output that survived filters | **Skip** | Coverage reports, linting output, benchmark results |

**Tie-break rule (asset vs doc):** when a file could match two classes, the **parent-directory class wins** over the extension class. A diagram/image inside an `architecture_doc` path (e.g. `docs/arch/diagram.png`) is classified `architecture_doc` (P0), not `asset_media` (P3). Only standalone assets not referenced by a doc directory fall to `asset_media`.

#### Classification Decision Tree

```text
For each surviving file F:
  1. Is F inside a directory named test(s)/ or __tests__/ or spec(s)/
     OR does filename match *_test.*, test_*.*, *.spec.*, *.test.*?
     → content_class = "test_code"

  2. Is F inside a directory named docs/, manual/, wiki/, guides/, knowledge/?
     → content_class = "wiki_knowledge" (if .md/.rst/.txt)
     → content_class = "requirements_spec" (if .pdf/.docx and name matches FRD/SRS/PRD)
     → content_class = "asset_media" (if image/diagram)

  3. Does filename match ADR-*, RFC-*, ARCHITECTURE.*, DESIGN.*?
     → content_class = "architecture_doc"

  4. Does filename match README.*, CONTRIBUTING.*, CHANGELOG.*?
     → content_class = "readme_overview"

  5. Does filename match openapi.*, swagger.*, *.graphql, *.proto?
     → content_class = "api_specification"

  6. Is F inside migrations/ or has name schema.*, *.sql?
     → content_class = "data_schema"

  7. Is F a source code file (matched by extension from project_type)?
     → content_class = "source_code"

  8. Is F a config/IaC file (Dockerfile, *.tf, *.yml in .github/)?
     → content_class = "config_iac"

  9. Fallback: classify by MIME type or mark "unclassified"
```

### 7.5 Source Code Extraction Strategy (Language-Agnostic)

Source code files (`content_class = "source_code"`) require special extraction treatment. Raw code is not directly useful for a knowledge wiki — instead, the extractor pulls **semantic summaries**:

| What to Extract | How | Output Unit Type |
|---|---|---|
| Module/file-level docstrings & comments | Regex + AST parsing (tree-sitter for any language) | `documentation` |
| Public class/function signatures + docstrings | tree-sitter AST query (`function_definition`, `class_definition`) | `api_surface` |
| Inline `// TODO`, `// FIXME`, `// HACK`, `// NOTE` comments | Regex pattern scan | `developer_note` |
| SQL migration content (CREATE TABLE, ALTER) | SQL parser or raw text | `data_schema` |
| Configuration constants, feature flags | Heuristic: `const`, `enum`, UPPER_CASE assignments | `configuration` |
| Import/dependency graph | AST import statement extraction | `dependency_metadata` (metadata only, not wiki content) |

**What NOT to extract from source code**:
- Function body implementation details (too volatile, low knowledge density)
- Variable assignments, loop bodies, error handling boilerplate
- Test assertions and mock setups (unless they document expected behavior)

---

## 7.6 Deep Code-to-Wiki Pipeline (Expert-Grade)

This section defines the **deep code-to-wiki pipeline** that surpasses standard OpenWiki code-to-wiki generation by orders of magnitude in depth, accuracy, and utility for LLMs. It uses **language-agnostic tree-sitter IR**, **multi-layer code graphs**, and **iterative deepening loops** until business-level understanding gates pass.

### 7.6.1 Philosophy & Differentiation from OpenWiki

| Dimension | OpenWiki Code-to-Wiki | This Pipeline (Deep) |
|---|---|---|
| **Symbol coverage** | Public API signatures only | Full: public + private + internal + generated + inferred |
| **Call graph depth** | 1-hop (direct calls) | Unbounded with cycle detection; loop until transitive closure |
| **Data flow** | None | Taint analysis + def-use chains + SQL-to-ORM mapping |
| **Control flow** | None | Path conditions, loop invariants, error paths, async boundaries |
| **Architecture recovery** | File structure | C4-level: containers, components, interfaces, boundaries |
| **Business logic** | Docstrings only | Inferred from code patterns, tests, configs, migrations |
| **Use cases / workflows** | Not extracted | Reconstructed from entry points + call chains + data mutations |
| **Cross-repo analysis** | Single repo | Monorepo + polyrepo with shared library resolution |
| **Test-as-spec** | Ignored | First-class: tests document expected behavior, edge cases |
| **Iterative deepening** | One pass | Loops 0–8 with gate-based continuation |
| **Citation granularity** | File-level | `repo@commit:path:lines` + exact AST node IDs |
| **GraphRAG on code** | No | Yes: entities = symbols, relations = calls/data/extends/impl |

### 7.6.2 Language-Agnostic Tree-sitter IR (Core)

All languages parse to a **unified intermediate representation (UIR)** before any analysis:

```python
# workers/gpu_worker/code_ir/unified_ir.py
@dataclass
class UIRNode:
    # Identity
    node_id: str                    # SHA256(language + file + byte_range + kind)
    repo_id: str
    commit_sha: str
    file_path: str
    language: str                   # python, typescript, go, java, rust, cpp, csharp, etc.
    
    # Structural
    kind: UIRKind                   # MODULE, CLASS, INTERFACE, FUNCTION, METHOD, 
                                    # FIELD, PROPERTY, ENUM, TYPE_ALIAS, IMPORT, 
                                    # DECORATOR, ANNOTATION, MACRO, etc.
    name: str
    qualified_name: str             # fully qualified: pkg.mod.Class.method
    parent_id: Optional[str]
    children: List[str]
    
    # Source location (exact, for citations)
    byte_start: int
    byte_end: int
    line_start: int
    line_end: int
    
    # Semantic
    signature: Optional[str]        # normalized signature (params, returns, generics)
    docstring: Optional[str]        # extracted + cleaned
    annotations: List[Annotation]   # @Deprecated, @Inject, @Transactional, #[async], etc.
    visibility: Visibility          # PUBLIC, PROTECTED, PRIVATE, INTERNAL, PACKAGE
    is_static: bool
    is_async: bool
    is_generator: bool
    is_abstract: bool
    is_override: bool
    
    # Type information (when available)
    type_annotation: Optional[str]  # return type, field type, generic params
    type_parameters: List[str]
    
    # Extracted metadata (language-specific, normalized)
    metadata: Dict[str, Any]        # e.g., {"sql_queries": [...], "http_routes": [...]}
```

**Tree-sitter Grammar Coverage** (required minimum):
| Language | Grammar | Key Queries |
|---|---|---|
| Python | tree-sitter-python | function_definition, class_definition, import_statement, decorated_definition |
| TypeScript/TSX | tree-sitter-typescript | function_declaration, class_declaration, interface_declaration, type_alias_declaration, import_statement, method_definition, property_signature |
| Go | tree-sitter-go | function_declaration, type_declaration, method_declaration, import_declaration |
| Java | tree-sitter-java | class_declaration, interface_declaration, method_declaration, field_declaration, import_declaration, annotation |
| Rust | tree-sitter-rust | function_item, struct_item, enum_item, trait_item, impl_item, mod_item, use_declaration |
| C# | tree-sitter-c-sharp | class_declaration, interface_declaration, method_declaration, property_declaration, using_directive, attribute |
| C/C++ | tree-sitter-cpp | function_definition, class_specifier, namespace_definition, template_declaration |
| SQL | tree-sitter-sql | create_table, create_view, create_function, select_statement |
| Protobuf | tree-sitter-protobuf | message_declaration, service_declaration, rpc_declaration |
| GraphQL | tree-sitter-graphql | object_type_definition, interface_type_definition, field_definition |

**Fallback for unsupported languages**: Regex + heuristic extractor producing `UIRNode` with `kind=UNKNOWN`, `signature=raw_text[:200]`.

### 7.6.3 Code Graph Construction (5 Graph Layers)

All graphs are persisted to Postgres (§8 tables: `code_symbols`, `code_edges`, `use_cases`, `workflows`, `workflow_steps`).

#### Layer 1: Symbol Graph (Structural)
- **Nodes**: Every `UIRNode` with `kind ∈ {CLASS, INTERFACE, FUNCTION, METHOD, FIELD, PROPERTY, ENUM, TYPE_ALIAS}`
- **Edges**:
  - `CONTAINS` (module→class, class→method, class→field)
  - `EXTENDS` (class→class, interface→interface)
  - `IMPLEMENTS` (class→interface)
  - `OVERRIDES` (method→method)
  - `DECORATES` (decorator→target)
  - `PARAMETERIZES` (generic→concrete)

#### Layer 2: Call Graph (Dynamic + Static)
- **Nodes**: Functions/Methods/Constructors
- **Edges**: `CALLS` (direct), `CALLS_VIRTUAL` (interface/abstract dispatch), `CALLS_DYNAMIC` (reflection, `getattr`, `MethodInfo.Invoke`)
- **Construction**:
  1. Static: tree-sitter `call_expression` → resolve callee via import + scope
  2. Virtual dispatch: CHA (Class Hierarchy Analysis) + RTA (Rapid Type Analysis)
  3. Dynamic: Pattern matching for `importlib`, `reflect`, `Activator.CreateInstance`, `__import__`
- **Edge metadata**: `call_site_id`, `is_async`, `is_conditional`, `loop_depth`, `try_block`

#### Layer 3: Data Flow Graph (Def-Use + Taint)
- **Nodes**: Variables, parameters, fields, return values, global state
- **Edges**: `DEFINES` (assignment), `USES` (read), `MODIFIES` (mutation), `TAINT_FLOWS` (user input → sink)
- **Analysis**:
  - SSA-form def-use chains per function
  - Inter-procedural: parameter passing, return values, field access
  - SQL taint: Track user input → ORM → raw SQL → execution
  - Config taint: Environment → config object → feature flag branch

#### Layer 4: Control Flow + Path Conditions
- **Nodes**: Basic blocks (entry, exit, branch, loop header, loop latch, try, catch, finally, await)
- **Edges**: `FLOWS_TO` with `condition` (boolean expression, `true`, `false`, `exception`)
- **Extracted**: Loop invariants (variables modified in loop), async boundaries (`await`, `yield`, `Task.Run`), error handling paths

#### Layer 5: Architecture Graph (Recovered C4)
- **Nodes**: 
  - `Container` (deployable unit: service, db, queue, frontend) — inferred from Dockerfile, k8s, compose, `.github/workflows`
  - `Component` (logical grouping: module, package, namespace with high internal cohesion)
  - `Interface` (public API: REST, gRPC, GraphQL, message queue contracts)
  - `Boundary` (external system: 3rd party API, legacy DB, message broker)
- **Edges**: `DEPENDS_ON` (sync), `EMITS_TO` (async), `READS_FROM`/`WRITES_TO` (data)
- **Evidence**: Import graphs, config (Spring `@Autowired`, FastAPI `Depends`, Go `wire`), infra-as-code

### 7.6.4 Deepening Loops (Loops 0–8)

```text
Loop 0: INVENTORY & REPO MAP
  Input: Git repo URL / local path / OneDrive mirror
  Actions:
    - git clone --mirror (or Graph API fetch)
    - Recursive walk with §7.3 noise filters
    - Language detection per file (linguist / extension + shebang)
    - UIR parsing (parallel per file, tree-sitter pool)
    - Persist: code_files, code_symbols (Layer 1)
  Output: Symbol inventory CSV + parse error report
  Gate: Parse success rate ≥ 98%; failed files → quarantine

Loop 1: SIGNATURES & API SURFACE
  Input: code_symbols from Loop 0
  Actions:
    - Normalize signatures across languages (unify generics, optionals, unions)
    - Extract public API surface: all PUBLIC + PROTECTED symbols
    - Classify: REST endpoint, gRPC method, GraphQL resolver, CLI command, 
      event handler, message consumer, scheduled job, migration
    - Bind to OpenAPI/Protobuf/GraphQL schemas when co-located
    - Persist: api_surface table (endpoint, method, params, returns, auth, deprecated)
  Gate: 100% of PUBLIC symbols have signature + qualified_name; 
        100% of framework entry points classified

Loop 2: CALL GRAPH & TRANSITIVE CLOSURE
  Input: code_symbols + UIR call expressions
  Actions:
    - Build intra-file call edges (exact resolution)
    - Build inter-file via import resolution + qualified names
    - CHA/RTA for virtual dispatch (interfaces, abstract classes, traits)
    - Dynamic call pattern detection (reflection, plugins, DI containers)
    - Compute transitive closure with cycle detection (SCC condensation)
    - Identify: entry points (no incoming), leaf nodes (no outgoing), 
      hot paths (high betweenness), recursive cycles
    - Persist: code_edges (CALLS, CALLS_VIRTUAL, CALLS_DYNAMIC)
  Gate: Call graph edge count > 0.5 × symbol count; 
        SCCs with >50 nodes flagged for review

Loop 3: USE CASES & ENTRY-POINT SCENARIOS
  Input: Call graph + entry point classifications (Loop 1)
  Actions:
    - For each entry point (HTTP handler, CLI cmd, event handler, job, migration):
      * Trace forward through call graph (depth-limited, configurable)
      * Collect: all symbols touched, data mutations (writes), external calls
      * Identify: business operation name (from route, command, event type)
      * Extract: preconditions, postconditions, side effects, error outcomes
    - Cluster related entry points into Use Cases (e.g., "User Registration" = 
      POST /users + UserCreated event handler + welcome email job)
    - Persist: use_cases (name, entry_points[], symbols[], mutations[], ext_calls[])
  Gate: Every PUBLIC entry point mapped to ≥1 use case; 
        Use case has ≥3 symbols or marked "trivial"

Loop 4: DATA FLOW & STATE MACHINES
  Input: code_symbols + call graph + def-use analysis
  Actions:
    - Build intra-procedural SSA def-use
    - Inter-procedural: parameter/return/field propagation
    - Identify: 
      * Entity lifecycles (create → update* → delete / archive)
      * State machines (status/enum fields with transition methods)
      * SQL/ORM operations per entity (CRUD + custom queries)
      * Configuration flows (env → config struct → feature flag → branch)
      * Serialization boundaries (DTO ↔ domain ↔ wire format)
    - Persist: data_flow_edges, entity_lifecycles, state_machines
  Gate: All DB-mapped entities have lifecycle; 
        All feature flags traced to decision points

Loop 5: WORKFLOWS & ORCHESTRATION
  Input: Use cases + call graph + async boundaries + message queues
  Actions:
    - Detect workflow patterns:
      * Saga: compensating transactions across services
      * Pipeline: sequential stages with data passing
      * Fan-out/Fan-in: parallel processing + aggregation
      * Event sourcing: command → events → projections
      * CQRS: separate read/write models
    - For each workflow: steps[], transitions[], compensation[], 
      idempotency keys, retry policies, timeout configs
    - Persist: workflows, workflow_steps
  Gate: All message consumers + scheduled jobs mapped to workflows

Loop 6: DOCUMENTATION BINDING & GAP ANALYSIS
  Input: All prior loops + docs units (from §8 pipeline) + README + ADRs
  Actions:
    - Semantic similarity (BGE-M3) between code symbols and doc units
    - Exact match: symbol qualified_name in doc text
    - For each symbol: linked_docs[], coverage_score (0–1)
    - Identify: undocumented public API, outdated docs (git blame on doc vs code),
      missing architecture decisions (ADR for major symbols)
    - Persist: code_doc_bindings, doc_gaps
  Gate: Public API documentation coverage ≥ 80%

Loop 7: ARCHITECTURE RECONSTRUCTION
  Input: Architecture graph (Layer 5) + container configs + deploy manifests
  Actions:
    - Reconstruct C4 diagrams (Context, Container, Component, Code)
    - Identify: circular dependencies, violated layering, God components,
      missing contracts, single points of failure
    - Generate: Mermaid/PlantUML diagrams + narrative markdown
    - Persist: architecture_diagrams, architecture_findings
  Gate: All containers + components have diagram; 
        Architecture decision log (ADR) for each major finding

Loop 8: CONSISTENCY, QUALITY & WIKI SYNTHESIS
  Input: All prior loops
  Actions:
    - Cross-check: API surface vs OpenAPI spec, DB schema vs migrations,
      message contracts vs producer/consumer, DI config vs actual injections
    - Generate wiki pages per use case / component / entity / workflow:
      * Business-language narrative (not code dump)
      * Mermaid diagrams embedded
      * `repo@commit:path:lines` citations for every claim
      * Decision rationale (from git history, ADRs, PR descriptions)
      * Known limitations, TODOs, technical debt markers
    - Quality gates: Citation density ≥ 1 per 3 sentences; 
      All PUBLIC symbols referenced; No orphan pages
    - Persist: wiki_pages (code/ domain), coverage_reports
  Gate: All gates pass → publish to Git wiki
```

**Loop Control**:
- Each loop is a separate Prefect flow in `gpu-code` pool
- Checkpoint state in `deepen_loops` table (`loop_index`, `status`, `started_at`, `completed_at`, `metrics_json`)
- Continue to next loop only if **all gates pass**
- If gate fails: record in `code_gaps`, allow manual override or config adjustment, retry loop
- Max 3 retries per loop before escalation

### 7.6.5 Business-Language Wiki Page Types (Generated)

| Page Type | Source | Template | Example |
|---|---|---|---|
| `use_case_<name>.md` | Loop 3 | `templates/use_case.md.j2` | "User Registration" — flow, validation, emails, events |
| `entity_<name>.md` | Loop 4 | `templates/entity.md.j2` | "Order" — lifecycle, states, fields, queries, invariants |
| `workflow_<name>.md` | Loop 5 | `templates/workflow.md.j2` | "Order Fulfillment Saga" — steps, compensation, timeouts |
| `component_<name>.md` | Loop 7 | `templates/component.md.j2` | "Payment Service" — interfaces, deps, data, scaling |
| `api_<endpoint>.md` | Loop 1 | `templates/api_endpoint.md.j2` | "POST /orders" — contract, auth, errors, examples |
| `architecture_<domain>.md` | Loop 7 | `templates/architecture.md.j2` | "Payments Domain" — C4 diagrams, decisions, boundaries |

**Every page includes**:
- Frontmatter: `id`, `title`, `page_type`, `domain`, `status`, `source_symbols[]`, `source_commits[]`, `coverage_score`, `generated_at`
- `## Overview` — 2–3 sentence business summary
- `## Detailed Flow` — numbered steps with code citations
- `## Data Model` — fields, types, constraints, relationships
- `## Error Handling` — failure modes, retries, compensations
- `## Configuration` — feature flags, env vars, tuning params
- `## Related` — links to other wiki pages (use cases, entities, workflows)
- `## Sources` — `repo@commit:path:lines` for every factual claim

### 7.6.6 Code-Specific Database Tables (Additions to §8)

```sql
-- Code symbols (unified IR nodes)
CREATE TABLE code_symbols (
    symbol_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    repo_id UUID NOT NULL REFERENCES repos(repo_id),
    commit_sha CHAR(40) NOT NULL,
    file_path TEXT NOT NULL,
    language VARCHAR(32) NOT NULL,
    kind VARCHAR(32) NOT NULL,              -- MODULE, CLASS, FUNCTION, METHOD, FIELD, ...
    name TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    parent_symbol_id UUID REFERENCES code_symbols(symbol_id),
    signature TEXT,
    docstring TEXT,
    visibility VARCHAR(16) NOT NULL,        -- PUBLIC, PROTECTED, PRIVATE, INTERNAL
    is_static BOOLEAN DEFAULT FALSE,
    is_async BOOLEAN DEFAULT FALSE,
    is_abstract BOOLEAN DEFAULT FALSE,
    byte_start INT NOT NULL,
    byte_end INT NOT NULL,
    line_start INT NOT NULL,
    line_end INT NOT NULL,
    metadata JSONB DEFAULT '{}'::jsonb,
    uir_hash CHAR(64) NOT NULL,             -- content hash for idempotency
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(repo_id, commit_sha, uir_hash)
);
CREATE INDEX idx_symbols_qualified ON code_symbols(qualified_name);
CREATE INDEX idx_symbols_repo_commit ON code_symbols(repo_id, commit_sha);

-- Code edges (all graph layers)
CREATE TABLE code_edges (
    edge_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    repo_id UUID NOT NULL REFERENCES repos(repo_id),
    commit_sha CHAR(40) NOT NULL,
    source_symbol_id UUID NOT NULL REFERENCES code_symbols(symbol_id),
    target_symbol_id UUID NOT NULL REFERENCES code_symbols(symbol_id),
    edge_type VARCHAR(32) NOT NULL,         -- CONTAINS, EXTENDS, IMPLEMENTS, OVERRIDES,
                                            -- CALLS, CALLS_VIRTUAL, CALLS_DYNAMIC,
                                            -- DEFINES, USES, MODIFIES, TAINT_FLOWS,
                                            -- FLOWS_TO, DEPENDS_ON, EMITS_TO
    metadata JSONB DEFAULT '{}'::jsonb,     -- {call_site_id, condition, loop_depth, ...}
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_symbol_id, target_symbol_id, edge_type)
);
CREATE INDEX idx_edges_source ON code_edges(source_symbol_id);
CREATE INDEX idx_edges_target ON code_edges(target_symbol_id);
CREATE INDEX idx_edges_type ON code_edges(edge_type);

-- Use cases (business operations)
CREATE TABLE use_cases (
    use_case_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    repo_id UUID NOT NULL REFERENCES repos(repo_id),
    name TEXT NOT NULL,
    description TEXT,
    entry_point_symbol_ids UUID[] NOT NULL,
    touched_symbol_ids UUID[] NOT NULL,
    mutation_symbol_ids UUID[] DEFAULT '{}',      -- symbols that write data
    external_call_ids UUID[] DEFAULT '{}',        -- HTTP, gRPC, DB, queue
    status VARCHAR(32) DEFAULT 'active',          -- active, deprecated, superseded
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(repo_id, name)
);

-- Workflows (multi-step orchestrations)
CREATE TABLE workflows (
    workflow_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    repo_id UUID NOT NULL REFERENCES repos(repo_id),
    name TEXT NOT NULL,
    pattern VARCHAR(32) NOT NULL,          -- SAGA, PIPELINE, FAN_OUT, EVENT_SOURCING, CQRS
    description TEXT,
    idempotency_key_pattern TEXT,
    retry_policy JSONB,
    timeout_seconds INT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(repo_id, name)
);

CREATE TABLE workflow_steps (
    step_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    workflow_id UUID NOT NULL REFERENCES workflows(workflow_id) ON DELETE CASCADE,
    step_index INT NOT NULL,
    name TEXT NOT NULL,
    symbol_ids UUID[] NOT NULL,              -- code symbols implementing this step
    input_data JSONB,                        -- expected input schema
    output_data JSONB,                       -- produced output schema
    compensation_symbol_ids UUID[] DEFAULT '{}',  -- rollback actions
    is_parallel BOOLEAN DEFAULT FALSE,
    depends_on_step_ids UUID[] DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(workflow_id, step_index)
);

-- Deepening loop tracking
CREATE TABLE deepen_loops (
    loop_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    repo_id UUID NOT NULL REFERENCES repos(repo_id),
    commit_sha CHAR(40) NOT NULL,
    loop_index INT NOT NULL,                 -- 0 to 8
    status VARCHAR(32) NOT NULL,             -- running, completed, failed, skipped
    gate_results JSONB DEFAULT '{}'::jsonb,  -- per-gate pass/fail + metrics
    metrics JSONB DEFAULT '{}'::jsonb,       -- symbols_processed, edges_found, etc.
    started_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ,
    UNIQUE(repo_id, commit_sha, loop_index)
);

-- Code gaps (failed gates, manual review items)
CREATE TABLE code_gaps (
    gap_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    repo_id UUID NOT NULL REFERENCES repos(repo_id),
    loop_index INT NOT NULL,
    gap_type VARCHAR(64) NOT NULL,           -- PARSE_FAILURE, MISSING_DOCS, CIRCULAR_DEP, 
                                            -- UNRESOLVED_CALL, UNMAPPED_ENTRY_POINT, etc.
    description TEXT NOT NULL,
    symbol_ids UUID[] DEFAULT '{}',
    severity VARCHAR(16) DEFAULT 'medium',   -- low, medium, high, critical
    disposition VARCHAR(32) DEFAULT 'open',  -- open, fixed, accepted, deferred
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMPTZ
);

-- Repo registry
CREATE TABLE repos (
    repo_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name TEXT NOT NULL UNIQUE,
    git_url TEXT,
    onedrive_path TEXT,                      -- mirror path in OneDrive
    default_branch TEXT DEFAULT 'main',
    languages TEXT[] DEFAULT '{}',
    config JSONB DEFAULT '{}'::jsonb,        -- deepening config overrides
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
```

### 7.6.7 Configuration (policies/code_deepening.yaml)

```yaml
# policies/code_deepening.yaml
version: 1

# Tree-sitter pool
tree_sitter:
  max_workers: 8
  timeout_seconds: 30
  memory_limit_mb: 512

# Loop gates (must ALL pass to continue)
gates:
  loop_0_inventory:
    parse_success_rate: 0.98
    max_quarantine_files: 50
  
  loop_1_signatures:
    public_symbol_coverage: 1.0
    entry_point_classification_rate: 1.0
  
  loop_2_call_graph:
    min_edges_per_symbol: 0.5
    max_scc_size_without_review: 50
  
  loop_3_use_cases:
    entry_point_mapping_rate: 1.0
    min_symbols_per_use_case: 3
  
  loop_4_data_flow:
    entity_lifecycle_coverage: 1.0
    feature_flag_traceability: 1.0
  
  loop_5_workflows:
    consumer_workflow_mapping: 1.0
    job_workflow_mapping: 1.0
  
  loop_6_doc_binding:
    public_api_doc_coverage: 0.80
    max_outdated_docs: 10
  
  loop_7_architecture:
    container_diagram_coverage: 1.0
    component_diagram_coverage: 1.0
    adr_for_major_findings: 1.0
  
  loop_8_synthesis:
    citation_density: 0.33       # 1 citation per 3 sentences
    public_symbol_reference_rate: 1.0
    no_orphan_pages: true

# Deepening control
deepening:
  max_loops: 9
  max_retries_per_loop: 3
  call_graph_max_depth: 10
  call_graph_max_nodes_per_trace: 5000
  data_flow_max_interprocedural_depth: 5

# Language-specific overrides
language_overrides:
  python:
    tree_sitter_queries: "custom/queries/python/*.scm"
    dynamic_call_patterns: ["importlib", "getattr", "__import__", "eval"]
    di_frameworks: ["fastapi", "flask", "django", "dependency_injector"]
  typescript:
    tree_sitter_queries: "custom/queries/typescript/*.scm"
    dynamic_call_patterns: ["require", "import()", "eval"]
    di_frameworks: ["nestjs", "inversify", "tsyringe"]
  java:
    tree_sitter_queries: "custom/queries/java/*.scm"
    dynamic_call_patterns: ["Class.forName", "Method.invoke", "Constructor.newInstance"]
    di_frameworks: ["spring", "guice", "dagger", "micronaut"]
  go:
    tree_sitter_queries: "custom/queries/go/*.scm"
    dynamic_call_patterns: ["reflect", "plugin"]
    di_frameworks: ["wire", "fx", "dig"]
  csharp:
    tree_sitter_queries: "custom/queries/csharp/*.scm"
    dynamic_call_patterns: ["Activator.CreateInstance", "Assembly.Load", "MethodInfo.Invoke"]
    di_frameworks: ["autofac", "msdi", "ninject", "simpleinjector"]
```

### 7.6.8 Agentic API for Code Deepening

```text
POST /jobs/code/deepen
  Body: { repo_id, commit_sha?, start_loop?, max_loops?, config_overrides? }
  Returns: { job_id, loop_status[], estimated_gpu_minutes }

GET /code/symbols/{symbol_id}
  Returns: full UIR node + all graph neighbors (configurable depth)

GET /code/neighbors
  Query: symbol_id, edge_types[], max_depth
  Returns: subgraph for visualization

GET /code/coverage/{repo_id}
  Returns: per-loop gate status, gap counts, wiki page count, citation density

POST /code/gaps/{gap_id}/resolve
  Body: { disposition, resolution_notes }
  Allows human override of failed gates
```

### 7.6.9 Integration with Research & Docs Pipelines

- **Research → Code**: Web claims about libraries/patterns bind to `code_symbols` via embed + exact name match (`web_documents_symbol` edge, weak)
- **Code → Research**: Undocumented algorithms / novel patterns in code spawn research briefs ("How does X implement Y?")
- **Docs → Code**: FRDs/ADRs bind to use_cases/workflows via semantic similarity + explicit references
- **Shared entity space**: `graphrag_entities` (docs) + `code_symbols` (code) unified in `_entities/` wiki namespace

---

7. Auto-Discovery & Project Classification Engine

The discovery engine is the **first stage** of the pipeline — it recursively walks a given input path (local filesystem directory, mounted drive, or OneDrive Graph folder) and automatically classifies every subfolder and file before anything enters the source registry. It is **language-agnostic** and works identically whether the input is a Python monorepo, a .NET solution, a Java Gradle project, a docs-only folder, or a mixed archive.

### 7.1 Discovery Flow

```text
[Input Path]
  (local dir / mounted drive / OneDrive Graph folder ID)
       |
       v
[Recursive Walker]
  - Walk all children depth-first
  - Apply global ignore rules (§7.3) at every level → prune entire subtrees
  - Collect surviving files into a flat manifest
       |
       v
[Project Fingerprinter]
  - At each directory level, detect project markers (§7.2)
  - Assign project_type, language_ecosystem, and project_root flag
  - Propagate project context DOWN to child files
       |
       v
[Content Classifier]
  - For each surviving file, assign content_class (§7.4) based on:
    1. File extension + MIME
    2. Parent directory name conventions
    3. Project context from fingerprinting
  - Tag with: content_class, extraction_priority, estimated_signal_density
       |
       v
[Discovery Manifest]
  - Write discovery_manifest.json to VPS (or upsert to `sources` table)
  - Each entry: {path, sha256, size, project_type, content_class, priority}
  - Feed directly into §8 pipeline Stage 1 (Ingest & Sync)
```

### 7.2 Project Fingerprinting Rules (Language-Agnostic)

The fingerprinter checks each directory for **marker files** to determine what kind of project it is. Multiple markers can coexist (e.g., a repo with both source code and docs).

| Marker File(s) | Detected `project_type` | `language_ecosystem` |
|---|---|---|
| `.git/` directory | `git_repository` | (inferred from other markers) |
| `.github/`, `.gitlab-ci.yml`, `Jenkinsfile`, `.circleci/` | `ci_cd_config` | — |
| `package.json` | `source_code` | `javascript/typescript` |
| `tsconfig.json`, `angular.json`, `next.config.*` | `source_code` | `typescript` |
| `requirements.txt`, `pyproject.toml`, `setup.py`, `Pipfile` | `source_code` | `python` |
| `*.sln`, `*.csproj`, `*.fsproj` | `source_code` | `dotnet` |
| `pom.xml`, `build.gradle`, `build.gradle.kts` | `source_code` | `java/kotlin` |
| `go.mod` | `source_code` | `go` |
| `Cargo.toml` | `source_code` | `rust` |
| `composer.json` | `source_code` | `php` |
| `Gemfile` | `source_code` | `ruby` |
| `mix.exs` | `source_code` | `elixir` |
| `Makefile`, `CMakeLists.txt` | `source_code` | `c/cpp` |
| `docker-compose.yml`, `Dockerfile` | `infrastructure` | `docker` |
| `*.tf`, `*.tfvars` | `infrastructure` | `terraform` |
| `Chart.yaml`, `values.yaml` | `infrastructure` | `helm/kubernetes` |
| `mkdocs.yml`, `docusaurus.config.*`, `_config.yml` (Jekyll), `book.toml` (mdBook) | `documentation_site` | — |
| `docs/`, `manual/`, `wiki/`, `guides/` (directory names) | `documentation_folder` | — |
| `README.md` alone (no source markers) | `standalone_docs` | — |
| `plan.md`, `ARCHITECTURE.md`, `ADR-*.md`, `RFC-*.md` | `design_documents` | — |
| `*.postman_collection.json`, `openapi.yaml`, `swagger.json` | `api_spec` | — |
| No recognized markers | `unclassified` | `unknown` |

**Propagation rule**: When a directory is identified as `git_repository`, all children inherit `repo_root = <that directory>`. The `language_ecosystem` detected at the root propagates down so that, e.g., a `src/utils/helper.py` file knows it belongs to a Python repo even without its own `pyproject.toml`.

### 7.3 Global Noise Exclusion Rules

These directories and files are **pruned at walk time** — the walker never descends into them and they never enter the source registry. The rules are language-agnostic and cover all major ecosystems.

#### 7.3.1 Directory Exclusion Patterns (Pruned Recursively)

```yaml
# Version control internals
- .git
- .svn
- .hg

# Dependency / package caches (language-agnostic)
- node_modules
- bower_components
- vendor                   # PHP Composer, Go vendor, Ruby bundler
- .bundle
- __pycache__
- .mypy_cache
- .pytest_cache
- .ruff_cache
- .tox
- .nox
- .venv
- venv
- env
- .env.local               # but NOT .env.example (that's config documentation)
- packages                 # NuGet restored packages (when inside .NET obj)

# Build output directories
- bin
- obj
- build
- dist
- out
- target                   # Java/Kotlin Maven/Gradle, Rust Cargo
- _build                   # Elixir Mix
- .next                    # Next.js build
- .nuxt                    # Nuxt.js build
- .output                  # Nitro/Nuxt output
- .turbo                   # Turborepo cache
- .parcel-cache
- .webpack
- .angular
- Debug
- Release
- x64
- x86
- .gradle
- .idea
- .vs
- .vscode                  # IDE settings (not documentation)

# Container / infrastructure artifacts
- .terraform
- .terragrunt-cache
- charts/*/charts          # Helm sub-chart dependencies
- coverage                 # Test coverage reports (auto-generated)
- .nyc_output
- htmlcov
- TestResults

# OS / editor junk
- .DS_Store
- Thumbs.db
- __MACOSX
- .Spotlight-V100
- .Trashes
```

#### 7.3.2 File Exclusion Patterns (Skipped Individually)

```yaml
# Lock files (dependency resolution artifacts, not human knowledge)
- "*.lock"                 # Cargo.lock, poetry.lock, Pipfile.lock, etc.
- package-lock.json
- yarn.lock
- pnpm-lock.yaml
- composer.lock
- Gemfile.lock
- go.sum
- flake.lock

# Compiled / binary artifacts
- "*.pyc"
- "*.pyo"
- "*.class"
- "*.o"
- "*.obj"
- "*.dll"
- "*.exe"
- "*.so"
- "*.dylib"
- "*.wasm"
- "*.min.js"
- "*.min.css"
- "*.map"                  # Source maps
- "*.bundle.js"

# Auto-generated files
- "*.designer.cs"          # .NET WinForms designer
- "*.generated.*"
- "*.g.cs"                 # .NET source generators
- "*.g.dart"               # Dart/Flutter codegen
- ".gitignore"             # Not knowledge content
- ".gitattributes"
- ".editorconfig"
- ".prettierrc*"
- ".eslintrc*"
- "*.tfstate"
- "*.tfstate.backup"

# Large media (unless in docs/assets — handled by §7.4)
- "*.mp4"
- "*.mov"
- "*.avi"
- "*.zip"
- "*.tar.gz"
- "*.rar"
- "*.iso"
```

#### 7.3.3 Configurable Override

All exclusion rules are defined in a single `discovery_rules.yaml` file in the repo root. Operators can:
- **Add** custom ignore patterns per deployment (e.g., `legacy_backups/`).
- **Whitelist** a normally-ignored directory if it contains documentation (e.g., `!vendor/internal-docs/`).
- **Adjust priority** weights for content classes.

```yaml
# discovery_rules.yaml
version: 1
ignore_dirs:
  - node_modules
  - .git
  - bin
  - obj
  # ... (full default list)

ignore_files:
  - "*.lock"
  - "*.pyc"
  # ... (full default list)

whitelist:
  - "vendor/internal-sdk/docs/**"    # Keep docs even inside vendor
  - ".github/ISSUE_TEMPLATE/**"      # GitHub issue templates are documentation

custom_project_markers:
  - marker: "CODEOWNERS"
    project_type: "git_repository"
```

### 7.4 Content Classification Taxonomy

After noise exclusion, every surviving file is assigned a `content_class` that determines its **extraction priority** and **pipeline routing**.

| `content_class` | Description | Extraction Priority | Examples |
|---|---|---|---|
| `architecture_doc` | System design, ADRs, RFCs, ERDs, C4 diagrams | **P0 — Critical** | `ARCHITECTURE.md`, `ADR-*.md`, `RFC-*.md`, `*.drawio`, `*.mermaid` |
| `requirements_spec` | FRDs, SRS, PRDs, user stories, acceptance criteria | **P0 — Critical** | `FRD-*.docx`, `SRS-*.pdf`, `requirements/*.md` |
| `api_specification` | OpenAPI, Swagger, GraphQL schemas, Postman collections | **P0 — Critical** | `openapi.yaml`, `swagger.json`, `*.graphql`, `schema.prisma` |
| `runbook_ops` | Operational runbooks, SOPs, incident playbooks | **P1 — High** | `runbooks/*.md`, `playbook-*.md`, `TROUBLESHOOTING.md` |
| `readme_overview` | Project/module READMEs, getting-started guides | **P1 — High** | `README.md`, `CONTRIBUTING.md`, `CHANGELOG.md` |
| `config_iac` | Infrastructure-as-code, deployment configs, CI/CD | **P1 — High** | `docker-compose.yml`, `*.tf`, `Dockerfile`, `*.k8s.yaml`, `.github/workflows/*.yml` |
| `source_code` | Application source files (business logic, not tests) | **P2 — Standard** | `*.py`, `*.ts`, `*.cs`, `*.java`, `*.go`, `*.rs` |
| `test_code` | Test files, fixtures, mocks, test helpers | **P3 — Low** | `*_test.go`, `test_*.py`, `*.spec.ts`, `*.test.js`, `__tests__/` |
| `data_schema` | Database migrations, SQL schemas, seed data | **P2 — Standard** | `migrations/*.sql`, `schema.sql`, `*.prisma`, `alembic/versions/` |
| `wiki_knowledge` | Internal wiki pages, knowledge base articles | **P0 — Critical** | `wiki/**/*.md`, `knowledge/**/*.md`, `docs/**/*.md` |
| `plan_design` | Project plans, roadmaps, sprint plans, design docs | **P1 — High** | `plan.md`, `ROADMAP.md`, `design/*.md` |
| `communication` | Exported emails, chat logs, meeting notes | **P2 — Standard** | `*.eml`, `*.msg`, `meeting-notes-*.md` |
| `research_reference` | Research papers, whitepapers, external references | **P2 — Standard** | `research/*.pdf`, `papers/*.pdf`, `references/` |
| `asset_media` | Diagrams, screenshots, images inside documentation | **P3 — Low** | `docs/assets/*.png`, `diagrams/*.svg` |
| `noise_generated` | Auto-generated, build output that survived filters | **Skip** | Coverage reports, linting output, benchmark results |

**Tie-break rule (asset vs doc):** when a file could match two classes, the **parent-directory class wins** over the extension class. A diagram/image inside an `architecture_doc` path (e.g. `docs/arch/diagram.png`) is classified `architecture_doc` (P0), not `asset_media` (P3). Only standalone assets not referenced by a doc directory fall to `asset_media`.

#### Classification Decision Tree

```text
For each surviving file F:
  1. Is F inside a directory named test(s)/ or __tests__/ or spec(s)/
     OR does filename match *_test.*, test_*.*, *.spec.*, *.test.*?
     → content_class = "test_code"

  2. Is F inside a directory named docs/, manual/, wiki/, guides/, knowledge/?
     → content_class = "wiki_knowledge" (if .md/.rst/.txt)
     → content_class = "requirements_spec" (if .pdf/.docx and name matches FRD/SRS/PRD)
     → content_class: if image/diagram)

  3. Does filename match ADR-*, RFC-*, ARCHITECTURE.*, DESIGN.*?
     → content_class = "architecture_doc"

  4. Does filename match README.*, CONTRIBUTING.*, CHANGELOG.*?
     → content_class = "readme_overview"

  5. Does filename match openapi.*, swagger.*, *.graphql, *.proto?
     → content_class = "api_specification"

  6. Is F inside migrations/ or has name schema.*, *.sql?
     → content_class = "data_schema"

  7. Is F a source code file (matched by extension from project_type)?
     → content_class = "source_code"

  8. Is F a config/IaC file (Dockerfile, *.tf, *.yml in .github/)?
     → content_class = "config_iac"

  9. Fallback: classify by MIME type or mark "unclassified"
```

### 7.5 Source Code Extraction Strategy (Language-Agnostic)

Source code files (`content_class = "source_code"`) require special extraction treatment. Raw code is not directly useful for a knowledge wiki — instead, the extractor pulls **semantic summaries**:

| What to Extract | How | Output Unit Type |
|---|---|---|
| Module/file-level docstrings & comments | Regex + AST parsing (tree-sitter for any language) | `documentation` |
| Public class/function signatures + docstrings | tree-sitter AST query (`function_definition`, `class_definition`) | `api_surface` |
| Inline `// TODO`, `// FIXME`, `// HACK`, `// NOTE` comments | Regex pattern scan | `developer_note` |
| SQL migration content (CREATE TABLE, ALTER) | SQL parser or raw text | `data_schema` |
| Configuration constants, feature flags | Heuristic: `const`, `enum`, UPPER_CASE assignments | `configuration` |
| Import/dependency graph | AST import statement extraction | `dependency_metadata` (metadata only, not wiki content) |

**What NOT to extract from source code**:
- Function body implementation details (too volatile, low knowledge density)
- Variable assignments, loop bodies, error handling boilerplate
- Test assertions and mock setups (unless they document expected behavior)

---

## 7.6 **Deep Internet Research Feature (Agentic, Loop-Driven)**

### 7.6.1 Intent

Agentic **deep dive research on the internet** that:

- Starts from a **research brief** (user/agent question, wiki gap, orphan API, missing REQ, "what is X in industry?").
- Uses **graph engineering** (sources, claims, entities, contradictions).
- Uses **loop engineering** until coverage/confidence gates pass.
- Writes **cited** Markdown under `research/` with snapshots in evidence.
- **Binds** findings to docs/code entities without overwriting approved internal truth.
- Works for OpenWiki and agents via `POST /jobs/research/deepen`.

### 7.6.2 Research Brief Schema

```yaml
research_id: rsch_...
question: "..."
scope: # optional
  domains_allow: ["*.example.com", "ietf.org", "w3.org", ...]
  domains_deny: ["*"] # deny-all except allow if set
  max_pages: 100
  max_loops: 8
  language: en
seed_urls: []
must_compare_to_wiki: true # flag conflicts with internal pages
authority_floor: web_secondary # never auto-promote above internal approved
```

### 7.6.3 Graph Engineering (Research)

| Graph | Nodes | Edges | Purpose |
|---|---|---|---|
| **Source graph** | url, site, author, publisher, date | links_to, mirrors, same_content | Provenance |
| **Passage graph** | extracted passages | quotes, cites | Evidence atoms |
| **Claim graph** | atomic claims | supports, contradicts, refines, duplicates | Truth tracking |
| **Entity graph** | people, products, standards, orgs | related_to, defined_as | Alignment to wiki `_entities/` |
| **Query graph** | search queries / sub-questions | spawned_from, answered_by | Loop planning |
| **Conflict graph** | claim vs claim / claim vs wiki | conflicts_with | Review queue |

Deterministic first: fetch → extract text → hash → dedup. LLM extracts claims **with mandatory source passage ids**.

### 7.6.4 Loop Engineering (Research Deepen)

```text
Loop 0 Brief normalize — parse question; expand sub-questions; load wiki context (FTS/vectors via VPS)
Loop 1 Seed & search — seed_urls + meta-search/OSS queries; enqueue candidate URLs
Loop 2 Fetch & snapshot — download; store snapshot to OneDrive research_snapshots/; register source
Loop 3 Extract & clean — Trafilatura/etc.; boilerplate kill; language detect; units
Loop 4 Embed & cluster — BGE-M3; near-dup; topic clusters for this research_id
Loop 5 Claim mine — structured claims + evidence spans; entity link to wiki
Loop 6 Gap questions — LLM/graph: unanswered sub-questions, weak citations, contradictions → new queries
Loop 7 Deepen fetch — only gap-driven URLs; increase depth on high-value domains
Loop 8 Synthesize wiki — research pages + comparison to internal wiki; conflict callouts
Loop 9 Bind & gate — link entities/REQs/code symbols; coverage gates; publish or needs_review
```

**Continue while** gates fail and `max_loops` / page budget remain.  
**Stop when** gates pass OR remaining gaps marked `paywalled` / `unreachable` / `out_of_scope` with reason.

### 7.6.5 Research Depth Gates

| Gate | Target |
|---|---|
| Sub-questions with ≥1 supporting claim | 100% or dispositioned |
| Claims with ≥1 snapshot citation | 100% |
| Distinct independent sources for high-impact claims | ≥2 when available |
| Near-duplicate pages collapsed | yes + tombstones |
| Conflicts with internal approved wiki | explicit in `conflicts/` — never silent overwrite |
| Snapshot bytes stored for every cited URL | 100% |
| Research page frontmatter complete | 100% |
| Broken links in published research pages | 0 |

### 7.6.6 Wiki Output (`research/`)

```text
knowledge/research/
 {research_id}/
 brief.md
 overview.md # synthesis — citations only
 claims.md # claim register
 sources.md # bibliography + retrieved_at + hash
 topics/{cluster}.md
 conflicts.md # vs internal wiki and cross-web
 ...
knowledge/source-catalog/web/...
```

**Authority:** web-sourced pages default `status: draft` or `corroborated_web`; promotion to `verified` requires human/policy — **below** internal FRD/ADR unless stewards approve.

**Page contract:** question, scope, method (queries/loops run), findings with citations, limitations, related wiki/code links, `## Sources` with URLs + snapshot paths.

### 7.6.7 Safety, Legality, Quality

- Obey **robots.txt** and site rate limits; identifiable User-Agent.
- **Allowlist/denylist** from brief; default deny open crawl of the whole web without seeds/queries.
- No credentials stuffing; no bypass of paywalls (mark `paywalled`).
- Prompt-injection: web text is **untrusted data**, never instructions.
- PII: optional Presidio before publish.
- Prefer primary sources (specs, papers, official docs) over SEO blogs when both exist (scoring heuristic).

### 7.6.8 Agentic API

```text
POST /api/v1/jobs/research/start    # body: research brief
POST /api/v1/jobs/research/deepen   # research_id + gap reason
GET /api/v1/research/{id}/coverage  # research coverage gate status
GET /api/v1/research/{id}/claims    # claim register
GET /api/v1/research/{id}/sources   # source bibliography
```

OpenWiki/agents can trigger research from a wiki gap or user question; results merge into Git wiki after gates.

### 7.6.9 Bind to Docs and Code

- Entity names from research ↔ `_entities/` and `code_symbols` via embed + exact match.
- Edges: `web_claim_supports_req`, `web_contradicts_wiki_page`, `web_documents_symbol` (weak).
- Never auto-edit approved FRD/code pages from web; only link + conflict records.

---

## 8. PostgreSQL Database Schema (Complete DDL)

```sql
-- Enable necessary extensions on VPS
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";      -- pgvector for dense embeddings
CREATE EXTENSION IF NOT EXISTS "pg_trgm";     -- Trigram indexing for fuzzy search
CREATE EXTENSION IF NOT EXISTS "btree_gin";   -- Multi-column GIN indexing

-- 1. Sync State (Delta tokens & cursors)
CREATE TABLE sync_state (
    sync_key VARCHAR(64) PRIMARY KEY,
    delta_token TEXT,
    last_sync_started_at TIMESTAMPTZ,
    last_sync_completed_at TIMESTAMPTZ,
    total_files_discovered INT DEFAULT 0,
    metadata JSONB DEFAULT '{}'::jsonb
);

-- 2. Sources (Raw Evidence Registry)
CREATE TABLE sources (
    source_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    drive_item_id VARCHAR(255) UNIQUE NOT NULL,
    drive_id VARCHAR(255) NOT NULL,
    file_path TEXT NOT NULL,
    file_name TEXT NOT NULL,
    mime_type VARCHAR(128) NOT NULL,
    size_bytes BIGINT NOT NULL,
    sha256_hash CHAR(64) NOT NULL,
    etag VARCHAR(128),
    security_classification VARCHAR(32) DEFAULT 'internal', -- public, internal, confidential, restricted
    status VARCHAR(32) NOT NULL DEFAULT 'discovered',       -- discovered, downloaded, extracted, indexed, quarantine, error
    error_message TEXT,
    source_metadata JSONB DEFAULT '{}'::jsonb,
    lang VARCHAR(8) DEFAULT 'simple',                       -- FTS language for to_tsvector (mixed/Nepali corpora)
    leased_by TEXT,                                          -- worker id holding the lease
    lease_token UUID,                                        -- unique per claim; reclaim only if mismatched
    heartbeat_at TIMESTAMPTZ,                                -- last worker heartbeat; stale = dead worker
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_sources_sha256 ON sources(sha256_hash);
CREATE INDEX idx_sources_status ON sources(status);
CREATE INDEX idx_sources_path ON sources(file_path);

-- 3. Canonical Units (Atomic extracted blocks with exact provenance)
CREATE TABLE units (
    unit_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_id UUID NOT NULL REFERENCES sources(source_id) ON DELETE CASCADE,
    doc_id VARCHAR(255) NOT NULL,
    unit_index INT NOT NULL,
    parent_unit_id UUID REFERENCES units(unit_id),
    heading_path TEXT[] NOT NULL DEFAULT '{}',
    unit_type VARCHAR(32) NOT NULL,                        -- paragraph, heading, table, code, list_item, diagram_caption
    raw_text TEXT NOT NULL,
    clean_text TEXT NOT NULL,
    char_start INT,
    char_end INT,
    page_number INT,
    bbox_coords JSONB,                                     -- {"x0": 0.1, "y0": 0.2, "x1": 0.9, "y1": 0.3}
    content_hash CHAR(64) NOT NULL,
    disposition VARCHAR(32) NOT NULL DEFAULT 'authoritative', -- authoritative, supporting, historical, superseded, duplicate, boilerplate, low_quality
    quality_score FLOAT DEFAULT 1.0,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_id, unit_index)                            -- idempotency: re-extraction upserts, never duplicates
);
CREATE INDEX idx_units_source_id ON units(source_id);
CREATE INDEX idx_units_content_hash ON units(content_hash);
CREATE INDEX idx_units_disposition ON units(disposition);
CREATE INDEX idx_units_heading_path ON units USING GIN(heading_path);

-- 4. Embedding Cache (BGE-M3 1024d Dense + Sparse Lexical Weights)
CREATE TABLE embed_cache (
    content_hash CHAR(64) PRIMARY KEY,
    model_id VARCHAR(64) NOT NULL DEFAULT 'BAAI/bge-m3',
    dense_vector vector(1024) NOT NULL,
    sparse_weights JSONB,                                  -- {"token_id": weight} for lexical matching
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- 5. Topic Clusters (HDBSCAN Outputs)
CREATE TABLE topic_clusters (
    cluster_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    cluster_label INT NOT NULL,                            -- -1 is noise
    topic_name VARCHAR(255) NOT NULL,
    centroid vector(1024),
    top_keywords TEXT[] NOT NULL,
    unit_count INT DEFAULT 0,
    exemplar_unit_ids UUID[] DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- 6. GraphRAG Knowledge Graph Tables
CREATE TABLE graphrag_entities (
    entity_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(255) NOT NULL,
    entity_type VARCHAR(64) NOT NULL,                      -- SYSTEM, SERVICE, API, PROTOCOL, PERSON, ORG, REQUIREMENT
    description TEXT NOT NULL,
    source_unit_ids UUID[] NOT NULL DEFAULT '{}',
    frequency INT DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(name, entity_type)                              -- natural key; scoped per corpus if multi-tenant
);
CREATE INDEX idx_entities_name ON graphrag_entities(name);

CREATE TABLE graphrag_relationships (
    rel_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_entity_id UUID NOT NULL REFERENCES graphrag_entities(entity_id) ON DELETE CASCADE,
    target_entity_id UUID NOT NULL REFERENCES graphrag_entities(entity_id) ON DELETE CASCADE,
    relationship_type VARCHAR(64) NOT NULL,                -- DEPENDS_ON, IMPLEMENTS, CALLS, OWNED_BY, SUPERSEDES
    description TEXT NOT NULL,
    weight FLOAT DEFAULT 1.0,
    source_unit_ids UUID[] NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_entity_id, target_entity_id, relationship_type)
);

CREATE TABLE graphrag_communities (
    community_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    level INT NOT NULL DEFAULT 0,                          -- Leiden hierarchy level
    title VARCHAR(255) NOT NULL,
    summary TEXT NOT NULL,
    findings JSONB NOT NULL DEFAULT '[]'::jsonb,           -- [{explanation, score}]
    member_entities TEXT[] NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- 7. Cluster Consensus (Consensus between HDBSCAN, GraphRAG, and Heading Hierarchy)
CREATE TABLE cluster_consensus (
    consensus_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    wiki_topic_path TEXT NOT NULL UNIQUE,                  -- e.g. "systems/auth/jwt-rotation"
    hdbscan_cluster_id UUID REFERENCES topic_clusters(cluster_id),
    community_id UUID REFERENCES graphrag_communities(community_id),
    heading_pattern TEXT NOT NULL,
    confidence_score FLOAT NOT NULL,                       -- Combined 3-way score (0.0 to 1.0)
    status VARCHAR(32) NOT NULL DEFAULT 'auto_approved',    -- auto_approved, needs_review, manual_override
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- 8. Fact Claims & Conflict Ledger
CREATE TABLE claims (
    claim_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    object TEXT NOT NULL,
    statement_text TEXT NOT NULL,
    authority_score INT NOT NULL DEFAULT 50,               -- 100 = Policy/ADR, 10 = Chat
    source_unit_id UUID NOT NULL REFERENCES units(unit_id),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_claims_subject ON claims(subject);

CREATE TABLE conflicts (
    conflict_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    topic_path TEXT NOT NULL,
    claim_a_id UUID NOT NULL REFERENCES claims(claim_id),
    claim_b_id UUID NOT NULL REFERENCES claims(claim_id),
    conflict_type VARCHAR(64) NOT NULL,                    -- DIRECT_CONTRADICTION, TEMPORAL_SUPERSEDED, NUMERICAL_DISCREPANCY
    description TEXT NOT NULL,
    severity VARCHAR(32) NOT NULL DEFAULT 'high',          -- low, medium, high, critical
    resolution_status VARCHAR(32) NOT NULL DEFAULT 'open', -- open, resolved_authoritative, accepted_divergence
    resolution_notes TEXT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- 8b. Deduplication Pairs & Tombstones
CREATE TABLE dedup_pairs (
    pair_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    kept_unit_id UUID NOT NULL REFERENCES units(unit_id),
    suppressed_unit_id UUID NOT NULL REFERENCES units(unit_id),
    similarity_score FLOAT NOT NULL,                       -- Jaccard (MinHash) or Cosine (embed)
    method VARCHAR(32) NOT NULL,                           -- exact_sha256, minhash_lsh, embed_cosine
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(kept_unit_id, suppressed_unit_id),
    CHECK (kept_unit_id < suppressed_unit_id)              -- canonical ordering; prevents A→B and B→A both existing
);
CREATE INDEX idx_dedup_suppressed ON dedup_pairs(suppressed_unit_id);

-- 9. Authoritative Wiki Pages (Git Reflection)
CREATE TABLE wiki_pages (
    page_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    file_path TEXT UNIQUE NOT NULL,                        -- e.g. "systems/identity-service.md"
    title TEXT NOT NULL,
    page_type VARCHAR(64) NOT NULL,                        -- domain, system, requirement, adr, runbook, glossary
    domain VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'active',          -- active, draft, deprecated, review
    frontmatter JSONB NOT NULL,
    markdown_body TEXT NOT NULL,
    git_commit_sha VARCHAR(40),
    source_unit_ids UUID[] NOT NULL DEFAULT '{}',
    last_verified_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_wiki_pages_path ON wiki_pages(file_path);

-- 10. Retrieval Chunks (Heading-Aware Sliced from Curated Markdown)
CREATE TABLE wiki_chunks (
    chunk_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    page_id UUID NOT NULL REFERENCES wiki_pages(page_id) ON DELETE CASCADE,
    file_path TEXT NOT NULL,
    heading_path TEXT[] NOT NULL,
    chunk_index INT NOT NULL,
    content TEXT NOT NULL,
    content_hash CHAR(64) NOT NULL,
    dense_vector vector(1024),                              -- nullable: FTS must work when GPU is offline
    sparse_weights JSONB,
    fts_vector tsvector GENERATED ALWAYS AS
        (to_tsvector(COALESCE(NULLIF((metadata->>'lang'),'')::regconfig, 'simple'), content)) STORED,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(page_id, chunk_index)                            -- idempotency: re-index upserts, never duplicates
);

-- Optimized Retrieval Indexes
CREATE INDEX idx_wiki_chunks_page_id ON wiki_chunks(page_id);
CREATE INDEX idx_wiki_chunks_fts ON wiki_chunks USING GIN(fts_vector);
-- Trigram index for mixed-language / non-English corpora where stemming to one language underperforms
CREATE INDEX idx_wiki_chunks_trgm ON wiki_chunks USING GIN (content gin_trgm_ops);
-- HNSW vector index is intentionally NOT created here. Build it AFTER bulk load (see note below).
-- CREATE INDEX CONCURRENTLY idx_wiki_chunks_vector_hnsw ON wiki_chunks
--     USING hnsw (dense_vector vector_cosine_ops) WITH (m = 16, ef_construction = 64);

-- NOTE (index build ordering): create the HNSW index in a follow-up migration
-- (migrations/versions/0002_hnsw_after_load.sql) AFTER the bulk COPY of embed_cache/wiki_chunks:
--   SET maintenance_work_mem = '1GB';
--   CREATE INDEX CONCURRENTLY idx_wiki_chunks_vector_hnsw ... ;
-- Building HNSW before the bulk load makes inserts O(n log n) and slow on the memory-capped VPS.

-- 11. Research Database Tables
CREATE TABLE research_briefs (
    research_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    question TEXT NOT NULL,
    scope JSONB DEFAULT '{}'::jsonb,
    seed_urls TEXT[] DEFAULT '{}',
    domains_allow TEXT[] DEFAULT '{}',
    domains_deny TEXT[] DEFAULT '{}',
    max_pages INT DEFAULT 100,
    max_loops INT DEFAULT 8,
    language VARCHAR(8) DEFAULT 'en',
    authority_floor VARCHAR(32) DEFAULT 'web_secondary',
    status VARCHAR(32) DEFAULT 'active',     -- active, completed, abandoned
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ
);

CREATE TABLE research_queries (
    query_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    research_id UUID NOT NULL REFERENCES research_briefs(research_id) ON DELETE CASCADE,
    query_text TEXT NOT NULL,
    loop_index INT DEFAULT 0,
    status VARCHAR(32) DEFAULT 'pending',     -- pending, completed, skipped, failed
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ
);

CREATE TABLE web_sources (
    source_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    research_id UUID NOT NULL REFERENCES research_briefs(research_id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    retrieved_at TIMESTAMPTZ,
    content_hash CHAR(64),
    snapshot_path TEXT,
    http_status INT,
    status VARCHAR(32) DEFAULT 'discovered',   -- discovered, fetched, extracted, skipped, paywalled, unreachable
    title TEXT,
    robots_txt_status VARCHAR(16) DEFAULT 'allowed'
);

CREATE TABLE research_passages (
    passage_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_id UUID NOT NULL REFERENCES web_sources(source_id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    extracted_at TIMESTAMPTZ,
    content_hash CHAR(64),
    language VARCHAR(8) DEFAULT 'en',
    passage_index INT,
    url_selector TEXT,
    span_start INT,
    span_end INT
);

CREATE TABLE research_claims (
    claim_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    passage_id UUID NOT NULL REFERENCES research_passages(passage_id) ON DELETE CASCADE,
    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    object TEXT NOT NULL,
    statement_text TEXT NOT NULL,
    authority_score INT NOT NULL DEFAULT 50,
    support_count INT DEFAULT 1,
    contradict_count INT DEFAULT 0,
    status VARCHAR(32) DEFAULT 'active',     -- active, verified, refuted, superseded
    entity_links JSONB DEFAULT '{}'::jsonb
);

CREATE TABLE research_edges (
    edge_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    claim_a_id UUID NOT NULL REFERENCES research_claims(claim_id) ON DELETE CASCADE,
    claim_b_id UUID NOT NULL REFERENCES research_claims(claim_id) ON DELETE CASCADE,
    relationship_type VARCHAR(32) NOT NULL,   -- supports, contradicts, refines, duplicate
    strength FLOAT DEFAULT 1.0
);

CREATE TABLE research_loops (
    loop_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    research_id UUID NOT NULL REFERENCES research_briefs(research_id) ON DELETE CASCADE,
    loop_index INT NOT NULL,
    started_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ,
    status VARCHAR(32) DEFAULT 'running',     -- running, completed, abandoned
    new_queries_generated INT DEFAULT 0,
    gaps_disposed INT DEFAULT 0,
    coverage_score FLOAT DEFAULT 0.0
);

CREATE TABLE research_gaps (
    gap_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    research_id UUID NOT NULL REFERENCES research_briefs(research_id) ON DELETE CASCADE,
    query_id UUID REFERENCES research_queries(query_id),
    description TEXT NOT NULL,
    reason VARCHAR(32) NOT NULL,              -- paywalled, unreachable, out_of_scope, answered
    dispositioned_at TIMESTAMPTZ,
    resolved_by UUID REFERENCES research_claims(claim_id)
);

-- 12. Prefect Work Pools (GPU)
- `gpu-docs` — document curation
- `gpu-code` — parse + code deepen loops
- `gpu-research` — internet deep dive loops

-- 13. Code Database Tables (Deep Code-to-Wiki)
-- Repo registry
CREATE TABLE repos (
    repo_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name TEXT NOT NULL UNIQUE,
    git_url TEXT,
    onedrive_path TEXT,
    default_branch TEXT DEFAULT 'main',
    languages TEXT[] DEFAULT '{}',
    config JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- Code symbols (unified IR nodes)
CREATE TABLE code_symbols (
    symbol_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    repo_id UUID NOT NULL REFERENCES repos(repo_id),
    commit_sha CHAR(40) NOT NULL,
    file_path TEXT NOT NULL,
    language VARCHAR(32) NOT NULL,
    kind VARCHAR(32) NOT NULL,
    name TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    parent_symbol_id UUID REFERENCES code_symbols(symbol_id),
    signature TEXT,
    docstring TEXT,
    visibility VARCHAR(16) NOT NULL,
    is_static BOOLEAN DEFAULT FALSE,
    is_async BOOLEAN DEFAULT FALSE,
    is_abstract BOOLEAN DEFAULT FALSE,
    byte_start INT NOT NULL,
    byte_end INT NOT NULL,
    line_start INT NOT NULL,
    line_end INT NOT NULL,
    metadata JSONB DEFAULT '{}'::jsonb,
    uir_hash CHAR(64) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(repo_id, commit_sha, uir_hash)
);
CREATE INDEX idx_symbols_qualified ON code_symbols(qualified_name);
CREATE INDEX idx_symbols_repo_commit ON code_symbols(repo_id, commit_sha);

-- Code edges (all graph layers)
CREATE TABLE code_edges (
    edge_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    repo_id UUID NOT NULL REFERENCES repos(repo_id),
    commit_sha CHAR(40) NOT NULL,
    source_symbol_id UUID NOT NULL REFERENCES code_symbols(symbol_id),
    target_symbol_id UUID NOT NULL REFERENCES code_symbols(symbol_id),
    edge_type VARCHAR(32) NOT NULL,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_symbol_id, target_symbol_id, edge_type)
);
CREATE INDEX idx_edges_source ON code_edges(source_symbol_id);
CREATE INDEX idx_edges_target ON code_edges(target_symbol_id);
CREATE INDEX idx_edges_type ON code_edges(edge_type);

-- Use cases (business operations)
CREATE TABLE use_cases (
    use_case_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    repo_id UUID NOT NULL REFERENCES repos(repo_id),
    name TEXT NOT NULL,
    description TEXT,
    entry_point_symbol_ids UUID[] NOT NULL,
    touched_symbol_ids UUID[] NOT NULL,
    mutation_symbol_ids UUID[] DEFAULT '{}',
    external_call_ids UUID[] DEFAULT '{}',
    status VARCHAR(32) DEFAULT 'active',
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(repo_id, name)
);

-- Workflows (multi-step orchestrations)
CREATE TABLE workflows (
    workflow_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    repo_id UUID NOT NULL REFERENCES repos(repo_id),
    name TEXT NOT NULL,
    pattern VARCHAR(32) NOT NULL,
    description TEXT,
    idempotency_key_pattern TEXT,
    retry_policy JSONB,
    timeout_seconds INT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(repo_id, name)
);

CREATE TABLE workflow_steps (
    step_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    workflow_id UUID NOT NULL REFERENCES workflows(workflow_id) ON DELETE CASCADE,
    step_index INT NOT NULL,
    name TEXT NOT NULL,
    symbol_ids UUID[] NOT NULL,
    input_data JSONB,
    output_data JSONB,
    compensation_symbol_ids UUID[] DEFAULT '{}',
    is_parallel BOOLEAN DEFAULT FALSE,
    depends_on_step_ids UUID[] DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(workflow_id, step_index)
);

-- Deepening loop tracking
CREATE TABLE deepen_loops (
    loop_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    repo_id UUID NOT NULL REFERENCES repos(repo_id),
    commit_sha CHAR(40) NOT NULL,
    loop_index INT NOT NULL,
    status VARCHAR(32) NOT NULL,
    gate_results JSONB DEFAULT '{}'::jsonb,
    metrics JSONB DEFAULT '{}'::jsonb,
    started_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ,
    UNIQUE(repo_id, commit_sha, loop_index)
);

-- Code gaps (failed gates, manual review items)
CREATE TABLE code_gaps (
    gap_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    repo_id UUID NOT NULL REFERENCES repos(repo_id),
    loop_index INT NOT NULL,
    gap_type VARCHAR(64) NOT NULL,
    description TEXT NOT NULL,
    symbol_ids UUID[] DEFAULT '{}',
    severity VARCHAR(16) DEFAULT 'medium',
    disposition VARCHAR(32) DEFAULT 'open',
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMPTZ
);

-- 14. Coverage Reports (per wiki page, tracks unit coverage metrics)
CREATE TABLE coverage_reports (
    report_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    page_id UUID NOT NULL REFERENCES wiki_pages(page_id) ON DELETE CASCADE,
    total_units INT NOT NULL,
    covered_units INT NOT NULL,
    coverage_score FLOAT NOT NULL,                         -- covered_units / total_units
    uncovered_unit_ids UUID[] DEFAULT '{}',
    job_id UUID REFERENCES pipeline_jobs(job_id),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_coverage_page ON coverage_reports(page_id);

-- Database Roles
-- gpu_worker: R/W on all pipeline tables (sources, units, embed_cache, clusters, wiki_*, jobs)
-- readonly:   SELECT-only on wiki_pages, wiki_chunks, graphrag_*, claims (for OpenWiki / consumers)
-- CREATE ROLE gpu_worker WITH LOGIN PASSWORD '...' ;
-- CREATE ROLE readonly WITH LOGIN PASSWORD '...' ;
-- GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO gpu_worker;
-- GRANT SELECT ON wiki_pages, wiki_chunks, graphrag_entities, graphrag_relationships,
--   graphrag_communities, claims, conflicts, topic_clusters TO readonly;
```

---

## 9. Detailed GPU Processing Pipeline & Algorithmic Specifications

```text
+-----------------------------------------------------------------------------------------+
|                               STAGE-BY-STAGE GPU PIPELINE                               |
+-----------------------------------------------------------------------------------------+
| [0. Auto-Discovery]       Recursive walk -> Fingerprint -> Classify -> Filter noise     |
| [1. Ingest & Sync]         Poll Graph API /delta -> Stream raw files -> SHA-256 verify  |
| [2. Extraction & Validate] Docling AST parse -> Extract units (headings/tables/text)     |
| [3. Quality & Norm]        Clean whitespace -> Header/footer strip -> Score quality     |
| [4. Dedup & Tombstone]     MinHash LSH (0.85 Jaccard) -> Tombstone exact/near duplicates|
| [5. Batch Embedding]       BAAI/bge-m3 dense (1024d) + sparse weights -> Cache on VPS   |
| [6. HDBSCAN Clustering]    UMAP dim-reduction (1024d->15d) -> HDBSCAN -> c-TF-IDF labels|
| [7. GraphRAG Extraction]   LiteLLM prompt representative units -> Entities/Rels/Leiden  |
| [8. 3-Way Consensus]       Compute score: 0.40*HDBSCAN + 0.35*GraphRAG + 0.25*Headings   |
| [9. Claim & Conflict]      Extract fact claims -> Authority ladder check -> Log conflict|
| [10. Markdown Compilation] Synthesize page -> Calculate sentence coverage >= 95%        |
| [11. Git Publish & Index]  Push Markdown to Git -> Heading chunk -> Index in pgvector   |
+-----------------------------------------------------------------------------------------+
```

### 9.0 GPU Cost / Latency Budget (sizing gate — run before Phase 3)

GraphRAG entity extraction and Markdown compilation are LLM-token-bound. On a local model over ~100 GB raw corpus this can be **days** of GPU time; size it before committing to a tier:

```text
Sizing (Tier 2 / L4, Qwen2.5-14B-AWQ):
  est. units after dedup      : <N>            # measure in Phase 1 smoke test
  GraphRAG extraction tokens  : units × ~1.5k tok
  Community report tokens     : clusters × ~3k tok   # only clusters > 20 units
  Markdown compile tokens     : pages × ~4k tok
  est. GPU-hours              : <derived from throughput benchmark in Phase 2>

Gate: if projected GPU-hours > session/tier budget, sub-sample GraphRAG to the
      top-K clusters by unit_count and defer the long tail to a later run.
```

### 8.1 3-Way Topic Consensus Algorithm (Mathematical Definition)
For every candidate knowledge unit $u_i \in U$, we determine its target Wiki page path $T(u_i)$ by computing the consensus agreement across three independent modalities:

1. **HDBSCAN Density Topic ($C_{\text{hdb}}$)**: Derived from the cosine distance matrix of BGE-M3 embeddings.
2. **GraphRAG Community ($C_{\text{graph}}$)**: Derived from the Leiden hierarchical community detection on entity co-occurrence.
3. **Heading Hierarchy Pattern ($C_{\text{head}}$)**: Normalized document structural breadcrumb (e.g., `["Architecture", "Authentication", "Token Refresh"]`).

$$\text{ConsensusScore}(u_i, T) = w_1 \cdot \text{Sim}(E(u_i), \mu_{\text{hdb}}) + w_2 \cdot \text{Sim}(E(u_i), \mu_{\text{graph}}) + w_3 \cdot \text{Match}(H(u_i), T)$$

Where:
- Weights: $w_1, w_2, w_3$ — **initial priors** 0.40 / 0.35 / 0.25, stored as tunable config (see below), not hard-coded.
- $\text{Sim}(a, b) = \frac{a \cdot b}{\|a\| \|b\|}$ (Cosine similarity of dense embeddings)
- $\text{Match}(H(u_i), T) = \text{LevenshteinTokenOverlap}(H(u_i), \text{PathSegments}(T)) \in [0.0, 1.0]$

> **Tunable constants (Edit 9):** consensus weights and thresholds, coverage thresholds, and the dedup band are *unvalidated priors*. They live in `policies/publication_gates.yaml` and are calibrated in Phase 4 against the gold set — not baked into prose:
> ```yaml
> consensus_weights:    {hdbscan: 0.40, graphrag: 0.35, headings: 0.25}
> consensus_thresholds: {auto_approve: 0.72, split: 0.50}
> coverage:             {unit_recall_cosine: 0.88, page_min: 0.95}
> dedup:                {minhash_jaccard: 0.85, review_band: [0.80, 0.88]}
> ```

**Gating Decision Rules** (thresholds below read from `consensus_thresholds` config):
- **Auto-Approve ($\ge$ `auto_approve`, prior 0.72)**: Unit is automatically assigned to Wiki topic path $T$.
- **Split / Secondary Section (`split` $\le \text{Score} <$ `auto_approve`)**: Unit is assigned as a sub-topic or supporting subsection.
- **Review Queue ($<$ `split`)**: Unit is routed to `conflicts/unresolved_topics.md` and flagged in the Control API for human operator attention.

### 8.2 Lossless Markdown Synthesis & Coverage Verification
The compilation engine runs on the GPU using the local LLM (`Qwen2.5-14B` or larger). It synthesizes structured Wiki pages adhering to these strict guarantees:

1. **Information Completeness (Sentence Recall Metric $\ge 95\%$)**:
   - For every non-boilerplate source unit $u_k$ belonging to a Wiki topic, the synthesized Markdown page $M$ is split into sentences $\{s_1, s_2, \dots, s_m\}$.
   - We compute maximum cosine semantic recall:
     $$\text{Recall}(u_k, M) = \max_{j} \text{CosineSim}(E(u_k), E(s_j))$$
   - A unit is **covered** if $\text{Recall}(u_k, M) \ge 0.88$.
   - **Quality Gate**: Overall Page Coverage $= \frac{\sum_{k=1}^N \mathbb{I}(\text{Recall}(u_k, M) \ge 0.88)}{N} \ge 0.95$. If $< 0.95$, the compilation step automatically falls back to an append-only raw extraction block.
2. **100% Citation Enforcement**:
   - Every factual assertion, configuration parameter, API endpoint, or date MUST include an explicit footnote citation: `[^src_12a3:unit_45b6]`.
   - Statements without direct evidence are forbidden or explicitly wrapped in `> [!WARNING] Inferred by Pipeline: <rationale>`.

---

## 10. Authority Ladder & Conflict Resolution Framework

### 9.1 Corporate Authority Ladder (Priority Order)

| Tier | Source Artifact Type | Authority Weight ($W_A$) | Conflict Behavior |
|---|---|---|---|
| **Tier 1** | Regulatory mandates, Executed Contracts, Security Policies | **100** | Overrides all lower tiers automatically. |
| **Tier 2** | Approved Architecture Decision Records (ADRs), Signed PRDs/FRDs | **90** | Overrides implementation code if within 6 months. |
| **Tier 3** | Production Code, IaC Configs (Terraform/K8s), OpenAPI Specs | **80** | Definitive for *actual current behavior*. |
| **Tier 4** | Engineering Runbooks, Standard Operating Procedures (SOPs) | **70** | Authoritative for operational workflows. |
| **Tier 5** | Maintained Internal Wikis, Knowledge Base Articles | **55** | Informative; subject to verification. |
| **Tier 6** | Closed Jira/Linear Tickets, Merged Pull Request Descriptions | **45** | Historical context for decisions. |
| **Tier 7** | Internal Tech Talks, Presentation Decks, Whitepapers | **35** | Supporting reference. |
| **Tier 8** | Recorded Email Decisions, Formal Slack Announcements | **25** | Informal agreement; flag if contradicting Tier 1–4. |
| **Tier 9** | Transient Chat Logs (Slack/Teams general channels) | **10** | Low confidence; cannot override documented specs. |
| **Tier 10** | LLM Synthesis / Inference without direct citation | **0** | Never used as authoritative truth. |

### 9.2 Conflict Rendering Template in Wiki Markdown
When two sources in Tiers 2–6 disagree (e.g., old PRD specifies OAuth2 Implicit Flow, while newer ADR specifies PKCE):

```markdown
> [!WARNING] Active Specification Conflict (Severity: High)
> **Topic**: User Authentication Token Exchange
> - **Variant A (ADR-042 - 2026-03-15)** [^src_01:u_88]: Mandates Authorization Code Flow with PKCE (Authority: 90).
> - **Variant B (FRD-CoreService - 2024-11-10)** [^src_09:u_14]: References Client Credentials Flow (Authority: 90, Superseded).
> **Status**: Auto-flagged. Variant A selected as active based on timestamp precedence; Variant B retained in historical section.
```

---

## 11. Wiki Information Architecture & File Layout

```text
knowledge/
├── index.md                               # Global knowledge graph summary & catalog
├── domains/                               # Business and functional domain overviews
│   ├── identity-and-access/
│   │   ├── index.md
│   │   └── rbac-matrix.md
│   └── billing-pipeline/
│       └── revenue-recognition.md
├── systems/                               # System and component technical architectures
│   ├── core-api/
│   │   ├── architecture.md
│   │   └── data-model.md
│   └── ingestion-worker/
├── requirements/                          # Governed functional & technical requirements
│   ├── srs-auth-v2.md
│   └── frd-export-compliance.md
├── decisions/                             # Architecture Decision Records (ADRs)
│   ├── adr-001-pgvector-selection.md
│   └── adr-002-litellm-gateway.md
├── operations/                            # Runbooks & operational procedures
│   └── runbooks/
│       ├── database-failover.md
│       └── secret-rotation.md
├── glossary/                              # Corporate taxonomy & acronym directory
│   └── enterprise-terms.md
├── conflicts/                             # Active and historical conflict records
│   ├── open-conflicts.md
│   └── resolved-log.md
├── source-catalog/                        # Traceability manifests mapped to OneDrive
│   └── manifest-2026-q3.md
└── assets/                                # Extracted SVG/PNG diagrams with provenance
    └── diagrams/
```

### 10.1 Standard Page Frontmatter Specification (JSON Schema Equivalent)
```yaml
---
id: "sys_core_api_arch"
title: "Core API Architecture & Microservice Contracts"
page_type: "system_architecture"
domain: "platform-engineering"
status: "active" # active | draft | deprecated | review
security_classification: "internal"
owners:
  - "team-platform@company.internal"
  - "davidsilwal"
last_verified_at: "2026-08-20T10:00:00Z"
source_ids:
  - "c8f1e9b2-3a4d-4e5f-8a1b-2c3d4e5f6a7b"
  - "a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d"
community_ids:
  - "comm_platform_routing_04"
tags:
  - "rest-api"
  - "fastapi"
  - "authentication"
coverage_score: 0.98
unresolved_conflicts: 0
---
```

---

## 12. FastAPI Control API Specification (VPS)

The Control API runs behind Caddy on port 443 with TLS and bearer token authentication.

```text
POST   /api/v1/sources/register       # Register discovered OneDrive items
POST   /api/v1/units/batch            # Bulk upsert canonical extracted units
POST   /api/v1/embeddings/batch       # Bulk upsert BGE-M3 vectors into embed_cache
POST   /api/v1/clusters/batch         # Upsert topic clusters & consensus mappings
POST   /api/v1/graphrag/sync          # Upsert entities, relationships, communities
POST   /api/v1/claims/conflicts       # Record extracted claims & conflict pairs
POST   /api/v1/wiki/publish           # Push compiled Markdown -> Git commit + pgvector
GET    /api/v1/search/fts             # Lexical full-text search (GPU offline ready)
POST   /api/v1/search/hybrid          # Hybrid RRF search (accepts dense vector from client)
POST   /api/v1/jobs/checkpoint        # Update job progress & lease heartbeat
GET    /api/v1/health                 # Health check (Postgres, Redis, Disk space)

# Research
POST   /api/v1/jobs/research/start    # Start research from brief
POST   /api/v1/jobs/research/deepen   # Deepen existing research
GET    /api/v1/research/{id}/coverage # Research coverage gate status
GET    /api/v1/research/{id}/claims   # Claim register
GET    /api/v1/research/{id}/sources  # Source bibliography
```

### 11.1 Hybrid Search Implementation with Reciprocal Rank Fusion (RRF)
When a query arrives, if `query_vector` is provided by the client/GPU worker, FastAPI executes an RRF merge inside PostgreSQL:

```sql
WITH fts_results AS (
    SELECT 
        chunk_id, 
        ROW_NUMBER() OVER (ORDER BY ts_rank_cd(fts_vector, websearch_to_tsquery(COALESCE(NULLIF(:lang,'')::regconfig, 'simple'), :query_text)) DESC) as fts_rank
    FROM wiki_chunks
    WHERE fts_vector @@ websearch_to_tsquery(COALESCE(NULLIF(:lang,'')::regconfig, 'simple'), :query_text)
    LIMIT 50
),
dense_results AS (
    SELECT 
        chunk_id, 
        ROW_NUMBER() OVER (ORDER BY dense_vector <=> :query_vector) as dense_rank
    FROM wiki_chunks
    WHERE dense_vector IS NOT NULL
      AND :query_vector IS NOT NULL
    ORDER BY dense_vector <=> :query_vector
    LIMIT 50
)
SELECT 
    w.chunk_id,
    w.file_path,
    w.heading_path,
    w.content,
    w.metadata,
    COALESCE(1.0 / (60 + f.fts_rank), 0.0) + 
    COALESCE(1.0 / (60 + d.dense_rank), 0.0) AS rrf_score
FROM wiki_chunks w
LEFT JOIN fts_results f ON w.chunk_id = f.chunk_id
LEFT JOIN dense_results d ON w.chunk_id = d.chunk_id
WHERE f.chunk_id IS NOT NULL OR d.chunk_id IS NOT NULL
ORDER BY rrf_score DESC
LIMIT :top_k;
```

---

## 13. Fault Tolerance, Idempotency & Colab Disconnect Recovery

Because Google Colab notebooks are subject to disconnects and 12-hour session limits:

```text
                                [GPU Session Starts]
                                         |
                                         v
        Query VPS (stage-aware resume — do NOT blanket-reset):
          SELECT source_id, status FROM sources
          WHERE status IN ('discovered','error')                -- not-started / failed only
             OR (status='processing'
                 AND heartbeat_at < NOW() - INTERVAL '30 min'
                 AND lease_token IS DISTINCT FROM current_token) -- verified-dead worker
          -- Sources in 'extracted'/'embedded'/'clustered' resume at their NEXT stage;
          -- they are NOT re-extracted. Reclaim requires both a stale heartbeat AND a
          -- lease_token mismatch so a slow-but-alive worker is never reset.
                                         |
            +----------------------------+----------------------------+
            |                                                         |
            v                                                         v
   [Unprocessed Sources]                                    [Partially Extracted]
   - Download raw file                                      - Check `units` table by source_id
   - Compute SHA-256                                        - If units present, skip extract
   - Extract units -> Flush to VPS                          - Proceed directly to Embed / Cluster
            |                                                         |
            +----------------------------+----------------------------+
                                         |
                                         v
                     [Batch Embed via BGE-M3 (Batch size: 64)]
                     - Check `embed_cache` by content_hash
                     - Embed only MISSING content hashes
                     - Direct COPY to Postgres `embed_cache`
                                         |
                                         v
                     [GraphRAG & Leiden Community Execution]
                     - Save community checkpoints to `graphrag_communities`
                                         |
                                         v
                     [Wiki Compile & Git Commit]
                     - Atomic Git transaction on VPS
                     - Sliced chunks indexed to `wiki_chunks`
                     - Mark source `status = 'indexed'`
```

1. **Unit-Level Content Hashing**: Every extracted unit has `content_hash = SHA256(clean_text)`. Re-running extraction produces identical hashes, making embeddings immediately cacheable without re-computation.
2. **Leased Job Execution**: Workers claim batches using atomic leases with `FOR UPDATE SKIP LOCKED` for safe concurrency:
   ```sql
   UPDATE sources SET status = 'processing', leased_by = :worker_id,
          lease_token = gen_random_uuid(), heartbeat_at = NOW(), updated_at = NOW()
   WHERE source_id IN (
       SELECT source_id FROM sources
       WHERE status = 'discovered'
       ORDER BY created_at
       LIMIT 50
       FOR UPDATE SKIP LOCKED
   )
   RETURNING *;
   ```
3. **Stale Lease Reclamation (verified-dead only)**: a VPS-side Prefect flow reclaims a lease only when the worker is provably dead — stale heartbeat AND a token mismatch — and never resets a source that has already advanced past `processing`:
   ```sql
   UPDATE sources SET status = 'discovered', leased_by = NULL, lease_token = NULL
   WHERE status = 'processing'
     AND heartbeat_at < NOW() - INTERVAL '30 minutes'
     AND lease_token IS DISTINCT FROM :current_token;
   -- Workers refresh heartbeat_at every ~2 min. A live worker's token matches, so it is skipped.
   ```

---

## 14. Quality Gates & Production Metrics

| Pipeline Stage | Evaluation Metric | Production Gate Threshold | Action on Gate Failure |
|---|---|---|---|
| **Text Extraction** | Character Corruption / Unicode Replacement Rate | $< 0.1\%$ corrupted characters | Route file to fallback OCR engine (`Tesseract`/`Docling`). |
| **Deduplication** | Near-duplicate Precision & Recall | Recall $\ge 98\%$, Precision $\ge 95\%$ | Flag borderline duplicates ($0.80 \le \text{Sim} \le 0.88$) for review. |
| **Topic Coherence** | HDBSCAN Normalized Pointwise Mutual Info (NPMI) | $\text{NPMI} \ge 0.45$ | Re-cluster with fine-grained minimum cluster size parameter. |
| **3-Way Consensus** | Agreement with manual gold classification | $\ge 80\%$ agreement on gold sample | Trigger manual topic assignment modal. |
| **Markdown Compilation** | Sentence-level Semantic Coverage ($\ge 0.88$ Cosine) | $\ge 95\%$ of unit sentences covered | Halt publication; generate additive raw-unit appendix page. |
| **Fact Citations** | Citation Density for Factual Assertions | $100\%$ claims cited or labeled inferred | Block Git merge to `main`; flag in review branch. |
| **Retrieval Accuracy** | Recall@10 on 100 Gold Benchmark Queries | $\ge 88\%$ retrieval recall | Re-index with updated chunk heading overlap parameters. |
| **End-to-End Faithfulness** | LLM answer faithfulness (RAG-Triad / Ragas) | $\ge 92\%$ grounded in cited context | Quarantine violating page; trigger recompilation. |
| **Research Deep Dive** | Claim citations + snapshots + sub-question coverage + no silent overwrite | All 8 gates in §6.5 pass | Halt publish; disposition gaps (`paywalled`/`unreachable`/`out_of_scope`/`answered`) or continue loops. |

---

## 15. Repository Layout & Project Blueprint

```text
rag-pipeline/
├── .env.example                               # Environment variable templates
├── docker-compose.yml                         # VPS control profile (Postgres, Prefect, API, Caddy)
├── README.md                                  # Operator onboarding and setup instructions
│
├── apps/
│   └── control_api/                           # FastAPI Control Plane Application
│       ├── main.py                            # FastAPI entry point & middleware
│       ├── config.py                          # Pydantic Settings & environment validation
│       ├── database.py                        # SQLAlchemy 2.0 async engine & connection pool
│       ├── models/                            # SQLAlchemy ORM models (Sources, Units, Chunks)
│       ├── schemas/                           # Pydantic request/response validation schemas
│       ├── routers/                           # API endpoints (sources, units, wiki, search)
│       └── services/                          # Business logic (Git manager, RRF hybrid search)
│
├── workers/
│   ├── gpu_worker/                            # GPU Worker Pipeline Package
│   │   ├── discovery.py                       # Recursive project discovery & classifier (§7)
│   │   ├── graph_client.py                    # Microsoft Graph API client (OAuth2, delta sync)
│   │   ├── extractors/                        # Docling, MarkItDown, OCRmyPDF, tree-sitter wrappers
│   │   ├── dedup.py                           # MinHash / SimHash LSH deduplication engine
│   │   ├── embedder.py                        # BAAI/bge-m3 dense + sparse embedding pipeline
│   │   ├── clustering.py                      # UMAP + HDBSCAN + c-TF-IDF keyword extractor
│   │   ├── graphrag_engine.py                 # GraphRAG entity/community extractor via LiteLLM
│   │   ├── consensus.py                       # 3-Way consensus scoring & topic resolver
│   │   ├── claims_conflicts.py                # Authority ladder & contradiction detector
│   │   ├── markdown_compiler.py               # Lossless Markdown synthesizer & coverage verifier
│   │   └── chunker.py                         # Heading-aware Markdown chunker for pgvector
│   └── vps_thin/                              # VPS Maintenance & Git Housekeeping Worker
│       └── git_ops.py                         # Git commit, push, branch, and re-indexing hooks
│
├── notebooks/
│   ├── 01_colab_full_pipeline_runner.ipynb    # One-click Colab execution notebook
│   └── 02_deepnote_interactive_eval.ipynb     # Interactive curation & evaluation notebook
│
├── migrations/                                # Alembic database migrations
│   ├── env.py
│   └── versions/
│
├── policies/                                  # Governance & Authority Rules
│   ├── discovery_rules.yaml                   # Global ignore patterns & project marker overrides
│   ├── authority_matrix.yaml                  # Source type priority weights
│   └── publication_gates.yaml                 # Metric thresholds (coverage, citations)
│
├── templates/                                 # Jinja2 / Markdown Page Templates
│   ├── system_architecture.md.j2
│   ├── requirement_spec.md.j2
│   ├── adr_record.md.j2
│   └── runbook.md.j2
│
├── tests/                                     # Automated test suites
│   ├── unit/                                  # Discovery, chunker, dedup, consensus, extractor tests
│   ├── integration/                           # FastAPI, PostgreSQL, Graph mock tests
│   └── eval/                                  # Gold dataset & Ragas evaluation runner
│
└── docs/
    ├── DEPLOY_VPS.md                          # Step-by-step VPS provisioning guide
    ├── DEPLOY_GPU.md                          # Colab & Deepnote runtime setup guide
    └── OPERATOR_MANUAL.md                     # Conflict resolution & review manual
```

> **Reuse vs Build (Edit 15):** before greenfield implementation, evaluate the existing `code_to_wiki` extractors/chunkers as the library behind `workers/gpu_worker/extractors/` and `chunker.py`. Record the decision in `decisions/adr-003-extraction-library.md`. Do not rebuild extraction logic that already exists and is tested.

---

## 16. Phased Implementation Roadmap for Agents

```text
[Phase 0: VPS Infrastructure] ---> [Phase 1: Discovery & Extraction Smoke]
               |                                        |
               v                                        v
[Phase 2: GPU Core Curation]  ---> [Phase 3: Markdown Compiler & Git]
               |                                        |
               v                                        v
[Phase 4: Eval & Quality Gates] -> [Phase 5: Search & OpenWiki API]
```

### Phase 0: VPS Control Plane Setup
- [ ] Initialize `docker-compose.yml` with PostgreSQL 16 (`pgvector/pgvector:pg16`), Prefect 3.x, Redis, Caddy, and FastAPI.
- [ ] **Generate Postgres TLS certs first** (§5.0 `migrations/init-certs.sh`) and mount them into the `postgres` container — required before `ssl=on` will start.
- [ ] Execute initial database migration creating all tables with GIN indexes. **Defer the HNSW vector index** to `0002_hnsw_after_load.sql` (run after bulk load, §8 note).
- [ ] Configure Caddy reverse proxy with automatic TLS on port 443.
- [ ] Verify external network connectivity to ports 443, 4200, and 5432 using public `VPS_IP`.
- [ ] Observability (Edit 16): structured JSON logs (uvicorn/prefect) → stdout with docker rotation; `/metrics` on control-api (optional); a Prefect automation that alerts when `pipeline_jobs.status='failed'` (≥1) or the stale-heartbeat reclaimer fires (>5/hr).

### Phase 1: Auto-Discovery, Multi-Source Ingestion & Extraction Smoke Test
- [ ] Implement `discovery.py` with multi-source adapters (Local FS, Mounted Drive, OneDrive Graph folder).
- [ ] Implement language-agnostic project fingerprinting, global noise exclusion (`node_modules`, `.git`, `bin`, `obj`, lockfiles), and content taxonomy classification (`P0`–`P3`).
- [ ] Implement `graph_client.py` using MSAL for OAuth2 client credentials authentication and delta sync.
- [ ] Build `Docling` + fallback extractor + code AST parser pipeline for Rich Docs, Markdown, and source code.
- [ ] Test end-to-end extraction on 10 sample heterogeneous project folders and verify canonical `units` insertion via FastAPI / SQL.

### Phase 2: GPU Curation Core (Embeddings, Clustering, GraphRAG)
- [ ] Integrate `BAAI/bge-m3` embedding pipeline with batching and PostgreSQL `embed_cache`.
- [ ] Implement MinHash LSH deduplication ($0.85$ threshold) with tombstoning.
- [ ] Build HDBSCAN topic clustering with UMAP dimensionality reduction.
- [ ] Integrate Microsoft GraphRAG OSS with local `vLLM` / `Ollama` via LiteLLM.
- [ ] Implement 3-way consensus engine (weights read from `policies/publication_gates.yaml`, not hard-coded).
- [ ] **Throughput benchmark** (feeds §9.0): measure tokens/sec for GraphRAG extraction + Markdown compile on the target GPU; derive projected GPU-hours; if over budget, sub-sample GraphRAG to top-K clusters.
- [ ] **After bulk load**, build the deferred HNSW index: `SET maintenance_work_mem='1GB'; CREATE INDEX CONCURRENTLY idx_wiki_chunks_vector_hnsw ...;` (§8 note).

### Phase 3: Lossless Markdown Compiler & Git Publisher
- [ ] Build Markdown synthesis engine adhering to corporate page templates.
- [ ] Implement sentence-level semantic coverage metric calculator (page_min read from `policies/publication_gates.yaml`).
- [ ] Implement 100% footnote citation generator linking back to source unit IDs.
- [ ] Implement conflict detection engine with authority ladder weighting.
- [ ] Build Git publishing worker: commit approved Markdown to `/var/data/wiki` repository on VPS.
- [ ] Git publish batching & locking (Edit 14): **one `git commit` per flow-run batch** (not per page); serialize wiki writes with a Postgres advisory lock (`SELECT pg_advisory_xact_lock(hashtext('wiki_publish'))`); store binary diagram assets (`assets/diagrams/*.png`) via **Git LFS** or an external object store, with `.gitattributes` + `.gitignore` for caches.
- [ ] Slice compiled Markdown into heading-aware chunks and index into `wiki_chunks` with pgvector.

### Phase 4: Quality Gates & Evaluation Benchmark
- [ ] Create 100-question gold evaluation benchmark (factual, multi-hop, contradictory, negative).
- [ ] **Calibrate tunable constants** (Edit 9): sweep `consensus_weights`, `consensus_thresholds`, `coverage`, and `dedup` in `policies/publication_gates.yaml` against the gold set; record chosen values and observed agreement.
- [ ] Implement automated quality gate evaluation runner (blocking publish if thresholds fail).
- [ ] Configure review queue for low-confidence clusters and open high-severity conflicts.

### Phase 5: Research MVP — Brief → Publish
- [ ] Implement `POST /jobs/research/start` — validate brief schema, create `research_briefs` row, enqueue `gpu-research` Prefect flow.
- [ ] Implement search/fetch loop (Loops 0–3): seed + meta-search, fetch + snapshot to OneDrive `research_snapshots/`, extract + clean.
- [ ] Implement Loop 4–5: embed & cluster, claim mining with entity links.
- [ ] Implement Loop 6–8: gap question generation + deepen fetch + wiki synthesis (`research/` front matter, claims, sources, conflicts).
- [ ] Implement gate evaluation against §6.5 depth gates; publish only when all pass.
- [ ] Implement `POST /jobs/research/deepen` — accept `research_id` + gap reason, re-enqueue loops for partial coverage.

### Phase 6: Research Deepen Loops + Cross-Bind
- [ ] Extend Prefect `gpu-research` pool to support iterative deepen loops until gates pass or gaps dispositioned.
- [ ] Implement conflict bind to internal wiki (§6.9): research findings link to/from `_entities/` and `code_symbols` without overriding.
- [ ] Implement agentic API surface: `POST /jobs/research/start`, `/jobs/research/deepen`, `GET /research/{id}/coverage`, `GET /research/{id}/claims`, `GET /research/{id}/sources`.
- [ ] Publish `research/` wiki bundle alongside docs/code wikis; share one Git root and entity space (acceptance criteria §9 items 9–12).

### Phase 7: Cross-Bind Docs ↔ Code ↔ Research; OpenWiki Handshake

---

## 17. Security & Threat Model

### 17.1 Core Security Posture
- **All source text is untrusted**: LLM prompts MUST use prompt-injection isolation (system/user message separation, output parsing, no eval of extracted content).
- **Fail closed on classification**: If `security_classification` or ACL metadata is unknown for a source and organizational policy requires it, the source is quarantined — never silently ingested.
- **GPU sees unit text**: Do not send content classified as `restricted` or `confidential` to GPU notebooks without explicit policy approval and audit logging.
- **Quarantine ≠ delete**: Quarantined files remain in OneDrive evidence with `status = 'quarantine'` in Postgres. They are excluded from processing but never purged.

### 17.2 Credential & Secret Boundaries

| Secret | Lives On | Never On |
|---|---|---|
| Microsoft Graph OAuth2 credentials (`AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`) | GPU notebook secrets only | VPS — VPS never contacts Graph |
| PostgreSQL `gpu_worker` password | GPU notebook `.env` + VPS `pg_hba.conf` | — |
| Prefect API key | GPU notebook + VPS Prefect server config | — |
| Control API bearer token (`API_TOKEN`) | GPU notebook + VPS FastAPI config | — |
| Git SSH key (wiki push) | VPS only (FastAPI service) | GPU — GPU pushes via Control API |

**Notebook secret store (Edit 11):** never put a real secret in a `.env` file inside a notebook repo (notebooks are JSON and are easily committed or synced to Drive). Load secrets from the platform's secret store:

| Platform | Secret store | Access |
|---|---|---|
| Google Colab | Colab Secrets (`google.colab.userdata`) | `from google.colab import userdata; userdata.get('AZURE_CLIENT_SECRET')` |
| Deepnote | Deepnote Environment Variables | `os.environ['AZURE_CLIENT_SECRET']` |

Rotate all keys on a schedule (see Accepted Risks) and on any suspected leak. `AZURE_CLIENT_SECRET` lives only in the notebook secret store — never on the VPS, never in Git.

### 17.3 Transport & Storage Encryption
- **PostgreSQL**: `sslmode=require` enforced in `pg_hba.conf` (`hostssl` only, SCRAM-SHA-256). Self-signed or Let's Encrypt cert (see §5.0 — certs must be generated before first start).
- **Control API**: Caddy automatic TLS (Let's Encrypt or ZeroSSL) on port 443. HTTP→HTTPS redirect.
- **Prefect API**: Port 4200 — see Accepted Risks (§17.5).
- **At rest**: PostgreSQL data directory on encrypted volume (LUKS or provider-level encryption). Git wiki on same encrypted volume.

### 17.4 Operational Hardening Checklist
- [ ] UFW: deny all incoming; allow 22 (rate-limited), 80, 443, 4200, 5432 only.
- [ ] Fail2ban: SSH (3 attempts → 24h ban), Caddy 4xx floods, PostgreSQL auth failures.
- [ ] Unattended upgrades enabled for security patches.
- [ ] Docker images pinned to digest hashes in `docker-compose.yml` (no floating `:latest`).
- [ ] Log rotation configured for all containers (`max-size: 10m`, `max-file: 3`).
- [ ] Postgres `log_connections = on`, `log_disconnections = on` for audit trail.

### 17.5 Accepted Risks (Edit 12)

| Risk | Why it's accepted | Mitigation |
|---|---|---|
| Prefect `:4200` exposed publicly with only an API key | Colab egress IPs are **not stable**, so a CIDR allowlist is impossible | API-key auth + fail2ban on 4200 + optional Caddy BasicAuth in front + 90-day key rotation |
| Self-signed Postgres TLS cert | No public CA for a bare IP; client verifies `sslmode=require` (encryption, not identity) | Optionally pin the cert fingerprint in the notebook; rotate with VPS rebuild |
| GPU worker sees `internal`/`confidential` unit text | Required for embedding/GraphRAG | Never send `restricted` content to GPU without explicit policy approval + audit log (§17.1) |

### 17.6 Backup & Disaster Recovery (Edit 13)

The Git wiki (Layer 4) is the source of truth and `pg_data` holds all derived state — both need backups.

```text
- Nightly: pg_dump knowledge_base  -> restic repo (S3/B2), 30-day retention.
- On publish: mirror the Git wiki to a second remote (or restic) after each batch commit.
- Quarterly: restore test on a fresh VPS — restore dump + wiki, then GET /api/v1/health == 200.
```

> The rclone ban applies **only** to OneDrive evidence sync (§1). Using restic/rclone to back the VPS up to object storage is a separate, permitted use.

---

## 18. Docker Compose & Caddyfile Skeletons

### 18.1 `docker-compose.yml` (VPS Control Plane)

```yaml
version: "3.9"

services:
  postgres:
    image: pgvector/pgvector:pg16
    restart: unless-stopped
    mem_limit: 1800m
    environment:
      POSTGRES_DB: knowledge_base
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: ${POSTGRES_ADMIN_PASSWORD}
    volumes:
      - pg_data:/var/lib/postgresql/data
      - ./migrations/init.sql:/docker-entrypoint-initdb.d/01-init.sql
      # TLS certs generated by migrations/init-certs.sh (§5.0) — must exist before start
      - ./migrations/certs/server.crt:/var/lib/postgresql/data/server.crt:ro
      - ./migrations/certs/server.key:/var/lib/postgresql/data/server.key:ro
    ports:
      - "5432:5432"
    command: >
      postgres
        -c shared_buffers=512MB
        -c work_mem=32MB
        -c maintenance_work_mem=128MB
        -c ssl=on
        -c ssl_cert_file=/var/lib/postgresql/data/server.crt
        -c ssl_key_file=/var/lib/postgresql/data/server.key
    logging:
      options:
        max-size: "10m"
        max-file: "3"

  redis:
    image: redis:7-alpine
    restart: unless-stopped
    mem_limit: 200m
    command: redis-server --maxmemory 150mb --maxmemory-policy allkeys-lru
    ports:
      - "6379:6379"

  prefect-server:
    image: prefecthq/prefect:3-python3.12
    restart: unless-stopped
    mem_limit: 800m
    environment:
      PREFECT_API_DATABASE_CONNECTION_URL: postgresql+asyncpg://postgres:${POSTGRES_ADMIN_PASSWORD}@postgres:5432/knowledge_base
      PREFECT_SERVER_API_HOST: "0.0.0.0"
    command: prefect server start
    ports:
      - "4200:4200"
    depends_on:
      - postgres

  control-api:
    build:
      context: ./apps/control_api
    restart: unless-stopped
    mem_limit: 400m
    environment:
      DATABASE_URL: postgresql+asyncpg://postgres:${POSTGRES_ADMIN_PASSWORD}@postgres:5432/knowledge_base
      REDIS_URL: redis://redis:6379/0
      API_TOKEN: ${API_TOKEN}
      WIKI_REPO_PATH: /var/data/wiki
    volumes:
      - wiki_data:/var/data/wiki
    ports:
      - "8000:8000"
    depends_on:
      - postgres
      - redis

  caddy:
    image: caddy:2-alpine
    restart: unless-stopped
    mem_limit: 100m
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile
      - caddy_data:/data
      - caddy_config:/config

volumes:
  pg_data:
  wiki_data:
  caddy_data:
  caddy_config:
```

### 18.2 `Caddyfile`

```text
{$VPS_DOMAIN:203.0.113.10} {
    # Control API — all /api/* routes
    handle /api/* {
        reverse_proxy control-api:8000
    }

    # Prefect UI (optional, can be removed in production)
    handle /prefect/* {
        reverse_proxy prefect-server:4200
    }

    # Default: 404
    handle {
        respond "Not Found" 404
    }

    log {
        output file /var/log/caddy/access.log
        format json
    }
}
```

---

## 19. Acceptance Criteria (Definition of Done)

1. **Zero VPS ML Execution**: VPS memory footprint stays under 3.5 GB RAM at all times; zero ML libraries installed on VPS.
2. **Network Isolation**: GPU worker connects to VPS using `VPS_IP` exclusively over TLS/SSL; no VPN daemon or rclone configured.
3. **Data Sovereign Extraction**: All OneDrive files acquired via Microsoft Graph in GPU notebook; zero paid external LLM APIs called.
4. **Idempotent Resilience**: Abruptly terminating and restarting the GPU notebook loses zero state and resumes from the exact unindexed batch.
5. **Lossless Wiki Coverage**: Every compiled Wiki page achieves $\ge 95\%$ sentence semantic recall against member units, with $100\%$ factual claims carrying footnote citations.
6. **Robust Search**: Lexical FTS functions with high precision when GPU is offline; Hybrid RRF search outperforms pure lexical search on gold benchmark queries (Recall@10 $\ge 0.88$).
7. **Complete Governance**: All conflicting assertions in raw evidence are surfaced as structured warning callouts in Wiki Markdown rather than silently discarded.
8. **Security Compliance**: No open database ports without SSL + strong passwords; no public Prefect without API key; all secrets rotatable without downtime.

### Research-Specific Criteria (added by §6 integration)

9. **Every cited web claim has a stored snapshot + hash**: $100\%$ of claims in `research_claims` have a corresponding entry in `web_sources` with `snapshot_path` and `content_hash` populated.
10. **Research loops run until gates pass or gaps dispositioned**: No published `research/` pages with `$max_loops$$ loops completed and gates failing; all gaps must be dispositioned as `paywalled` / `unreachable` / `out_of_scope` / `answered$`.
11. **No silent overwrite of internal approved wiki**: Conflicts with internal FRD/ADR pages are explicitly recorded in `conflicts/` — never silently overridden or merged.
12. **Research depth gates satisfied**: All 8 gates in §6.5 must pass before a research ID is marked `completed` in `research_briefs.status`.

---
## 20. Explicit Non-Goals

- Fine-tuning embedding or base LLM foundation models as a required path.
- Treating vector DB or GraphRAG indexes as the persistent source of truth (Git Markdown is authoritative).
- Running full multi-stage ingestion pipeline solely inside Colab ephemeral disk without VPS persistence.
- Integrating proprietary cloud services (Azure Durable Functions, Azure AI Search, Azure Document Intelligence).
- Silent conflict resolution, heuristic deletion of contradictory evidence, or unrecorded document suppression.
- Real-time streaming ingestion; this is a batch-oriented pipeline.

### Research-Specific Non-Goals

- Unlimited open-web scraping without brief/budget.
- Paywall bypass or ToS-violating crawl.
- Paid search/LLM APIs.
- One-shot web summarize without snapshots/claims/loops.
- Auto-promoting web text to `verified` over FRD/ADR.
- Research output silently overriding approved internal wiki pages.

---
## 21. Execution Summary for Engineering Agents

Build a **strictly decoupled two-tier system**:

1. **Small VPS** = Durable PostgreSQL 16 (pgvector HNSW + FTS GIN) + Prefect 3.x Server + FastAPI Control API + Git Markdown Wiki Repository (`/var/data/wiki`), accessible over public IP with TLS/SSL. Zero ML inference, zero OneDrive sync daemons, zero rclone.
2. **Colab / Deepnote GPU** = Ephemeral compute worker pulling jobs from Prefect, acquiring OneDrive files via Microsoft Graph delta API, extracting canonical units via Docling, generating BGE-M3 embeddings, running HDBSCAN + GraphRAG topic clustering, executing 3-way consensus scoring, synthesizing lossless cited Markdown pages, and indexing sliced chunks into pgvector. Also drives **deep internet research loops**: GraphRAG + claim extraction + deepen loops + snapshot storage + research wiki compilation.

Success = measurable completeness, cited Markdown, GraphRAG-organized wiki, and LLM-ready retrieval — all self-hosted except OneDrive and the VPS provider.

