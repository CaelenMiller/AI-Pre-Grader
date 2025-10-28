import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from flask import Flask, jsonify, render_template, request
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
APPDATA_DIR = BASE_DIR / "appdata"

APPDATA_DIR.mkdir(parents=True, exist_ok=True)

CATEGORY_TITLES: Dict[str, str] = {
    "solutions": "solutions",
    "problems": "problems",
    "submissions": "submissions",
}

OUTPUTS_KEY = "outputs"
APPDATA_SECTIONS: Dict[str, str] = {**CATEGORY_TITLES, OUTPUTS_KEY: OUTPUTS_KEY}
OUTPUT_STATES: List[str] = ["done", "review", "problem"]


@dataclass
class SubmissionOutcome:
    """Container describing the grading result for a submission."""

    submission: Path
    state: str
    notes: str = ""

    def normalized_state(self) -> str:
        return _normalize_state(self.state)


def _normalize_state(state: Optional[str]) -> str:
    if state is None:
        return "review"
    normalized = state.strip().lower()
    return normalized if normalized in OUTPUT_STATES else "review"


def _run_grading_pipeline(submission_paths: List[Path]) -> List[SubmissionOutcome]:
    """Execute the grading pipeline and return structured outcomes per submission.

    The real project wires this into the pre-grader/runner stack. For the purposes of
    this repository snapshot we simulate the behaviour by reading an optional
    "State:" marker from each submission file. If the marker is not present (or the
    file cannot be read), the submission defaults to the "review" state.
    """

    outcomes: List[SubmissionOutcome] = []

    for path in submission_paths:
        detected_state = "review"
        note = "Detailed grading notes are pending."

        try:
            contents = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            contents = ""

        for line in contents.splitlines():
            if line.lower().startswith("state:"):
                candidate = line.split(":", 1)[1].strip()
                if candidate:
                    detected_state = candidate.lower()
                break

        normalized = _normalize_state(detected_state)
        if normalized == "done":
            note = "Automated checks reported no issues."
        elif normalized == "review":
            note = "Requires human review."
        elif normalized == "problem":
            note = "Automatic grading detected a blocking problem."

        outcomes.append(SubmissionOutcome(submission=path, state=detected_state, notes=note))

    return outcomes

app = Flask(__name__)

# Application state
uploaded_files: Dict[str, List[str]] = {
    "solutions": [],
    "problems": [],
    "submissions": [],
}
status_message: str = "Idle"
max_concurrent: int = 1
nitpickiness_level: int = 3
grading_notes: str = ""
assignment_title: str = ""


@app.route("/")
def index():
    appdata_files = _gather_appdata_files()
    authoritative_files = _build_authoritative_file_state(appdata_files)
    return render_template(
        "index.html",
        status=status_message,
        files=uploaded_files,
        authoritative_files=authoritative_files,
        assignments=_list_assignments(),
        max_concurrent=max_concurrent,
        nitpickiness=nitpickiness_level,
        grading_notes=grading_notes,
        assignment_title=assignment_title,
    )


def _ensure_category(category: str, base_dir: Optional[Path] = None) -> Path:
    if category not in uploaded_files:
        raise ValueError(f"Unsupported category: {category}")
    root = base_dir if base_dir is not None else UPLOAD_DIR
    root.mkdir(parents=True, exist_ok=True)
    if base_dir is not None:
        folder_name = CATEGORY_TITLES.get(category, category)
    else:
        folder_name = category
    path = root / folder_name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _gather_appdata_files() -> Dict[str, Dict[str, object]]:
    data: Dict[str, Dict[str, object]] = {}

    if not assignment_title:
        for category in APPDATA_SECTIONS:
            data[category] = {"files": [], "exists": False}
        return data

    assignment_root = APPDATA_DIR / assignment_title

    for category, folder_name in APPDATA_SECTIONS.items():
        folder = assignment_root / folder_name
        exists = folder.is_dir()
        if exists:
            if category == OUTPUTS_KEY:
                files = []
                for item in folder.rglob("*"):
                    if item.is_file():
                        relative = item.relative_to(folder)
                        files.append(relative.as_posix())
                files.sort(key=lambda name: name.lower())
            else:
                files = [
                    item.name
                    for item in sorted(folder.iterdir(), key=lambda p: p.name.lower())
                    if item.is_file()
                ]
        else:
            files = []
        data[category] = {"files": files, "exists": exists}
    return data


