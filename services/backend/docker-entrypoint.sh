#!/bin/sh
set -eu
python -m scripts.initialize_keys
export JWT_SECRET_KEY="$(cat /run/keys/session.secret)"
python -m scripts.initialize_database
if [ "$#" -gt 0 ]; then
    exec "$@"
fi
exec uvicorn api.main:app --host 0.0.0.0 --port "${PORT:-8080}" --no-access-log
