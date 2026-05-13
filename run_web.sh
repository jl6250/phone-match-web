#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONPATH="${PYTHONPATH:-}:$(pwd)"
if ! python3 -c "import streamlit" 2>/dev/null; then
  echo "请先执行: python3 -m pip install -r requirements.txt" >&2
  exit 1
fi
exec python3 -m streamlit run app/streamlit_app.py "$@"
