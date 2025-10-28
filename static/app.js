const statusEl = document.getElementById("status");
const concurrencySlider = document.getElementById("concurrency");
const concurrencyValue = document.getElementById("concurrency-value");
const nitpickinessSlider = document.getElementById("nitpickiness");
const nitpickinessValue = document.getElementById("nitpickiness-value");
const gradingNotesInput = document.getElementById("grading-notes");
const assignmentSelect = document.getElementById("assignment-selector");
const newAssignmentGroup = document.getElementById("new-assignment-group");
const assignmentTitleInput = document.getElementById("assignment-title");
const solutionContainsProblemCheckbox = document.getElementById(
  "solution-contains-problem"
);
const NEW_ASSIGNMENT_VALUE = "__new__";
let notesDebounceHandle;
let pendingNewAssignmentTitle = assignmentTitleInput?.value || "";
let currentAssignmentTitle = "";
let lastAssignmentSelectValue = assignmentSelect?.value || "";
let hydratedMaxConcurrent = Number(concurrencySlider?.value) || 1;

const gradingModal = document.getElementById("grading-modal");
const gradingModalElements = gradingModal
  ? {
      backdrop: gradingModal.querySelector(".grading-modal__backdrop"),
      assignmentLabel: gradingModal.querySelector(
        "[data-role='assignment-label']"
      ),
      progressWheel: gradingModal.querySelector(
        "[data-role='progress-wheel']"
      ),
      progressCount: gradingModal.querySelector(
        "[data-role='progress-count']"
      ),
      progressTotal: gradingModal.querySelector(
        "[data-role='progress-total']"
      ),
      runningCount: gradingModal.querySelector(
        "[data-role='count-running']"
      ),
      completedCount: gradingModal.querySelector(
        "[data-role='count-completed']"
      ),
      flaggedCount: gradingModal.querySelector(
        "[data-role='count-flagged']"
      ),
      issuesCount: gradingModal.querySelector(
        "[data-role='count-issues']"
      ),
      timeline: gradingModal.querySelector("[data-role='timeline']"),
      closeButton: gradingModal.querySelector("[data-role='close-modal']"),
      reportMessage: gradingModal.querySelector("[data-role='report-message']"),
      reportPath: gradingModal.querySelector("[data-role='report-path']"),
    }
  : null;

let activeGradingSessionPromise = null;
const FALLBACK_SUBMISSION_COUNT = 6;

function toggleNewAssignmentInput(show) {
  if (!newAssignmentGroup) return;
  newAssignmentGroup.hidden = !show;
}

if (assignmentSelect) {
  if (assignmentSelect.value === NEW_ASSIGNMENT_VALUE) {
    toggleNewAssignmentInput(true);
    pendingNewAssignmentTitle = (assignmentTitleInput?.value || "").trim();
    currentAssignmentTitle = "";
  } else if (assignmentSelect.value) {
    toggleNewAssignmentInput(false);
    currentAssignmentTitle = assignmentSelect.value;
  } else {
    toggleNewAssignmentInput(false);
    currentAssignmentTitle = "";
  }
} else {
  toggleNewAssignmentInput(Boolean(assignmentTitleInput?.value));
  pendingNewAssignmentTitle = (assignmentTitleInput?.value || "").trim();
  currentAssignmentTitle = (assignmentTitleInput?.value || "").trim();
}

function resolveCurrentTitle() {
  if (assignmentSelect?.value === NEW_ASSIGNMENT_VALUE) {
    return (pendingNewAssignmentTitle || "").trim();
  }
  return currentAssignmentTitle;
}

function formatDuration(milliseconds) {
  const totalTenths = Math.max(0, Math.round(milliseconds / 100));
  const seconds = Math.floor(totalTenths / 10);
  const tenths = totalTenths % 10;
  if (seconds < 60) {
    return tenths === 0 ? `${seconds}s` : `${seconds}.${tenths}s`;
  }
  const minutes = Math.floor(seconds / 60);
  const leftoverSeconds = seconds % 60;
  return `${minutes}m ${leftoverSeconds}s`;
}

