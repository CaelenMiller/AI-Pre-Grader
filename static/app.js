const statusEl = document.getElementById("status");
const concurrencySlider = document.getElementById("concurrency");
const concurrencyValue = document.getElementById("concurrency-value");
const nitpickinessSlider = document.getElementById("nitpickiness");
const nitpickinessValue = document.getElementById("nitpickiness-value");
const gradingNotesInput = document.getElementById("grading-notes");
let notesDebounceHandle;

async function updateStatus(message) {
  if (!statusEl) return;
  statusEl.textContent = `Status: ${message}`;
}

async function postJSON(url, data) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.message || "Request failed");
  }
  return response.json();
}

async function postFormData(url, formData) {
  const response = await fetch(url, {
    method: "POST",
    body: formData,
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.message || "Upload failed");
  }
  return response.json();
}

function refreshFileList(category, files = [], options = {}) {
  const {
    selector = `.file-list[data-category="${category}"]`,
    emptyMessage = "",
  } = options;
  const list = document.querySelector(selector);
  if (!list) return;
  list.innerHTML = "";
  if (!files.length) {
    if (emptyMessage) {
      const li = document.createElement("li");
      li.textContent = emptyMessage;
      li.classList.add("empty");
      list.appendChild(li);
    }
    return;
  }
  files.forEach((file) => {
    const li = document.createElement("li");
    li.textContent = file;
    list.appendChild(li);
  });
}

function initializeDropArea(panel) {
  const dropArea = panel.querySelector(".drop-area");
  const fileInput = dropArea.querySelector("input[type='file']");
  const category = panel.dataset.category;

  const handleFiles = async (fileList) => {
    if (!fileList || !fileList.length) {
      return;
    }
    const formData = new FormData();
    Array.from(fileList).forEach((file) => formData.append("files", file));

    try {
      const result = await postFormData(`/upload/${category}`, formData);
      refreshFileList(category, result.files);
      await updateStatus("Idle");
    } catch (error) {
      alert(error.message);
    }
  };

  dropArea.addEventListener("dragover", (event) => {
    event.preventDefault();
    dropArea.classList.add("dragover");
  });

  dropArea.addEventListener("dragleave", () => {
    dropArea.classList.remove("dragover");
  });

  dropArea.addEventListener("drop", (event) => {
    event.preventDefault();
    dropArea.classList.remove("dragover");
    const files = event.dataTransfer?.files;
    handleFiles(files);
  });

  fileInput.addEventListener("change", (event) => {
    handleFiles(event.target.files);
    event.target.value = "";
  });
}

function initializePanel(panel) {
  initializeDropArea(panel);

  panel.querySelectorAll("button").forEach((button) => {
    const action = button.dataset.action;
    const category = panel.dataset.category;

    if (action === "clear") {
      button.addEventListener("click", async () => {
        try {
          await postJSON(`/clear/${category}`, {});
          refreshFileList(category, []);
          await updateStatus("Idle");
        } catch (error) {
          alert(error.message);
        }
      });
    }

    if (action === "generate") {
      button.addEventListener("click", async () => {
        try {
          const result = await postJSON("/action/generate-solution", {});
          await updateStatus(result.status);
        } catch (error) {
          alert(error.message);
        }
      });
    }

    if (action === "grade") {
      button.addEventListener("click", async () => {
        try {
          const result = await postJSON("/action/grade-submission", {});
          await updateStatus(result.status);
        } catch (error) {
          alert(error.message);
        }
      });
    }
  });
}

function initializeConcurrency() {
  concurrencySlider?.addEventListener("input", () => {
    concurrencyValue.textContent = concurrencySlider.value;
  });

  concurrencySlider?.addEventListener("change", async () => {
    const value = Number(concurrencySlider.value);
    try {
      await postJSON("/settings/concurrency", { maxConcurrent: value });
    } catch (error) {
      alert(error.message);
    }
  });
}

function initializeNitpickiness() {
  nitpickinessSlider?.addEventListener("input", () => {
    nitpickinessValue.textContent = nitpickinessSlider.value;
  });

  nitpickinessSlider?.addEventListener("change", async () => {
    const value = Number(nitpickinessSlider.value);
    try {
      await postJSON("/settings/nitpickiness", { level: value });
    } catch (error) {
      alert(error.message);
    }
  });
}

function initializeGradingNotes() {
  if (!gradingNotesInput) return;

  gradingNotesInput.addEventListener("input", () => {
    clearTimeout(notesDebounceHandle);
    const notes = gradingNotesInput.value;
    notesDebounceHandle = window.setTimeout(async () => {
      try {
        await postJSON("/settings/notes", { notes });
      } catch (error) {
        alert(error.message);
      }
    }, 400);
  });
}

async function hydrate() {
  try {
    const state = await fetch("/state").then((res) => res.json());
    updateStatus(state.status || "Idle");
    Object.entries(state.files || {}).forEach(([category, items]) => {
      refreshFileList(category, items);
    });
    const appdataEntries = state.appdataFiles || {};
    Object.entries(appdataEntries).forEach(([category, info = {}]) => {
      const files = Array.isArray(info.files) ? info.files : [];
      const exists = typeof info.exists === "boolean" ? info.exists : false;
      const emptyMessage = exists
        ? "No stored files found."
        : "Folder not found.";
      refreshFileList(category, files, {
        selector: `.file-list[data-appdata-category="${category}"]`,
        emptyMessage,
      });
    });
    if (typeof state.maxConcurrent === "number") {
      concurrencySlider.value = state.maxConcurrent;
      concurrencyValue.textContent = state.maxConcurrent;
    }
    if (typeof state.nitpickiness === "number" && nitpickinessSlider) {
      nitpickinessSlider.value = state.nitpickiness;
      nitpickinessValue.textContent = state.nitpickiness;
    }
    if (typeof state.gradingNotes === "string" && gradingNotesInput) {
      gradingNotesInput.value = state.gradingNotes;
    }
  } catch (error) {
    console.error("Failed to load initial state", error);
  }
}

document.querySelectorAll(".panel").forEach((panel) => initializePanel(panel));
initializeConcurrency();
initializeNitpickiness();
initializeGradingNotes();
hydrate();
