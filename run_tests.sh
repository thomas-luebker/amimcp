#!/bin/sh
# Run every test. No Amiga and no cross-compiler needed.
set -eu
cd "$(dirname "$0")"
python3 tests/test_protocol_sync.py "$@"
python3 tests/test_e2e.py "$@"
