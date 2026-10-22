#!/usr/bin/env bash
# Boots the single-container stack: Postgres, Redis, migrations, then the API (which runs
# the ingestion worker in-process). Data lives under DOCPILOT_DATA_DIR and is lost when
# the container restarts, which is fine for a demo.
set -euo pipefail

DATA_DIR="${DOCPILOT_DATA_DIR:-/tmp/docpilot}"
PGDATA="$DATA_DIR/pg"
PG_BIN="$(ls -d /usr/lib/postgresql/*/bin | sort -V | tail -1)"
mkdir -p "$DATA_DIR" "$LOCAL_STORAGE_PATH"

if [ ! -s "$PGDATA/PG_VERSION" ]; then
  "$PG_BIN/initdb" -D "$PGDATA" -U "$(id -un)" --auth=trust -E UTF8 > /dev/null
fi
"$PG_BIN/pg_ctl" -D "$PGDATA" -l "$DATA_DIR/postgres.log" -w \
  -o "-c listen_addresses=127.0.0.1 -k $DATA_DIR" start
"$PG_BIN/createdb" -h 127.0.0.1 docpilot 2> /dev/null || true

redis-server --bind 127.0.0.1 --port 6379 --save '' --appendonly no \
  --daemonize yes --logfile "$DATA_DIR/redis.log"

export DATABASE_URL="${DATABASE_URL:-postgresql+asyncpg://$(id -un)@127.0.0.1:5432/docpilot}"
# A Space secret named JWT_SECRET keeps sessions valid across restarts; otherwise a random
# one is generated on every boot.
JWT_SECRET="${JWT_SECRET:-$(python -c 'import secrets; print(secrets.token_urlsafe(48))')}"
export JWT_SECRET

alembic upgrade head
exec docpilot-api
