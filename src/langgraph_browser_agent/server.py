"""FastAPI control plane — BYOK, SSE streaming, event store & memory endpoints.

V3 additions:
- Global EventStore and EpisodicMemory instances (SQLite, stored in data/)
- SSE events: plan, reflection, memory_context
- Endpoints: GET /api/runs, GET /api/runs/{id}/events, GET /api/memory
"""

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent import LangGraphBrowserAgent
from .event_store import EventStore
from .memory import EpisodicMemory

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
DATA_DIR = Path(__file__).resolve().parents[2] / "data"
DATA_DIR.mkdir(exist_ok=True)

app = FastAPI(title="OrchestrAI")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_running_tasks: dict[str, dict[str, Any]] = {}

# V3: Global stores (SQLite backed, persist across restarts)
_event_store = EventStore(db_path=DATA_DIR / "orchestrai_events.db")
_memory = EpisodicMemory(db_path=DATA_DIR / "orchestrai_memory.db")


class RunRequest(BaseModel):
    task: str = Field(min_length=1)
    provider: str = "groq"
    model: str | None = None
    groq_api_key: str | None = None
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    mistral_api_key: str | None = None
    headless: bool = False
    max_steps: int = 50
    step_timeout: int = 60


SUPPORTED_PROVIDERS = ("groq", "openai", "claude", "mistral")


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _provider_api_key(request: RunRequest) -> str:
    mapping = {
        "groq": request.groq_api_key,
        "openai": request.openai_api_key,
        "claude": request.anthropic_api_key,
        "mistral": request.mistral_api_key,
    }
    return (mapping.get(request.provider) or "").strip()


def _build_llm(request: RunRequest):
    from browser_use import ChatAnthropic, ChatGroq, ChatMistral, ChatOpenAI

    api_key = _provider_api_key(request)
    if not api_key:
        raise ValueError(f"{request.provider} API key is required.")

    if request.provider == "openai":
        return ChatOpenAI(model=request.model or "gpt-4o", api_key=api_key)

    if request.provider == "claude":
        return ChatAnthropic(
            model=request.model or "claude-sonnet-4-5",
            api_key=api_key,
        )

    if request.provider == "mistral":
        return ChatMistral(
            model=request.model or "mistral-medium-latest",
            api_key=api_key,
        )

    if request.provider == "groq":
        return ChatGroq(model=request.model or "openai/gpt-oss-20b", api_key=api_key)

    raise ValueError(f"Unsupported provider: {request.provider}")


def _browser_headless(request: RunRequest) -> bool:
    env_value = os.getenv("BROWSER_USE_HEADLESS", "").lower()
    if env_value in ("true", "1", "yes"):
        return True
    return request.headless


async def _run_agent(request: RunRequest, run_id: str, queue: asyncio.Queue):
    from browser_use import Agent, BrowserProfile

    stop_event = asyncio.Event()
    _running_tasks[run_id] = {"stop": stop_event, "agent": None}

    try:
        await queue.put(
            _sse("log", {"message": f"Starting task with {request.provider.upper()}..."})
        )

        llm = _build_llm(request)
        profile = BrowserProfile(headless=_browser_headless(request))

        async def should_stop() -> bool:
            return stop_event.is_set()

        browser_agent = Agent(
            task=request.task,
            llm=llm,
            browser_profile=profile,
            register_should_stop_callback=should_stop,
            # Keep prompts under Groq free-tier TPM (8k for gpt-oss).
            use_vision=False,
            flash_mode=True,
            use_thinking=False,
            max_history_items=8,
            max_clickable_elements_length=12000,
            max_actions_per_step=3,
        )
        _running_tasks[run_id]["agent"] = browser_agent

        async def on_step(browser_state_summary, model_output, step_number: int):
            goal = getattr(model_output, "next_goal", None) if model_output else None

            await queue.put(
                _sse(
                    "step",
                    {
                        "step": step_number,
                        "url": browser_state_summary.url,
                        "title": browser_state_summary.title,
                        "goal": goal,
                        "screenshot": browser_state_summary.screenshot,
                    },
                )
            )

            if goal:
                await queue.put(_sse("log", {"message": f"Step {step_number}: {goal}"}))

        browser_agent.register_new_step_callback = on_step

        # V3: Create agent with event store and memory
        langgraph_agent = LangGraphBrowserAgent(
            browser_agent,
            event_store=_event_store,
            memory=_memory,
        )

        # V3: Emit memory context info
        if _memory:
            try:
                similar = _memory.retrieve_similar(request.task, top_k=3)
                if similar:
                    await queue.put(
                        _sse(
                            "memory_context",
                            {
                                "count": len(similar),
                                "episodes": [
                                    {
                                        "task": ep["task_text"],
                                        "success": ep.get("success", False),
                                        "similarity": ep.get("similarity_score", 0),
                                    }
                                    for ep in similar
                                ],
                            },
                        )
                    )
                    await queue.put(
                        _sse(
                            "log",
                            {"message": f"📚 Found {len(similar)} similar past task(s) for planning context"},
                        )
                    )
            except Exception:
                pass  # Memory retrieval is best-effort

        history = await langgraph_agent.run(
            max_steps=request.max_steps,
            step_timeout=request.step_timeout,
        )

        success = bool(history and history.is_successful())
        final_text = (history.final_result() if history else None) or "Task finished."

        await queue.put(
            _sse(
                "done",
                {
                    "success": success,
                    "result": final_text,
                    "run_id": run_id,
                    "steps": langgraph_agent.current_step,
                },
            )
        )
    except asyncio.CancelledError:
        await queue.put(_sse("log", {"message": "Task cancelled."}))
        await queue.put(_sse("done", {"success": False, "result": "Cancelled."}))
    except Exception as exc:
        await queue.put(_sse("error", {"message": str(exc)}))
        await queue.put(_sse("done", {"success": False, "result": str(exc)}))
    finally:
        _running_tasks.pop(run_id, None)
        await queue.put(None)


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "version": "3.0.0",
        "byok": True,
        "providers": list(SUPPORTED_PROVIDERS),
        "default_provider": "groq",
        "default_model": "openai/gpt-oss-20b",
        "features": ["planning", "reflection", "episodic_memory", "event_log"],
    }


