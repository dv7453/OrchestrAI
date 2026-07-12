#!/usr/bin/env bash
cd "$(dirname "$0")/.."
source .venv/bin/activate 2>/dev/null || true
echo "Starting Browser Agent at http://127.0.0.1:8765"
python -m langgraph_browser_agent.server
