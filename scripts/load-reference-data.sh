#!/usr/bin/env bash
# Replay the versioned reference data (real destinations and institutions, with sources)
# through the import pipeline. Safe to re-run: unchanged rows commit nothing.
#
#   ./scripts/load-reference-data.sh            load
#   DRY_RUN=1 ./scripts/load-reference-data.sh  stage and discard, to preview the diff
set -euo pipefail
cd "$(dirname "$0")/.."

: "${DATABASE_URL:=postgresql+psycopg://scholara:scholara@127.0.0.1:5432/scholara}"
: "${REDIS_URL:=redis://127.0.0.1:6379/0}"
export DATABASE_URL REDIS_URL

cd backend
python3 -m app.services.reference_data
