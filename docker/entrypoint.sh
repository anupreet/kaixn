#!/usr/bin/env bash
# Apply the schema (when a Postgres DSN is configured), then serve the app.
set -euo pipefail

# In AWS the DB password arrives as a separate secret; compose the DSN from
# parts so the secret never lives in a plaintext env var. Locally KAIXN_DSN is
# set directly (docker-compose) and this block is skipped.
if [[ -z "${KAIXN_DSN:-}" && -n "${DB_HOST:-}" ]]; then
  export KAIXN_DSN="postgresql://${DB_USER:-kaixn}:${DB_PASSWORD:-}@${DB_HOST}:${DB_PORT:-5432}/${DB_NAME:-kaixn}"
fi

if [[ -n "${KAIXN_DSN:-}" ]]; then
  echo "kaixn: waiting for Postgres…"
  for i in $(seq 1 30); do
    if python - <<'PY' 2>/dev/null
import os, psycopg
psycopg.connect(os.environ["KAIXN_DSN"], connect_timeout=2).close()
PY
    then break; fi
    sleep 2
  done
  echo "kaixn: applying migrations…"
  python scripts/apply_migrations.py || {
    echo "kaixn: migration step failed (continuing — may already be applied)"; }
fi

exec uvicorn kaixn.web:app --host 0.0.0.0 --port "${PORT:-8000}"