function getCategoryFileNames(category) {
  const list = document.querySelector(
    `.file-list[data-files-category='${category}']`
  );
  if (!list) {
    return [];
  }
  return Array.from(list.children)
    .filter((item) => !item.classList.contains("empty"))
    .map((item) => (item.textContent || "").trim())
    .filter(Boolean);
}

function getSubmissionNames() {
  return getCategoryFileNames("submissions");
}

function sanitizeReportSegment(value) {
  const normalized = (value || "").trim();
  if (!normalized) {
    return "current_assignment";
  }
  return normalized
    .replace(/[^a-zA-Z0-9._-]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 80) || "current_assignment";
}

function buildReportPath(title) {
  const safeSegment = sanitizeReportSegment(title);
  return `appdata/${safeSegment}/submissions/grading-report.json`;
}

function openGradingModal() {
  if (!gradingModal) {
    return;
  }
  gradingModal.classList.add("is-open");
  gradingModal.setAttribute("aria-hidden", "false");
  document.body.classList.add("modal-open");
}

function closeGradingModal(force = false) {
  if (!gradingModal) {
    return;
  }
  if (!force && gradingModalElements?.closeButton?.disabled) {
    return;
  }
  gradingModal.classList.remove("is-open");
  gradingModal.setAttribute("aria-hidden", "true");
  document.body.classList.remove("modal-open");
}

function updateSummaryChips({ running = 0, completed = 0, flagged = 0, issues = 0 }) {
  if (!gradingModalElements) {
    return;
  }
  if (gradingModalElements.runningCount) {
    gradingModalElements.runningCount.textContent = String(running);
  }
  if (gradingModalElements.completedCount) {
    gradingModalElements.completedCount.textContent = String(completed);
  }
  if (gradingModalElements.flaggedCount) {
    gradingModalElements.flaggedCount.textContent = String(flagged);
  }
  if (gradingModalElements.issuesCount) {
    gradingModalElements.issuesCount.textContent = String(issues);
  }
}

function updateProgressWheel(total, finished) {
  if (!gradingModalElements) {
    return;
  }
  const safeTotal = Math.max(0, Number(total) || 0);
  const safeFinished = Math.max(0, Math.min(Number(finished) || 0, safeTotal));
  if (gradingModalElements.progressTotal) {
    gradingModalElements.progressTotal.textContent = String(safeTotal);
  }
  if (gradingModalElements.progressCount) {
    gradingModalElements.progressCount.textContent = String(safeFinished);
  }
  if (gradingModalElements.progressWheel) {
    const progress = safeTotal === 0 ? 0 : Math.min(1, safeFinished / safeTotal);
    gradingModalElements.progressWheel.style.setProperty(
      "--progress",
      `${Math.round(progress * 360)}deg`
    );
  }
}

function createTimelineRow(name, index) {
  const row = document.createElement("article");
  row.className = "timeline-row status-queued";

  const badge = document.createElement("div");
  badge.className = "timeline-row__badge";
  badge.textContent = String(index);

  const body = document.createElement("div");
  body.className = "timeline-row__body";

  const header = document.createElement("div");
  header.className = "timeline-row__header";

  const title = document.createElement("span");
  title.className = "timeline-row__title";
  title.textContent = name;

  const status = document.createElement("span");
  status.className = "timeline-row__status";
  status.textContent = "Queued";

  header.append(title, status);

  const meta = document.createElement("div");
  meta.className = "timeline-row__meta";

  const elapsed = document.createElement("span");
  elapsed.className = "timeline-row__elapsed";
  elapsed.textContent = "0s";

  meta.append(elapsed);

  const log = document.createElement("p");
  log.className = "timeline-row__log";
  log.textContent = "Waiting to start…";

  body.append(header, meta, log);
  row.append(badge, body);

  return {
    name,
    row,
    statusEl: status,
    logEl: log,
    elapsedEl: elapsed,
    timer: null,
    startTime: null,
  };
}