def _build_authoritative_file_state(
    appdata: Optional[Dict[str, Dict[str, object]]] = None,
) -> Dict[str, Dict[str, object]]:
    source = appdata if appdata is not None else _gather_appdata_files()
    authoritative: Dict[str, Dict[str, object]] = {}

    for category in APPDATA_SECTIONS:
        info = source.get(category, {})
        files = info.get("files") if isinstance(info, dict) else []
        if not isinstance(files, list):
            files = []
        exists = info.get("exists") if isinstance(info, dict) else False
        authoritative[category] = {
            "files": files,
            "exists": bool(exists),
        }

    return authoritative


def _list_assignments() -> List[str]:
    if not APPDATA_DIR.exists():
        return []
    assignments = [
        item.name
        for item in APPDATA_DIR.iterdir()
        if item.is_dir()
    ]
    assignments.sort(key=lambda name: name.lower())
    return assignments


def _build_state_payload() -> Dict[str, object]:
    appdata_files = _gather_appdata_files()
    return {
        "status": status_message,
        "files": uploaded_files,
        "appdataFiles": appdata_files,
        "authoritativeFiles": _build_authoritative_file_state(appdata_files),
        "assignments": _list_assignments(),
        "maxConcurrent": max_concurrent,
        "nitpickiness": nitpickiness_level,
        "gradingNotes": grading_notes,
        "assignmentTitle": assignment_title,
    }


@app.route("/state")
def get_state():
    return jsonify(_build_state_payload())


@app.route("/assignment/select", methods=["POST"])
def select_assignment():
    global assignment_title

    payload = request.get_json(silent=True) or {}
    raw_title = payload.get("assignmentTitle") or payload.get("title") or ""
    requested_title = (raw_title or "").strip()
    if not requested_title:
        return jsonify({"message": "assignmentTitle is required."}), 400

    safe_title = secure_filename(requested_title)
    if not safe_title:
        return (
            jsonify(
                {
                    "message": "Title must include letters or numbers after removing unsafe characters.",
                }
            ),
            400,
        )

    assignment_path = APPDATA_DIR / safe_title
    if not assignment_path.exists() or not assignment_path.is_dir():
        return jsonify({"message": "Assignment not found."}), 404

    assignment_title = safe_title
    for category in uploaded_files:
        uploaded_files[category].clear()

    return jsonify(_build_state_payload())


@app.route("/upload/<category>", methods=["POST"])
def upload(category: str):
    global assignment_title
    if category not in uploaded_files:
        return jsonify({"message": f"Unsupported category: {category}."}), 404

    raw_title = request.form.get("assignmentTitle")
    if raw_title is None:
        raw_title = request.form.get("title", "")
    title = (raw_title or "").strip()
    if not title:
        return jsonify({"message": "A title is required."}), 400

    safe_title = secure_filename(title)
    if not safe_title:
        return jsonify({"message": "Title must include letters or numbers after removing unsafe characters."}), 400

    assignment_title = safe_title

    uploaded_files[category].clear()

    files = request.files.getlist("files")
    if not files:
        return jsonify({"message": "No files uploaded."}), 400

    submission_root = APPDATA_DIR / assignment_title
    target_dir = _ensure_category(category, submission_root)

    stored = []
    for file_storage in files:
        filename = secure_filename(file_storage.filename)
        if not filename:
            continue
        filepath = target_dir / filename
        file_storage.save(filepath)
        stored.append(filename)

    if not stored:
        return jsonify({"message": "No valid files uploaded."}), 400

    uploaded_files[category] = stored
    return jsonify(
        {
            "message": "Files uploaded successfully.",
            "files": stored,
            "assignmentTitle": assignment_title,
        }
    )


@app.route("/clear/<category>", methods=["POST"])
def clear(category: str):
    if category not in uploaded_files:
        return jsonify({"message": f"Unsupported category: {category}."}), 404

    if not assignment_title:
        return jsonify({"message": "No assignment selected."}), 400

    assignment_root = APPDATA_DIR / assignment_title
    folder_name = CATEGORY_TITLES.get(category, category)
    target_dir = assignment_root / folder_name

    if target_dir.is_dir():
        for item in target_dir.iterdir():
            if item.is_file():
                item.unlink()

        uploaded_files[category].clear()

        def _prune_empty(path: Path, stop: Path) -> None:
            current = path
            while current != stop and current.exists():
                try:
                    next(current.iterdir())
                except StopIteration:
                    current.rmdir()
                    current = current.parent
                else:
                    break

        _prune_empty(target_dir, APPDATA_DIR)
    else:
        uploaded_files[category].clear()

    return jsonify({"message": "Cleared."})


