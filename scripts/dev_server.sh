#!/bin/sh
# Start the RELAY backend (serves the built UI from frontend/dist too).
#   scripts/dev_server.sh            live mode, needs GEMINI_API_KEY in .env
#   RELAY_MODE=fixture scripts/dev_server.sh   hand-written test doubles, no key needed
cd "$(dirname "$0")/../backend" || exit 1
exec .venv/bin/python -m uvicorn relay.app:app --port "${RELAY_PORT:-8017}" --log-level warning "$@"
