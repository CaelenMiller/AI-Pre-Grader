import csv
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app


@pytest.fixture()
def isolated_appdata(tmp_path, monkeypatch):
    appdata_dir = tmp_path / "appdata"
    appdata_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(app, "APPDATA_DIR", appdata_dir)

    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(app, "UPLOAD_DIR", uploads_dir)

    monkeypatch.setattr(app, "assignment_title", "Algebra-1", raising=False)

    yield appdata_dir


def test_pdf_routed_to_problem_state(tmp_path, isolated_appdata, monkeypatch):
    assignment_root = isolated_appdata / app.assignment_title
    submissions_dir = assignment_root / app.CATEGORY_TITLES["submissions"]
    submissions_dir.mkdir(parents=True, exist_ok=True)

    pdf_path = submissions_dir / "student-work.pdf"
    pdf_path.write_text("State: problem\nNeeds manual review", encoding="utf-8")

    def fake_pipeline(paths):
        return [
            app.SubmissionOutcome(
                submission=paths[0],
                state="problem",
                notes="OCR failed; unreadable submission.",
            )
        ]

    monkeypatch.setattr(app, "_run_grading_pipeline", fake_pipeline)

    with app.app.test_client() as client:
        response = client.post("/action/grade-submission")

    assert response.status_code == 200

    summary_path = assignment_root / app.OUTPUTS_KEY / "summary.csv"
    assert summary_path.exists()

    with summary_path.open(newline="", encoding="utf-8") as csvfile:
        rows = list(csv.reader(csvfile))

    # Header + single entry
    assert rows[1] == ["student-work.pdf", "001", "problem"]

    problem_note = assignment_root / app.OUTPUTS_KEY / "problem" / "001.txt"
    assert problem_note.exists()
    note_body = problem_note.read_text(encoding="utf-8")
    assert "student-work.pdf" in note_body
    assert "OCR failed" in note_body

