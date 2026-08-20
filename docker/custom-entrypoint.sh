#!/bin/bash
# docker/custom-entrypoint.sh
# Copies TLS certs from /opt/certs/ to /var/lib/postgresql/data/ before Postgres starts
# Only copies if the data dir is empty (fresh init), otherwise skips

set -e

# Export necessary environment variables
export POSTGRES_PASSWORD=${POSTGRES_ADMIN_PASSWORD:-"$(openssl rand -hex 32)"}
export POSTGRES_DB=knowledge_base

CERT_SRC_DIR="/opt/certs"
PG_DATA_DIR="/var/lib/postgresql/data"

# Only copy certs if data dir is empty (fresh init) and certs exist
if [ -f "${CERT_SRC_DIR}/server.crt" ] && [ -f "${CERT_SRC_DIR}/server.key" ] && [ -z "$(ls -A ${PG_DATA_DIR} 2>/dev/null)" ]; then
    echo "Fresh init detected — copying TLS certs from ${CERT_SRC_DIR} to ${PG_DATA_DIR}"
    cp "${CERT_SRC_DIR}/server.crt" "${PG_DATA_DIR}/server.crt"
    cp "${CERT_SRC_DIR}/server.key" "${PG_DATA_DIR}/server.key"
    chown 999:999 "${PG_DATA_DIR}/server.key" "${PG_DATA_DIR}/server.crt" 2>/dev/null || true
    chmod 600 "${PG_DATA_DIR}/server.key"
    chmod 644 "${PG_DATA_DIR}/server.crt"
    echo "TLS certs copied and permissions set"
elif [ -f "${CERT_SRC_DIR}/server.crt" ] && [ -f "${CERT_SRC_DIR}/server.key" ]; then
    echo "Data directory already initialized — skipping cert copy"
fi

# Hand off to the original docker-entrypoint.sh
exec /usr/local/bin/docker-entrypoint.sh "$@"