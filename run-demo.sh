#!/usr/bin/env sh
# One-command demo (Linux/macOS). Windows: run-demo.bat
cd "$(dirname "$0")" && exec python3 -m edm_ai demo "$@"
