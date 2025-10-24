from pathlib import Path
from typing import Dict, List

from flask import Flask, jsonify, render_template, request
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
APPDATA_DIR = BASE_DIR / "appdata"

CATEGORY_TITLES: Dict[str, str] = {
    "solutions": "Solution",
    "problems": "Problems",
    "submissions": "Student Submissions",
}

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
    return render_template(
        "index.html",
        status=status_message,
        files=uploaded_files,
        appdata_files=appdata_files,
        max_concurrent=max_concurrent,
        nitpickiness=nitpickiness_level,
        grading_notes=grading_notes,
        assignment_title=assignment_title,
    )


def _ensure_category(category: str) -> Path:
    if category not in uploaded_files:
        raise ValueError(f"Unsupported category: {category}")
    path = UPLOAD_DIR / category
    path.mkdir(parents=True, exist_ok=True)
    return path


def _gather_appdata_files() -> Dict[str, Dict[str, object]]:
    data: Dict[str, Dict[str, object]] = {}
    for category, title in CATEGORY_TITLES.items():
        folder = APPDATA_DIR / title
        exists = folder.is_dir()
        if exists:
            files = [
                item.name
                for item in sorted(folder.iterdir(), key=lambda p: p.name.lower())
                if item.is_file()
            ]
        else:
            files = []
        data[category] = {"files": files, "exists": exists}
    return data


@app.route("/state")
def get_state():
    return jsonify(
        {
            "status": status_message,
            "files": uploaded_files,
            "appdataFiles": _gather_appdata_files(),
            "maxConcurrent": max_concurrent,
            "nitpickiness": nitpickiness_level,
            "gradingNotes": grading_notes,
            "assignmentTitle": assignment_title,
        }
    )


@app.route("/upload/<category>", methods=["POST"])
def upload(category: str):
    global assignment_title
    target_dir = _ensure_category(category)
    uploaded_files[category].clear()

    files = request.files.getlist("files")
    if not files:
        return jsonify({"message": "No files uploaded."}), 400

    title = request.form.get("assignmentTitle")
    if isinstance(title, str):
        assignment_title = title

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
    return jsonify({"message": "Files uploaded successfully.", "files": stored})


@app.route("/clear/<category>", methods=["POST"])
def clear(category: str):
    target_dir = _ensure_category(category)
    for item in target_dir.iterdir():
        if item.is_file():
            item.unlink()
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
    status_message = "Grading submission"
    return jsonify({"status": status_message})


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