@app.route("/action/generate-solution", methods=["POST"])
def action_generate_solution():
    global status_message
    status_message = "Generating solution"
    return jsonify({"status": status_message})


@app.route("/action/grade-submission", methods=["POST"])
def action_grade_submission():
    global status_message
    if not assignment_title:
        status_message = "No assignment selected"
        return jsonify({"message": "No assignment selected."}), 400

    assignment_root = APPDATA_DIR / assignment_title
    submissions_dir = assignment_root / CATEGORY_TITLES["submissions"]

    submissions = []
    if submissions_dir.is_dir():
        submissions = [
            item
            for item in submissions_dir.iterdir()
            if item.is_file()
        ]
        submissions.sort(key=lambda path: path.name.lower())

    outputs_dir = assignment_root / OUTPUTS_KEY
    outputs_dir.mkdir(parents=True, exist_ok=True)

    state_directories = {}
    for state in OUTPUT_STATES:
        state_path = outputs_dir / state
        state_path.mkdir(parents=True, exist_ok=True)
        state_directories[state] = state_path

    for directory in state_directories.values():
        for existing_note in directory.glob("*.txt"):
            if existing_note.is_file():
                existing_note.unlink()

    summary_rows: List[List[str]] = []
    outcomes = _run_grading_pipeline(submissions)
    outcome_map = {result.submission.resolve(): result for result in outcomes}

    for index, submission in enumerate(submissions, start=1):
        submission_id = f"{index:03d}"
        submission_name = submission.name
        outcome = outcome_map.get(submission.resolve())
        if outcome is None:
            outcome = SubmissionOutcome(submission=submission, state="review")

        state = outcome.normalized_state()
        summary_rows.append([submission_name, submission_id, state])

        state_dir = state_directories.get(state, state_directories["review"])
        note_path = state_dir / f"{submission_id}.txt"
        note_lines = [f"Submission: {submission_name}", ""]
        notes = outcome.notes.strip()
        if notes:
            note_lines.append(notes)
        else:
            note_lines.append("Detailed grading notes are pending.")
        note_path.write_text("\n".join(note_lines) + "\n", encoding="utf-8")

    summary_path = outputs_dir / "summary.csv"
    with summary_path.open("w", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["submission_name", "id", "state"])
        writer.writerows(summary_rows)

    status_message = "Grading summary generated"
    return jsonify(
        {
            "status": status_message,
            "summaryPath": f"appdata/{assignment_title}/{OUTPUTS_KEY}/summary.csv",
            "totalSubmissions": len(summary_rows),
        }
    )


@app.route("/settings/concurrency", methods=["POST"])
def update_concurrency():
    global max_concurrent
    payload = request.get_json(silent=True) or {}
    value = payload.get("maxConcurrent")
    if not isinstance(value, int) or not (1 <= value <= 10):
        return jsonify({"message": "maxConcurrent must be an integer between 1 and 10."}), 400
    max_concurrent = value
    return jsonify({"message": "Updated.", "maxConcurrent": max_concurrent})


@app.route("/settings/nitpickiness", methods=["POST"])
def update_nitpickiness():
    global nitpickiness_level
    payload = request.get_json(silent=True) or {}
    value = payload.get("level")
    if not isinstance(value, int) or not (1 <= value <= 5):
        return jsonify({"message": "level must be an integer between 1 and 5."}), 400
    nitpickiness_level = value
    return jsonify({"message": "Updated.", "nitpickiness": nitpickiness_level})


@app.route("/settings/notes", methods=["POST"])
def update_notes():
    global grading_notes
    payload = request.get_json(silent=True) or {}
    notes = payload.get("notes", "")
    if not isinstance(notes, str):
        return jsonify({"message": "notes must be a string."}), 400
    grading_notes = notes
    return jsonify({"message": "Updated.", "gradingNotes": grading_notes})


if __name__ == "__main__":
    app.run(debug=True)
