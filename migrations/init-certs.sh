#!/usr/bin/env bash
# ============================================================================
# migrations/init-certs.sh
# Generate a self-signed TLS certificate/key for the postgres container.
# Run ONCE, before `docker compose up postgres`, with VPS_PUBLIC_HOST set.
# Postgres refuses world-readable key files, so chmod 600 the key.
# ============================================================================
set -euo pipefail

OUT_DIR="${1:-./migrations/certs}"
mkdir -p "$OUT_DIR"

: "${VPS_PUBLIC_HOST:?VPS_PUBLIC_HOST must be set (VPS public IP or DNS A record)}"

openssl req -new -x509 -days 825 -nodes \
  -out "${OUT_DIR}/server.crt" \
  -keyout "${OUT_DIR}/server.key" \
  -subj "/CN=${VPS_PUBLIC_HOST}"

chmod 600 "${OUT_DIR}/server.key"
chmod 644 "${OUT_DIR}/server.crt"

echo "Generated TLS cert + key in ${OUT_DIR}:"
ls -l "${OUT_DIR}"