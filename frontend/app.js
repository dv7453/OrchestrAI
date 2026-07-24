const API_BASE = window.location.origin;
const STORAGE_KEYS = {
  provider: "orchestrai.provider",
  groqKey: "orchestrai.groq_api_key",
  openaiKey: "orchestrai.openai_api_key",
  groqModel: "orchestrai.groq_model",
  openaiModel: "orchestrai.openai_model",
};

const providerCards = document.querySelectorAll(".provider-card");
const groqSettings = document.getElementById("groq-settings");
const openaiSettings = document.getElementById("openai-settings");
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
const groqKeyInput = document.getElementById("groq-key");
const openaiKeyInput = document.getElementById("openai-key");
const groqModelSelect = document.getElementById("groq-model");
const openaiModelSelect = document.getElementById("openai-model");

let currentRunId = null;
let isRunning = false;

function getSelectedProvider() {
  return document.querySelector('input[name="provider"]:checked').value;
}

function setProvider(provider) {
  providerCards.forEach((card) => {
    card.classList.toggle("selected", card.dataset.provider === provider);
  });

  const input = document.querySelector(`input[name="provider"][value="${provider}"]`);
  if (input) {
    input.checked = true;
  }

  const isGroq = provider === "groq";
  groqSettings.classList.toggle("hidden", !isGroq);
  openaiSettings.classList.toggle("hidden", isGroq);
  statusProvider.textContent = isGroq ? "Groq (BYOK)" : "OpenAI (BYOK)";
  localStorage.setItem(STORAGE_KEYS.provider, provider);
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
  localStorage.setItem(STORAGE_KEYS.groqKey, groqKeyInput.value.trim());
  localStorage.setItem(STORAGE_KEYS.openaiKey, openaiKeyInput.value.trim());
  localStorage.setItem(STORAGE_KEYS.groqModel, groqModelSelect.value);
  localStorage.setItem(STORAGE_KEYS.openaiModel, openaiModelSelect.value);
}

function loadPersistedSettings() {
  const savedProvider = localStorage.getItem(STORAGE_KEYS.provider) || "groq";
  const savedGroqKey = localStorage.getItem(STORAGE_KEYS.groqKey) || "";
  const savedOpenaiKey = localStorage.getItem(STORAGE_KEYS.openaiKey) || "";
  const savedGroqModel = localStorage.getItem(STORAGE_KEYS.groqModel);
  const savedOpenaiModel = localStorage.getItem(STORAGE_KEYS.openaiModel);

  groqKeyInput.value = savedGroqKey;
  openaiKeyInput.value = savedOpenaiKey;

  if (savedGroqModel) {
    groqModelSelect.value = savedGroqModel;
  }
  if (savedOpenaiModel) {
    openaiModelSelect.value = savedOpenaiModel;
  }

  setProvider(savedProvider);
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
  const groqKey = groqKeyInput.value.trim();
  const openaiKey = openaiKeyInput.value.trim();

  if (!task) {
    appendLog("Enter a task before running.");
    return;
  }

  if (provider === "groq" && !groqKey) {
    appendLog("Enter your Groq API key (BYOK).");
    return;
  }

  if (provider === "openai" && !openaiKey) {
    appendLog("Enter your OpenAI API key (BYOK).");
    return;
  }

  persistKeys();

  const payload = {
    task,
    provider,
    model: provider === "groq" ? groqModelSelect.value : openaiModelSelect.value,
    groq_api_key: provider === "groq" ? groqKey : null,
    openai_api_key: provider === "openai" ? openaiKey : null,
    headless: document.getElementById("headless").checked,
  };

  resetPreview();
  setRunning(true);
  appendLog(`Running with ${provider === "groq" ? "Groq" : "OpenAI"} (BYOK)...`);

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
      setBadge("BYOK Ready", "ready");
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

groqKeyInput.addEventListener("change", persistKeys);
openaiKeyInput.addEventListener("change", persistKeys);
groqModelSelect.addEventListener("change", persistKeys);
openaiModelSelect.addEventListener("change", persistKeys);

runBtn.addEventListener("click", runTask);
stopBtn.addEventListener("click", stopTask);

loadPersistedSettings();
checkBackend();
setInterval(checkBackend, 15000);
