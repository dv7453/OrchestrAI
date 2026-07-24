const API_BASE = window.location.origin;

const PROVIDERS = {
  groq: {
    label: "Groq",
    keyField: "groq_api_key",
    keyInputId: "groq-key",
    modelSelectId: "groq-model",
    settingsId: "groq-settings",
    storageKey: "orchestrai.groq_api_key",
    storageModel: "orchestrai.groq_model",
  },
  openai: {
    label: "OpenAI",
    keyField: "openai_api_key",
    keyInputId: "openai-key",
    modelSelectId: "openai-model",
    settingsId: "openai-settings",
    storageKey: "orchestrai.openai_api_key",
    storageModel: "orchestrai.openai_model",
  },
  claude: {
    label: "Claude",
    keyField: "anthropic_api_key",
    keyInputId: "claude-key",
    modelSelectId: "claude-model",
    settingsId: "claude-settings",
    storageKey: "orchestrai.anthropic_api_key",
    storageModel: "orchestrai.claude_model",
  },
  mistral: {
    label: "Mistral",
    keyField: "mistral_api_key",
    keyInputId: "mistral-key",
    modelSelectId: "mistral-model",
    settingsId: "mistral-settings",
    storageKey: "orchestrai.mistral_api_key",
    storageModel: "orchestrai.mistral_model",
  },
};

const STORAGE_PROVIDER = "orchestrai.provider";

const providerCards = document.querySelectorAll(".provider-card");
const statusProvider = document.getElementById("status-provider");
const statusBackend = document.getElementById("status-backend");
const statusBrowser = document.getElementById("status-browser");
const connectionBadge = document.getElementById("connection-badge");
const logEl = document.getElementById("log");
const runBtn = document.getElementById("run-btn");
const stopBtn = document.getElementById("stop-btn");
const taskInput = document.getElementById("task-input");
const previewImage = document.getElementById("preview-image");
const previewFrame = document.getElementById("preview-frame");
const previewStep = document.getElementById("preview-step");
const previewUrl = document.getElementById("preview-url");

let currentRunId = null;
let isRunning = false;

function getSelectedProvider() {
  return document.querySelector('input[name="provider"]:checked').value;
}

function getEl(id) {
  return document.getElementById(id);
}

function setProvider(provider) {
  if (!PROVIDERS[provider]) {
    provider = "groq";
  }

  providerCards.forEach((card) => {
    card.classList.toggle("selected", card.dataset.provider === provider);
  });

  const input = document.querySelector(`input[name="provider"][value="${provider}"]`);
  if (input) {
    input.checked = true;
  }

  Object.entries(PROVIDERS).forEach(([name, config]) => {
    getEl(config.settingsId).classList.toggle("hidden", name !== provider);
  });

  statusProvider.textContent = PROVIDERS[provider].label;
  localStorage.setItem(STORAGE_PROVIDER, provider);
}

function setStatusPill(el, text, variant) {
  el.innerHTML = `<span class="status-pill ${variant}">${text}</span>`;
}

function setBadge(text, variant) {
  connectionBadge.className = `badge badge-${variant}`;
  connectionBadge.innerHTML = `<span class="badge-dot"></span>${text}`;
}

function appendLog(message) {
  const empty = logEl.querySelector(".log-empty");
  if (empty) {
    empty.remove();
  }

  const entry = document.createElement("div");
  entry.className = "log-entry";

  const time = document.createElement("div");
  time.className = "log-time";
  time.textContent = new Date().toLocaleTimeString();

  const text = document.createElement("div");
  text.textContent = message;

  entry.append(time, text);
  logEl.prepend(entry);
}

function setRunning(running) {
  isRunning = running;
  runBtn.disabled = running;
  stopBtn.disabled = !running;
  document.body.classList.toggle("is-running", running);

  if (running) {
    setStatusPill(statusBrowser, "Running", "running");
    setBadge("Running", "running");
  } else {
    setStatusPill(statusBrowser, "Idle", "idle");
  }
}

function updatePreview({ step, url, title, screenshot }) {
  if (typeof step === "number") {
    previewStep.textContent = `Step ${step}`;
  }

  if (url) {
    previewUrl.textContent = title ? `${title} — ${url}` : url;
  }

  if (screenshot) {
    const empty = previewFrame.querySelector(".preview-empty");
    if (empty) {
      empty.remove();
    }

    previewImage.classList.remove("visible");
    requestAnimationFrame(() => {
      previewImage.src = `data:image/png;base64,${screenshot}`;
      previewImage.classList.remove("hidden");
      requestAnimationFrame(() => {
        previewImage.classList.add("visible");
      });
    });
  }
}

function resetPreview() {
  previewStep.textContent = "Starting";
  previewUrl.textContent = "Waiting for browser...";
  previewImage.classList.remove("visible");
  previewImage.classList.add("hidden");
  previewImage.removeAttribute("src");

  if (!previewFrame.querySelector(".preview-empty")) {
    const empty = document.createElement("div");
    empty.className = "preview-empty";
    empty.innerHTML =
      '<span class="preview-empty-icon">⬚</span>Screenshots appear here as the agent works';
    previewFrame.appendChild(empty);
  }
}

