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

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"

app = FastAPI(title="OrchestrAI")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_running_tasks: dict[str, dict[str, Any]] = {}


class RunRequest(BaseModel):
    task: str = Field(min_length=1)
    provider: str = "groq"
    model: str | None = None
    openai_api_key: str | None = None
    headless: bool = False
    max_steps: int = 50
    step_timeout: int = 60


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _build_llm(request: RunRequest):
    from browser_use import ChatGroq, ChatOpenAI

    if request.provider == "openai":
        api_key = request.openai_api_key or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OpenAI API key is required for BYOK.")
        model = request.model or "gpt-4o"
        return ChatOpenAI(model=model, api_key=api_key)

    if request.provider != "groq":
        raise ValueError(f"Unsupported provider: {request.provider}")

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise ValueError("GROQ_API_KEY is not set in server environment variables.")
    model = request.model or os.getenv(
        "GROQ_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct"
    )
    return ChatGroq(model=model, api_key=api_key)


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
        langgraph_agent = LangGraphBrowserAgent(browser_agent)

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
        "groq_configured": bool(os.getenv("GROQ_API_KEY")),
        "openai_configured": bool(os.getenv("OPENAI_API_KEY")),
        "default_provider": "groq",
        "default_model": os.getenv(
            "GROQ_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct"
        ),
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
    if request.provider == "groq" and not os.getenv("GROQ_API_KEY"):
        raise HTTPException(
            status_code=400,
            detail="GROQ_API_KEY is not set in server environment variables.",
        )
    if request.provider == "openai" and not (
        request.openai_api_key or os.getenv("OPENAI_API_KEY")
    ):
        raise HTTPException(status_code=400, detail="OpenAI API key is required for BYOK.")

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
