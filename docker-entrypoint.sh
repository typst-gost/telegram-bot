#!/usr/bin/env sh
set -eu

DB_PATH="${DOCS_DB_PATH:-/app/data/docs_vector_db}"
ASSETS_DIR="${COMPILE_ASSETS_DIR:-/app/data/compile_assets}"
CACHE_DIR="${TYPST_CACHE_DIR:-/app/data/typst_cache}"

mkdir -p "$(dirname "$DB_PATH")" "$ASSETS_DIR" "$CACHE_DIR"

if [ ! -d "$DB_PATH/docs.lance" ] || [ "${SYNC_DOCS_ON_START:-false}" = "true" ]; then
  echo "[entrypoint] Syncing docs_vector_db and compile_assets..."
  sync-docs-db
fi

exec "$@"