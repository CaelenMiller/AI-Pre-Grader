const statusEl = document.getElementById("status");
const concurrencySlider = document.getElementById("concurrency");
const concurrencyValue = document.getElementById("concurrency-value");
const nitpickinessSlider = document.getElementById("nitpickiness");
const nitpickinessValue = document.getElementById("nitpickiness-value");
const gradingNotesInput = document.getElementById("grading-notes");
const assignmentSelect = document.getElementById("assignment-selector");
const newAssignmentGroup = document.getElementById("new-assignment-group");
const assignmentTitleInput = document.getElementById("assignment-title");
const NEW_ASSIGNMENT_VALUE = "__new__";
let notesDebounceHandle;
let pendingNewAssignmentTitle = assignmentTitleInput?.value || "";
let currentAssignmentTitle = "";

function toggleNewAssignmentInput(show) {
  if (!newAssignmentGroup) return;
  newAssignmentGroup.hidden = !show;
}

if (assignmentSelect) {
  if (assignmentSelect.value === NEW_ASSIGNMENT_VALUE) {
    toggleNewAssignmentInput(true);
    currentAssignmentTitle = (assignmentTitleInput?.value || "").trim();
  } else if (assignmentSelect.value) {
    toggleNewAssignmentInput(false);
    currentAssignmentTitle = assignmentSelect.value;
  } else {
    toggleNewAssignmentInput(false);
    currentAssignmentTitle = "";
  }
} else {
  toggleNewAssignmentInput(Boolean(assignmentTitleInput?.value));
  currentAssignmentTitle = (assignmentTitleInput?.value || "").trim();
}

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
    selector = `.file-list[data-files-category="${category}"]`,
    emptyMessage = "No files available.",
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
    if (!currentAssignmentTitle) {
      alert("Select or create an assignment before uploading files.");
      return;
    }
    const formData = new FormData();
    if (assignmentTitleInput) {
      formData.append("assignmentTitle", currentAssignmentTitle);
    }
    Array.from(fileList).forEach((file) => formData.append("files", file));

    try {
      const result = await postFormData(`/upload/${category}`, formData);
      if (typeof result.assignmentTitle === "string") {
        currentAssignmentTitle = result.assignmentTitle;
      }
      await hydrate();
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

    if (action === "wipe") {
      button.addEventListener("click", async () => {
        const confirmWipe = window.confirm(
          `Are you sure you want to wipe the ${category} folder?`
        );
        if (!confirmWipe) {
          return;
        }

        try {
          await updateStatus("Wiping folder...");
          await postJSON(`/clear/${category}`, {});
          await hydrate();
          await updateStatus("Folder wiped");
        } catch (error) {
          alert(error.message);
          await updateStatus("Failed to wipe folder");
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

function updateAssignmentOptions(assignments = [], selectedTitle = "") {
  if (!assignmentSelect) return;

  const normalizedAssignments = Array.isArray(assignments)
    ? assignments
    : [];
  const previousValue = assignmentSelect.value;
  const previousPending = pendingNewAssignmentTitle;

  assignmentSelect.innerHTML = "";

  const placeholderOption = document.createElement("option");
  placeholderOption.value = "";
  placeholderOption.disabled = true;
  placeholderOption.textContent = "Select an assignment";
  assignmentSelect.appendChild(placeholderOption);

  normalizedAssignments.forEach((name) => {
    const option = document.createElement("option");
    option.value = name;
    option.textContent = name;
    assignmentSelect.appendChild(option);
  });

  const createOption = document.createElement("option");
  createOption.value = NEW_ASSIGNMENT_VALUE;
  createOption.textContent = "Create new assignment…";
  assignmentSelect.appendChild(createOption);

  let valueToSelect = "";

  if (selectedTitle && normalizedAssignments.includes(selectedTitle)) {
    valueToSelect = selectedTitle;
  } else if (selectedTitle) {
    valueToSelect = NEW_ASSIGNMENT_VALUE;
    pendingNewAssignmentTitle = selectedTitle;
  } else if (previousValue && normalizedAssignments.includes(previousValue)) {
    valueToSelect = previousValue;
  } else if (previousValue === NEW_ASSIGNMENT_VALUE) {
    valueToSelect = NEW_ASSIGNMENT_VALUE;
    pendingNewAssignmentTitle = previousPending;
  }

  if (valueToSelect) {
    assignmentSelect.value = valueToSelect;
  } else {
    assignmentSelect.value = "";
    placeholderOption.selected = true;
  }

  if (assignmentSelect.value === NEW_ASSIGNMENT_VALUE) {
    toggleNewAssignmentInput(true);
    if (assignmentTitleInput) {
      assignmentTitleInput.value = pendingNewAssignmentTitle;
    }
    currentAssignmentTitle = (assignmentTitleInput?.value || "").trim();
  } else if (assignmentSelect.value) {
    toggleNewAssignmentInput(false);
    currentAssignmentTitle = assignmentSelect.value;
    if (assignmentTitleInput) {
      assignmentTitleInput.value = "";
      pendingNewAssignmentTitle = "";
    }
  } else {
    toggleNewAssignmentInput(false);
    currentAssignmentTitle = "";
  }

  if (assignmentTitleInput && assignmentSelect.value === NEW_ASSIGNMENT_VALUE) {
    pendingNewAssignmentTitle = assignmentTitleInput.value;
  }
}

function initializeAssignmentControls() {
  if (assignmentSelect) {
    assignmentSelect.addEventListener("change", () => {
      if (assignmentSelect.value === NEW_ASSIGNMENT_VALUE) {
        toggleNewAssignmentInput(true);
        if (assignmentTitleInput) {
          assignmentTitleInput.value = pendingNewAssignmentTitle;
          assignmentTitleInput.focus();
        }
        currentAssignmentTitle = (assignmentTitleInput?.value || "").trim();
      } else if (assignmentSelect.value) {
        toggleNewAssignmentInput(false);
        currentAssignmentTitle = assignmentSelect.value;
      } else {
        toggleNewAssignmentInput(false);
        currentAssignmentTitle = "";
      }
    });
  }

  if (assignmentTitleInput) {
    assignmentTitleInput.addEventListener("input", () => {
      pendingNewAssignmentTitle = assignmentTitleInput.value;
      if (assignmentSelect?.value === NEW_ASSIGNMENT_VALUE) {
        currentAssignmentTitle = assignmentTitleInput.value.trim();
      }
    });
  }
}

async function hydrate() {
  try {
    const state = await fetch("/state").then((res) => res.json());
    updateStatus(state.status || "Idle");
    const authoritativeEntries = state.authoritativeFiles || {};
    Object.entries(authoritativeEntries).forEach(([category, info = {}]) => {
      const files = Array.isArray(info.files) ? info.files : [];
      const exists = typeof info.exists === "boolean" ? info.exists : false;
      const emptyMessage = exists
        ? "No files available."
        : "Folder not found.";
      refreshFileList(category, files, { emptyMessage });
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
    updateAssignmentOptions(state.assignments || [], state.assignmentTitle || "");
  } catch (error) {
    console.error("Failed to load initial state", error);
  }
}

document.querySelectorAll(".panel").forEach((panel) => initializePanel(panel));
initializeConcurrency();
initializeNitpickiness();
initializeGradingNotes();
initializeAssignmentControls();
hydrate();
