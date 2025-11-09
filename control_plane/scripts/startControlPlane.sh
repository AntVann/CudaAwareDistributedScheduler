#!/usr/bin/env bash
set -euo pipefail
export PYTHONUNBUFFERED=1
exec uvicorn control_plane.app:app --host 0.0.0.0 --port 8000
