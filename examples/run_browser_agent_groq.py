import asyncio
from langgraph_browser_agent import LangGraphBrowserAgent
from browser_use import Agent, ChatGroq

async def test_complete_workflow():
    print('Testing LangGraph workflow with Groq...')

    agent = Agent(
        task="Go to amazon.com to check the price of high end CPUs for mining purposes",
        llm=ChatGroq(model='llama-3.3-70b-versatile'),
    )

    langgraph_agent = LangGraphBrowserAgent(agent)

    try:
        await langgraph_agent.run()
    except Exception as e:
        print(f'Workflow failed: {e}')
        import traceback
        traceback.print_exc()

asyncio.run(test_complete_workflow())
