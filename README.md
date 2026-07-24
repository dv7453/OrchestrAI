# OrchestrAI

> ⚠️ **Update (July 2026): This is V2.** If you received this repo/README earlier, please re-check — the orchestration layer was rebuilt (LangGraph state machine replacing the old multi-agent setup) and previous architectural inaccuracies are fixed. Scroll to the bottom for a short summary of what changed.

AI browser automation powered by LangGraph, Groq, and a live-preview web UI.

Describe a task in plain English — OrchestrAI opens a browser, plans actions with an LLM, and executes them step by step while streaming screenshots to the UI.

## Live Demo

**https://orchestrai-1.onrender.com**

## Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e '.[browser,server]'
playwright install chromium

cp .env.template .env

cd frontend && ./start.sh
```

Open **http://127.0.0.1:8765** and paste your own **Groq** or **OpenAI** API key in the UI (full BYOK).

## Environment

OrchestrAI is **full BYOK** — LLM keys are not required on the server. Users provide keys in the UI (or API request body). Keys are not stored server-side.

| Variable | Required | Description |
|----------|----------|-------------|
| `BROWSER_USE_HEADLESS` | No | `true` on cloud / Docker |
| `PORT` | No | Server port (Render sets this) |

API run body fields: `groq_api_key` or `openai_api_key` depending on `provider`.

## Architecture

```
Web UI (frontend/) → FastAPI (server.py) → LangGraphBrowserAgent → browser-use → Chromium
```

### LangGraph workflow

15-node state machine: guard checks → step loop (prepare → LLM → execute → evaluate) → completion or retry.

| Metric | Value |
|--------|------:|
| Graph nodes | 15 |
| State fields | 4 |
| Routing branches | 11 |
| Unit tests | 36 (100% pass) |

## API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Web UI |
| `/api/health` | GET | Backend status |
| `/api/run` | POST | Start task (SSE stream) |
| `/api/run/{id}/stop` | POST | Cancel task |

## CLI Examples

```bash
python examples/run_browser_agent_groq.py
python examples/download_vscode.py
```

## Development

```bash
pytest tests/ -v
pip install -e '.[studio]'
langgraph dev
```

## Project Structure

```
src/langgraph_browser_agent/   # Core agent + LangGraph + API
frontend/                      # Web UI
examples/                      # CLI demos
tests/                         # Unit tests
```

---

## 📌 Note on V2

If you've seen an earlier version of this project or README, a few things changed:

- **Orchestration rebuilt**: moved from a multi-agent decomposition approach to a LangGraph state machine (15 nodes, 28 edges) wrapping `browser-use`. This is a more accurate and testable representation of how the agent actually executes tasks.
- **Fixed architectural inaccuracies** present in the earlier version's description (see PROJECT_CONTEXT.md for full architecture, if present in repo).
- **Resume/portfolio note**: if cross-checking against my resume, refer to the version listing this as "LangGraph Browser Automation Agent" — that's the one that matches this codebase. An older "Multi-Agent Web Automation" description is deprecated.

Full technical writeup: PROJECT_CONTEXT.md
