#!/usr/bin/env sh
set -eu

ASSETS_DIR="${COMPILE_ASSETS_DIR:-/app/data/compile_assets}"
CACHE_DIR="${TYPST_CACHE_DIR:-/app/data/typst_cache}"

mkdir -p "$ASSETS_DIR" "$CACHE_DIR"

if [ -z "$(ls -A "$ASSETS_DIR" 2>/dev/null)" ] || [ "${SYNC_ASSETS_ON_START:-false}" = "true" ]; then
  echo "[entrypoint] Syncing compile_assets..."
  sync-assets
fi

exec "$@"