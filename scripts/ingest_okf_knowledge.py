#!/usr/bin/env python3
"""Ingest ~/.okf/knowledge into RAG pipeline via batch API."""
import os, sys, pathlib, hashlib, json, time
import httpx

BASE = os.getenv("CONTROL_API_URL", "http://localhost:8000/api/v1").rstrip("/")
TOKEN = os.getenv("API_TOKEN") or open("/root/my/rag-pipeline/.env").read().split("API_TOKEN=")[1].split()[0].strip()
ROOT = pathlib.Path("/root/.okf/knowledge")

def sha256_file(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

def mime_for(p: pathlib.Path) -> str:
    import mimetypes
    m,_ = mimetypes.guess_type(str(p))
    return m or "application/octet-stream"

def discover(root: pathlib.Path):
    # Use the pipeline's discovery (same as worker)
    sys.path.insert(0, "/root/my/rag-pipeline")
    from workers.gpu_worker.discovery import discover as do_discover
    return do_discover(str(root))

def main():
    print(f"Discovering {ROOT} ...")
    manifest = discover(ROOT)
    print(f"Found {len(manifest)} files")
    # Batch register 100 at a time
    headers = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
    client = httpx.Client(timeout=60)
    registered = 0
    # First, batch register
    for i in range(0, len(manifest), 100):
        batch = manifest[i:i+100]
        items = []
        for it in batch:
            items.append({
                "drive_item_id": f"local:{it['sha256_hash']}",
                "drive_id": "local",
                "file_path": it["file_path"],
                "file_name": it["file_name"],
                "mime_type": it["mime_type"],
                "size_bytes": it["size_bytes"],
                "sha256_hash": it["sha256_hash"],
                "status": "discovered",
            })
        resp = client.post(f"{BASE}/sources/register-batch", headers=headers, json={"items": items})
        if resp.status_code != 200:
            print(f"Batch {i} failed {resp.status_code} {resp.text[:500]}")
            # Try individual
            for it in batch:
                r2 = client.post(f"{BASE}/sources/register", headers=headers, json={
                    "drive_item_id": f"local:{it['sha256_hash']}",
                    "drive_id": "local",
                    "file_path": it["file_path"],
                    "file_name": it["file_name"],
                    "mime_type": it["mime_type"],
                    "size_bytes": it["size_bytes"],
                    "sha256_hash": it["sha256_hash"],
                    "status": "discovered",
                })
                if r2.status_code == 200:
                    registered += 1
        else:
            data = resp.json()
            for r in data.get("results", []):
                if r.get("status") == "ok":
                    registered += 1
        print(f"Batch {i//100+1}/{(len(manifest)+99)//100} -> registered {registered}/{len(manifest)}")
        # Now upload blobs for this batch (in parallel, but sequentially for simplicity)
        for it in batch:
            # Need source_id - fetch by hash
            sha = it["sha256_hash"]
            # Get source by hash via list? Use by-hash endpoint
            try:
                r = client.get(f"{BASE}/sources/by-hash/{sha}", headers=headers)
                if r.status_code != 200:
                    continue
                sid = r.json().get("source_id") or r.json().get("id") or sha
                # Read file and upload blob
                p = ROOT / it["file_path"]
                if not p.exists():
                    continue
                raw = p.read_bytes()
                # Only upload if not too large (skip >10MB for now to avoid timeout)
                if len(raw) > 10*1024*1024:
                    print(f"Skip large {it['file_path']} {len(raw)}")
                    continue
                # Upload blob
                # Use httpx with raw bytes
                headers_blob = {"Authorization": f"Bearer {TOKEN}", "Content-Type": it["mime_type"] or "application/octet-stream"}
                br = client.post(f"{BASE}/sources/{sid}/blob", headers=headers_blob, content=raw)
                if br.status_code not in (200, 201):
                    print(f"Blob failed for {it['file_path']}: {br.status_code}")
            except Exception as e:
                print(f"Blob error {it['file_path']}: {e}")
        # Small sleep to avoid overwhelming API
        time.sleep(0.5)
    print(f"Done. Registered {registered}, total manifest {len(manifest)}")
    # Check queue
    r = client.get(f"{BASE}/health", headers=headers)
    print("Health:", r.text[:500])
    r = client.get(f"{BASE}/metrics", headers=headers)
    print("Metrics:", r.text[:1000])

if __name__ == "__main__":
    main()