function prepareGradingModal(assignmentTitle, submissions = []) {
  if (!gradingModal || !gradingModalElements) {
    return null;
  }
  const names = submissions.length
    ? submissions
    : Array.from({ length: FALLBACK_SUBMISSION_COUNT }, (_, index) =>
        `Submission ${index + 1}`
      );
  const total = names.length;

  gradingModal.classList.remove("is-finished");
  const timelineEl = gradingModalElements.timeline;
  if (timelineEl) {
    timelineEl.innerHTML = "";
  }
  const assignmentLabel = assignmentTitle
    ? `Assignment: ${assignmentTitle}`
    : "Assignment: not yet saved";
  const submissionLabel = `${total} submission${total === 1 ? "" : "s"}`;
  if (gradingModalElements.assignmentLabel) {
    gradingModalElements.assignmentLabel.textContent = `${assignmentLabel} • ${submissionLabel}`;
  }
  if (gradingModalElements.reportMessage) {
    gradingModalElements.reportMessage.hidden = true;
  }
  if (gradingModalElements.reportPath) {
    gradingModalElements.reportPath.textContent = "";
  }
  updateProgressWheel(total, 0);
  updateSummaryChips({ running: 0, completed: 0, flagged: 0, issues: 0 });

  const items = names.map((name, index) => {
    const item = createTimelineRow(name, index + 1);
    timelineEl?.appendChild(item.row);
    return item;
  });

  if (timelineEl) {
    timelineEl.scrollTop = 0;
  }
  if (gradingModalElements.closeButton) {
    gradingModalElements.closeButton.disabled = true;
  }

  return {
    total,
    items,
    names,
  };
}

