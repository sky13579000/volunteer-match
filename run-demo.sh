#!/usr/bin/env sh
# One-command demo (Linux/macOS). Windows: run-demo.bat.   Other PCs on the LAN:  ./run-demo.sh --lan
cd "$(dirname "$0")" || exit 1
for py in python3 python; do
  if command -v "$py" >/dev/null 2>&1 && "$py" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
    exec "$py" -m edm_ai demo "$@"
  fi
done
echo "Python 3.10+ 이 필요합니다 (macOS: brew install python / Ubuntu: sudo apt install python3)" >&2
exit 1
