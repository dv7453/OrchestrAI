FROM mcr.microsoft.com/playwright/python:v1.60.0-noble

WORKDIR /app

COPY pyproject.toml README.md langgraph.json ./
COPY src ./src
COPY frontend ./frontend
COPY examples ./examples

# Noble image ships Python 3.12 + Chromium; pin playwright to match image browsers.
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -e '.[browser,server]' \
    && pip install --no-cache-dir 'playwright==1.60.0'

ENV BROWSER_USE_HEADLESS=true
ENV PYTHONUNBUFFERED=1

EXPOSE 8765

CMD uvicorn langgraph_browser_agent.server:app --host 0.0.0.0 --port ${PORT:-8765}