function runSimulatedGradingSession({
  assignmentTitle,
  submissions = [],
  maxConcurrent = 1,
}) {
  if (activeGradingSessionPromise) {
    return activeGradingSessionPromise;
  }

  const safeMaxConcurrent = Math.max(1, Number(maxConcurrent) || 1);

  if (!gradingModal || !gradingModalElements) {
    return new Promise((resolve) => {
      const duration = 1200 + Math.random() * 1800;
      window.setTimeout(() => {
        resolve({
          total: submissions.length,
          completed: submissions.length,
          flagged: 0,
          failed: 0,
          hasIssues: false,
          message: "Grading completed (simulation).",
        });
      }, duration);
    });
  }

  const prepared = prepareGradingModal(assignmentTitle, submissions);
  if (!prepared) {
    return Promise.resolve({
      total: 0,
      completed: 0,
      flagged: 0,
      failed: 0,
      hasIssues: false,
      message: "No submissions queued.",
    });
  }

  openGradingModal();

  const state = {
    total: prepared.total,
    items: prepared.items,
    running: 0,
    success: 0,
    flagged: 0,
    failed: 0,
  };

  let resolveSession;
  activeGradingSessionPromise = new Promise((resolve) => {
    resolveSession = resolve;
  });

  const getFinishedCount = () => state.success + state.flagged + state.failed;
  const getIssuesCount = () => state.flagged + state.failed;
  const refreshDisplays = () => {
    updateSummaryChips({
      running: state.running,
      completed: state.success,
      flagged: state.flagged,
      issues: getIssuesCount(),
    });
    updateProgressWheel(state.total, getFinishedCount());
  };

  let sessionFinished = false;

  const finishSession = () => {
    if (sessionFinished) {
      return;
    }
    sessionFinished = true;
    refreshDisplays();
    if (gradingModalElements.reportPath) {
      gradingModalElements.reportPath.textContent = buildReportPath(
        assignmentTitle
      );
    }
    if (gradingModalElements.reportMessage) {
      gradingModalElements.reportMessage.hidden = false;
    }
    gradingModal.classList.add("is-finished");
    if (gradingModalElements.closeButton) {
      gradingModalElements.closeButton.disabled = false;
      gradingModalElements.closeButton.focus();
    }
    const issuesCount = getIssuesCount();
    const pluralSuffix = issuesCount === 1 ? "" : "s";
    const summaryMessage =
      issuesCount > 0
        ? `Grading completed with ${issuesCount} submission${pluralSuffix} needing attention.`
        : "Grading completed successfully.";
    resolveSession({
      total: state.total,
      completed: state.success,
      flagged: state.flagged,
      failed: state.failed,
      hasIssues: issuesCount > 0,
      message: summaryMessage,
    });
    activeGradingSessionPromise = null;
  };

  const startItem = (item) => {
    state.running += 1;
    item.row.classList.remove(
      "status-queued",
      "status-complete",
      "status-flagged",
      "status-error"
    );
    item.row.classList.add("status-running");
    item.statusEl.textContent = "Running";
    item.logEl.textContent = "Evaluating submission…";
    item.startTime = Date.now();
    item.elapsedEl.textContent = "0s";
    item.timer = window.setInterval(() => {
      item.elapsedEl.textContent = formatDuration(
        Date.now() - item.startTime
      );
    }, 500);
    refreshDisplays();
    item.row.scrollIntoView({ behavior: "smooth", block: "end" });
  };

  const completeItem = (item, result) => {
    if (item.timer) {
      window.clearInterval(item.timer);
      item.timer = null;
    }
    state.running = Math.max(0, state.running - 1);
    const elapsed = Date.now() - (item.startTime || Date.now());
    item.elapsedEl.textContent = formatDuration(elapsed);

    item.row.classList.remove(
      "status-running",
      "status-queued",
      "status-complete",
      "status-flagged",
      "status-error"
    );

    const outcome = result?.outcome || "error";
    const logMessage = result?.logMessage;

    if (outcome === "success") {
      state.success += 1;
      item.row.classList.add("status-complete");
      item.statusEl.textContent = "Completed";
      item.logEl.textContent =
        logMessage || "Automatic feedback delivered.";
    } else if (outcome === "flagged") {
      state.flagged += 1;
      item.row.classList.add("status-flagged");
      item.statusEl.textContent = "Needs review";
      item.logEl.textContent =
        logMessage || "Manual follow-up recommended.";
    } else {
      state.failed += 1;
      item.row.classList.add("status-error");
      item.statusEl.textContent = "Problem";
      item.logEl.textContent =
        logMessage || "Processing error encountered.";
    }

    const finished = getFinishedCount();
    refreshDisplays();
    item.row.scrollIntoView({ behavior: "smooth", block: "end" });
    return finished;
  };

  const determineSimulatedResult = (fileName) => {
    const normalized = (fileName || "").toLowerCase();
    const extensionMatch = /\.([^.]+)$/.exec(fileName || "");
    const originalExtension = extensionMatch?.[1] || "";
    const extensionLabel = originalExtension
      ? originalExtension.toLowerCase()
      : "";

    if (normalized.endsWith(".pdf")) {
      return {
        outcome: "success",
        logMessage: "PDF submission processed successfully.",
        delay: 1200 + Math.random() * 2200,
      };
    }

    if (normalized.endsWith(".ipynb")) {
      return {
        outcome: "flagged",
        logMessage: "Notebook (.ipynb) requires manual review.",
        delay: 900 + Math.random() * 1500,
      };
    }

    return {
      outcome: "error",
      logMessage: extensionLabel
        ? `Unsupported file type detected (.${extensionLabel}).`
        : "Unsupported file type detected.",
      delay: 700 + Math.random() * 1200,
    };
  };

  let nextIndex = 0;

  const launchNext = () => {
    if (sessionFinished) {
      return;
    }
    while (
      state.running < safeMaxConcurrent &&
      nextIndex < state.items.length
    ) {
      const current = state.items[nextIndex];
      nextIndex += 1;
      startItem(current);
      const result = determineSimulatedResult(current.name);
      const duration = Math.max(400, Number(result?.delay) || 1200);
      window.setTimeout(() => {
        const finished = completeItem(current, result);
        if (finished >= state.total) {
          finishSession();
        } else {
          launchNext();
        }
      }, duration);
    }
  };

  if (state.total === 0) {
    finishSession();
  } else {
    window.setTimeout(launchNext, 350);
  }

  return activeGradingSessionPromise;
}