@app.post("/api/run/{run_id}/stop")
async def stop_run(run_id: str):
    entry = _running_tasks.get(run_id)
    if not entry:
        raise HTTPException(status_code=404, detail="No active run found.")

    entry["stop"].set()
    agent = entry.get("agent")
    if agent is not None:
        agent.stop()

    return {"status": "stopping"}


@app.post("/api/run")
async def run_task(request: RunRequest):
    if request.provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported provider. Use one of: {', '.join(SUPPORTED_PROVIDERS)}",
        )
    if not _provider_api_key(request):
        label = {
            "groq": "Groq",
            "openai": "OpenAI",
            "claude": "Anthropic (Claude)",
            "mistral": "Mistral",
        }[request.provider]
        raise HTTPException(
            status_code=400,
            detail=f"{label} API key is required.",
        )

    run_id = os.urandom(8).hex()
    queue: asyncio.Queue = asyncio.Queue()
    task = asyncio.create_task(_run_agent(request, run_id, queue))

    async def event_stream():
        yield _sse("started", {"run_id": run_id})
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield item
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Run-Id": run_id,
        },
    )


# ---------------------------------------------------------------------------
# V3: Event log endpoints
# ---------------------------------------------------------------------------

@app.get("/api/runs")
async def list_runs(limit: int = 50):
    """List recent runs from the event store."""
    return {"runs": _event_store.list_runs(limit=limit)}


@app.get("/api/runs/{run_id}/events")
async def get_run_events(run_id: str):
    """Get the full event timeline for a specific run."""
    events = _event_store.get_run_events(run_id)
    if not events:
        raise HTTPException(status_code=404, detail="Run not found.")
    summary = _event_store.get_run_summary(run_id)
    return {"run_id": run_id, "summary": summary, "events": events}


# ---------------------------------------------------------------------------
# V3: Memory endpoints
# ---------------------------------------------------------------------------

@app.get("/api/memory")
async def list_memory(limit: int = 50):
    """List stored episodes from episodic memory."""
    return {"episodes": _memory.list_episodes(limit=limit)}


@app.get("/api/memory/search")
async def search_memory(query: str, top_k: int = 5):
    """Search episodic memory for similar past tasks."""
    results = _memory.retrieve_similar(query, top_k=top_k)
    return {"query": query, "results": results}


# ---------------------------------------------------------------------------
# Frontend serving
# ---------------------------------------------------------------------------

@app.get("/")
async def index():
    return FileResponse(FRONTEND_DIR / "index.html")


if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


def main():
    import uvicorn

    port = int(os.environ.get("PORT", "8765"))
    uvicorn.run(
        "langgraph_browser_agent.server:app",
        host="0.0.0.0",
        port=port,
        reload=False,
    )


if __name__ == "__main__":
    main()