function formatApiError(errorBody) {
  if (!errorBody) {
    return "Request failed.";
  }
  if (typeof errorBody.detail === "string") {
    return errorBody.detail;
  }
  if (Array.isArray(errorBody.detail)) {
    return errorBody.detail.map((item) => item.msg).join(", ");
  }
  return "Request failed.";
}

async function readError(response) {
  try {
    return formatApiError(await response.json());
  } catch {
    return `Request failed (${response.status}).`;
  }
}

function parseSseChunk(buffer) {
  const events = [];
  const parts = buffer.split("\n\n");

  for (let i = 0; i < parts.length - 1; i += 1) {
    const block = parts[i];
    const lines = block.split("\n");
    let event = "message";
    let data = "";

    for (const line of lines) {
      if (line.startsWith("event:")) {
        event = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        data = line.slice(5).trim();
      }
    }

    if (data) {
      events.push({ event, data: JSON.parse(data) });
    }
  }

  return {
    events,
    remainder: parts[parts.length - 1],
  };
}

function persistKeys() {
  Object.values(PROVIDERS).forEach((config) => {
    localStorage.setItem(config.storageKey, getEl(config.keyInputId).value.trim());
    localStorage.setItem(config.storageModel, getEl(config.modelSelectId).value);
  });
}

function loadPersistedSettings() {
  Object.values(PROVIDERS).forEach((config) => {
    const key = localStorage.getItem(config.storageKey) || "";
    const model = localStorage.getItem(config.storageModel);
    getEl(config.keyInputId).value = key;
    if (model) {
      getEl(config.modelSelectId).value = model;
    }
  });

  setProvider(localStorage.getItem(STORAGE_PROVIDER) || "groq");
}

async function handleEvent(event, data) {
  if (event === "started") {
    currentRunId = data.run_id;
    appendLog("Task started.");
    return;
  }

  if (event === "log") {
    appendLog(data.message);
    return;
  }

  if (event === "step") {
    updatePreview(data);
    if (data.goal) {
      appendLog(`Step ${data.step}: ${data.goal}`);
    }
    return;
  }

  if (event === "error") {
    appendLog(`Error: ${data.message}`);
    return;
  }

  if (event === "done") {
    appendLog(data.result);
    setRunning(false);
    currentRunId = null;
    previewStep.textContent = data.success ? "Completed" : "Finished";
    checkBackend();
  }
}

async function runTask() {
  const task = taskInput.value.trim();
  const provider = getSelectedProvider();
  const config = PROVIDERS[provider];
  const apiKey = getEl(config.keyInputId).value.trim();

  if (!task) {
    appendLog("Enter a task before running.");
    return;
  }

  if (!apiKey) {
    appendLog(`Enter your ${config.label} API key.`);
    return;
  }

  persistKeys();

  const payload = {
    task,
    provider,
    model: getEl(config.modelSelectId).value,
    groq_api_key: null,
    openai_api_key: null,
    anthropic_api_key: null,
    mistral_api_key: null,
    headless: document.getElementById("headless").checked,
  };
  payload[config.keyField] = apiKey;

  resetPreview();
  setRunning(true);
  appendLog(`Running with ${config.label}...`);

  try {
    const response = await fetch(`${API_BASE}/api/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      throw new Error(await readError(response));
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let finished = false;

    while (true) {
      const { value, done } = await reader.read();
      if (done) {
        break;
      }

      buffer += decoder.decode(value, { stream: true });
      const parsed = parseSseChunk(buffer);
      buffer = parsed.remainder;

      for (const item of parsed.events) {
        if (item.event === "done") {
          finished = true;
        }
        await handleEvent(item.event, item.data);
      }
    }

    if (!finished && isRunning) {
      setRunning(false);
      appendLog("Connection closed before task finished.");
      checkBackend();
    }
  } catch (error) {
    appendLog(`Error: ${error.message}`);
    setRunning(false);
    currentRunId = null;
    previewStep.textContent = "Error";
    checkBackend();
  }
}

async function stopTask() {
  if (!currentRunId) {
    appendLog("No active task to stop.");
    return;
  }

  try {
    await fetch(`${API_BASE}/api/run/${currentRunId}/stop`, { method: "POST" });
    appendLog("Stop requested.");
  } catch (error) {
    appendLog(`Stop failed: ${error.message}`);
  }
}

async function checkBackend() {
  try {
    const response = await fetch(`${API_BASE}/api/health`);
    if (!response.ok) {
      throw new Error("Backend unavailable");
    }

    await response.json();
    setStatusPill(statusBackend, "Connected", "connected");

    if (!isRunning) {
      setBadge("Active", "ready");
    }
  } catch {
    setStatusPill(statusBackend, "Offline", "offline");
    if (!isRunning) {
      setBadge("Offline", "warn");
    }
  }
}

providerCards.forEach((card) => {
  card.addEventListener("click", () => {
    const input = card.querySelector('input[type="radio"]');
    input.checked = true;
    setProvider(input.value);
  });
});

Object.values(PROVIDERS).forEach((config) => {
  getEl(config.keyInputId).addEventListener("change", persistKeys);
  getEl(config.modelSelectId).addEventListener("change", persistKeys);
});

runBtn.addEventListener("click", runTask);
stopBtn.addEventListener("click", stopTask);

loadPersistedSettings();
checkBackend();
setInterval(checkBackend, 15000);