if (gradingModalElements?.closeButton) {
  gradingModalElements.closeButton.addEventListener("click", () =>
    closeGradingModal(true)
  );
}

if (gradingModalElements?.backdrop) {
  gradingModalElements.backdrop.addEventListener("click", () => {
    if (!gradingModalElements.closeButton?.disabled) {
      closeGradingModal(true);
    }
  });
}

document.addEventListener("keydown", (event) => {
  if (
    event.key === "Escape" &&
    gradingModal?.classList?.contains("is-open") &&
    !gradingModalElements?.closeButton?.disabled
  ) {
    closeGradingModal(true);
  }
});

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
    const effectiveTitle = resolveCurrentTitle();
    if (!effectiveTitle) {
      alert("Select or create an assignment before uploading files.");
      return;
    }
    const formData = new FormData();
    formData.append("assignmentTitle", effectiveTitle);
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
        alert(
          "Solution generation is not yet implemented. Please add your solution in the designated folder."
        );
        await updateStatus("Solution generation not implemented.");
      });
    }

    if (action === "grade") {
      button.addEventListener("click", async () => {
        if (button.disabled) {
          return;
        }

        const missingRequirements = [];
        const solutionFiles = getCategoryFileNames("solutions");
        if (solutionFiles.length === 0) {
          missingRequirements.push("solution PDF");
        }

        const requiresProblem = !solutionContainsProblemCheckbox?.checked;
        if (requiresProblem) {
          const problemFiles = getCategoryFileNames("problems");
          if (problemFiles.length === 0) {
            missingRequirements.push("problem PDF");
          }
        }

        const submissions = getSubmissionNames();
        if (submissions.length === 0) {
          missingRequirements.push("at least one submission file");
        }

        if (missingRequirements.length > 0) {
          const list = missingRequirements.map((item) => `• ${item}`).join("\n");
          window.alert(
            `Cannot start grading yet. Please add:\n${list}`
          );
          await updateStatus("Missing required files for grading.");
          return;
        }

        const assignmentTitle = resolveCurrentTitle();
        const sliderValue = Number(concurrencySlider?.value);
        const selectedMaxConcurrent =
          Number.isFinite(sliderValue) && sliderValue > 0
            ? sliderValue
            : Math.max(1, hydratedMaxConcurrent || 1);

        button.disabled = true;

        try {
          await updateStatus("Grading submissions…");
          try {
            await postJSON("/action/grade-submission", {});
          } catch (error) {
            console.warn("Failed to sync grading status", error);
          }
          const summary = await runSimulatedGradingSession({
            assignmentTitle,
            submissions,
            maxConcurrent: selectedMaxConcurrent,
          });
          await updateStatus(summary?.message || "Grading completed.");
        } catch (error) {
          console.error(error);
          alert(error.message || "Failed to simulate grading.");
          await updateStatus("Grading interrupted.");
        } finally {
          button.disabled = false;
        }
      });
    }
  });
}

