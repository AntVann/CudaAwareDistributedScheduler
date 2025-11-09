#!/usr/bin/env bash
set -euo pipefail
export PYTHONUNBUFFERED=1
PORT="${AGENT_PORT:-8001}"
exec uvicorn agent.agent:app --host 0.0.0.0 --port "${PORT}"
