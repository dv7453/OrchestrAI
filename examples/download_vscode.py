import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from browser_use import Agent, ChatGroq
from langgraph_browser_agent import LangGraphBrowserAgent

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

GROQ_MODEL = os.getenv("GROQ_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")

VSCODE_TASK = """
Go to https://code.visualstudio.com/download and download Visual Studio Code for macOS.

Steps:
1. Open the VS Code download page.
2. Find the macOS download option (Apple Silicon or Intel Mac if prompted).
3. Click the download button to start downloading the installer.
4. Confirm the download started successfully.
5. Report which file was downloaded and where it was saved if visible.
"""


async def main():
    print("Starting VS Code download automation with Groq...")

    agent = Agent(
        task=VSCODE_TASK.strip(),
        llm=ChatGroq(model=GROQ_MODEL),
    )
    langgraph_agent = LangGraphBrowserAgent(agent)

    try:
        final_state = await langgraph_agent.run(max_steps=50, step_timeout=60)
        print("Done.", final_state)
    except Exception as e:
        print(f"Automation failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