function initializeConcurrency() {
  concurrencySlider?.addEventListener("input", () => {
    if (concurrencyValue) {
      concurrencyValue.textContent = concurrencySlider.value;
    }
    const rawValue = Number(concurrencySlider.value);
    if (Number.isFinite(rawValue) && rawValue > 0) {
      hydratedMaxConcurrent = rawValue;
    }
  });

  concurrencySlider?.addEventListener("change", async () => {
    const rawValue = Number(concurrencySlider.value);
    const safeValue = Math.max(
      1,
      Number.isFinite(rawValue) && rawValue > 0
        ? rawValue
        : Math.max(1, hydratedMaxConcurrent || 1)
    );
    if (!Number.isFinite(rawValue) || rawValue < 1) {
      concurrencySlider.value = String(safeValue);
      if (concurrencyValue) {
        concurrencyValue.textContent = String(safeValue);
      }
    }
    hydratedMaxConcurrent = safeValue;
    try {
      await postJSON("/settings/concurrency", { maxConcurrent: safeValue });
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
    currentAssignmentTitle = "";
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

  lastAssignmentSelectValue = assignmentSelect.value;
}

function applyState(state = {}) {
  updateStatus(state.status || "Idle");
  const authoritativeEntries = state.authoritativeFiles || {};
  Object.entries(authoritativeEntries).forEach(([category, info = {}]) => {
    const files = Array.isArray(info.files) ? info.files : [];
    const exists = typeof info.exists === "boolean" ? info.exists : false;
    const emptyMessage = exists ? "No files available." : "Folder not found.";
    refreshFileList(category, files, { emptyMessage });
  });
  if (typeof state.maxConcurrent === "number") {
    const safeMax = Math.max(1, Number(state.maxConcurrent) || 1);
    hydratedMaxConcurrent = safeMax;
    if (concurrencySlider) {
      concurrencySlider.value = String(safeMax);
    }
    if (concurrencyValue) {
      concurrencyValue.textContent = String(safeMax);
    }
  }
  if (typeof state.nitpickiness === "number" && nitpickinessSlider) {
    nitpickinessSlider.value = state.nitpickiness;
    nitpickinessValue.textContent = state.nitpickiness;
  }
  if (typeof state.gradingNotes === "string" && gradingNotesInput) {
    gradingNotesInput.value = state.gradingNotes;
  }
  updateAssignmentOptions(state.assignments || [], state.assignmentTitle || "");
}

function initializeAssignmentControls() {
  if (assignmentSelect) {
    assignmentSelect.addEventListener("change", async () => {
      const previousSelectValue = lastAssignmentSelectValue;
      const previousConfirmedTitle = currentAssignmentTitle;
      const selectedValue = assignmentSelect.value;
      lastAssignmentSelectValue = selectedValue;
      if (selectedValue === NEW_ASSIGNMENT_VALUE) {
        toggleNewAssignmentInput(true);
        if (assignmentTitleInput) {
          assignmentTitleInput.value = pendingNewAssignmentTitle;
          assignmentTitleInput.focus();
        }
        currentAssignmentTitle = "";
        return;
      }

      toggleNewAssignmentInput(false);

      if (!selectedValue) {
        currentAssignmentTitle = "";
        lastAssignmentSelectValue = "";
        return;
      }

      try {
        const state = await postJSON("/assignment/select", {
          assignmentTitle: selectedValue,
        });
        applyState(state);
        pendingNewAssignmentTitle = "";
      } catch (error) {
        alert(error.message);
        assignmentSelect.value = previousSelectValue;
        lastAssignmentSelectValue = previousSelectValue;
        currentAssignmentTitle = previousConfirmedTitle;
        if (previousSelectValue === NEW_ASSIGNMENT_VALUE) {
          toggleNewAssignmentInput(true);
          if (assignmentTitleInput) {
            assignmentTitleInput.value = pendingNewAssignmentTitle;
            assignmentTitleInput.focus();
          }
        } else {
          toggleNewAssignmentInput(false);
        }
      }
    });
  }

  if (assignmentTitleInput) {
    assignmentTitleInput.addEventListener("input", () => {
      pendingNewAssignmentTitle = assignmentTitleInput.value;
    });
  }
}

async function hydrate() {
  try {
    const state = await fetch("/state").then((res) => res.json());
    applyState(state);
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
