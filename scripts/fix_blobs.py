#!/usr/bin/env python3
import os, pathlib, httpx, sys
BASE = os.getenv("CONTROL_API_URL", "http://localhost:8000/api/v1").rstrip("/")
TOKEN = os.getenv("API_TOKEN") or open("/root/my/rag-pipeline/.env").read().split("API_TOKEN=")[1].split()[0].strip()
ROOT = pathlib.Path("/root/.okf/knowledge")

# Get all discovered sources without blobs
import psycopg2
# Use direct DB to find missing blobs
import subprocess, json
# Use psql to get list
out = subprocess.check_output(["docker", "exec", "rag-pipeline-postgres-1", "psql", "-U", "postgres", "-d", "knowledge_base", "-t", "-A", "-c", "SELECT count(*) FROM sources s LEFT JOIN source_blobs b ON s.source_id=b.source_id WHERE s.status='discovered' AND b.source_id IS NULL"], text=True)
total_missing = int(out.strip().split()[0])
print(f"Total missing blobs: {total_missing}")
batch_size = 1000
offset = 0
headers_base = {"Authorization": f"Bearer {TOKEN}"}
client = httpx.Client(timeout=30)
uploaded = 0
while True:
    out2 = subprocess.check_output(["docker", "exec", "rag-pipeline-postgres-1", "psql", "-U", "postgres", "-d", "knowledge_base", "-t", "-A", "-c", f"SELECT s.source_id, s.file_path, s.mime_type FROM sources s LEFT JOIN source_blobs b ON s.source_id=b.source_id WHERE s.status='discovered' AND b.source_id IS NULL LIMIT {batch_size} OFFSET {offset}"], text=True)
    lines = [l for l in out2.strip().split("\n") if l.strip()]
    if not lines:
        break
    print(f"Batch offset {offset}, found {len(lines)} missing")
    for idx, line in enumerate(lines):
        parts = line.split("|")
        if len(parts) < 3:
            continue
        sid, fpath, mime = parts[0].strip(), parts[1].strip(), parts[2].strip()
        p = ROOT / fpath
        if not p.exists():
            print(f"Missing file {fpath}")
            continue
        try:
            raw = p.read_bytes()
        except Exception as e:
            print(f"Read fail {fpath}: {e}")
            continue
        if len(raw) > 5*1024*1024:
            print(f"Skip large {fpath} {len(raw)}")
            continue
        try:
            r = client.post(f"{BASE}/sources/{sid}/blob", headers={**headers_base, "Content-Type": mime or "application/octet-stream"}, content=raw)
            if r.status_code not in (200,201):
                print(f"Blob fail {fpath}: {r.status_code} {r.text[:200]}")
            else:
                uploaded += 1
                if uploaded % 100 == 0:
                    print(f"Uploaded {uploaded}/{total_missing} {fpath}")
        except Exception as e:
            print(f"Error {fpath}: {e}")
    offset += batch_size
    # Small pause to avoid overwhelming
    import time; time.sleep(0.5)
print(f"Done fixing blobs: uploaded {uploaded}/{total_missing}")

