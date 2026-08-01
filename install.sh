#!/bin/sh
# Register amimcp with Claude Code.
#
#   ./install.sh <amiga-host> [token] [port]
#
# Verifies the Amiga is actually reachable first — registering a server that
# cannot connect just moves the failure somewhere less obvious.

set -eu

HOST="${1:-}"
TOKEN="${2:-}"
PORT="${3:-7846}"

if [ -z "$HOST" ]; then
    echo "usage: $0 <amiga-host> [token] [port]" >&2
    echo "example: $0 192.168.1.42 pickasecret" >&2
    exit 2
fi

DIR="$(cd "$(dirname "$0")" && pwd)"
SERVER="$DIR/server/amimcp.py"

echo "Probing $HOST:$PORT ..."
if ! AMIGA_HOST="$HOST" AMIGA_PORT="$PORT" AMIGA_TOKEN="$TOKEN" \
        python3 "$SERVER" --probe; then
    echo
    echo "Could not reach amiagent. Check that:" >&2
    echo "  * the Amiga is on and its TCP/IP stack is up" >&2
    echo "  * amiagent is running (try: amiagent TOKEN=$TOKEN)" >&2
    echo "  * $HOST is the Amiga's actual address" >&2
    echo "  * the token matches the one amiagent was started with" >&2
    exit 1
fi

if ! command -v claude >/dev/null 2>&1; then
    echo
    echo "The 'claude' CLI is not on PATH, so I cannot register the server." >&2
    echo "Add it by hand with:" >&2
    echo >&2
    echo "  claude mcp add amiga \\" >&2
    echo "    --env AMIGA_HOST=$HOST --env AMIGA_PORT=$PORT --env AMIGA_TOKEN=$TOKEN \\" >&2
    echo "    -- python3 $SERVER" >&2
    exit 1
fi

echo
echo "Registering with Claude Code ..."
claude mcp remove amiga >/dev/null 2>&1 || true
claude mcp add amiga \
    --env "AMIGA_HOST=$HOST" \
    --env "AMIGA_PORT=$PORT" \
    --env "AMIGA_TOKEN=$TOKEN" \
    -- python3 "$SERVER"

echo
echo "Done. Start a new Claude Code session and ask it to check the Amiga."
if [ -z "$TOKEN" ]; then
    echo
    echo "NOTE: no token set. Anyone on your network can run commands on the" >&2
    echo "Amiga. Restart amiagent with TOKEN=<secret> and re-run this script." >&2
fi
