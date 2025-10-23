#!/usr/bin/env python3
# main.py
"""
Pre-grading CLI + minimal PDF/Image agent runner (async) + solution synthesis.
- Resolves ./Homeworks/<folder> unless path already includes "Homeworks"
- Creates outputs under ./Homeworks/<folder>/outputs/run_<timestamp>/
- Builds anonymization map (001, 002, …)
- If no solution PDF, uses gpt-5 to synthesize a plain-text solution and emits a simple PDF
- Spawns up to --max-async agents (default 5) to analyze each alias (PDF/PNG/JPG only)
- Writes pregrading_results.csv with (alias_id, original_name, major_count, moderate_count, agent_output)

Notes:
- ClassInfo is INCLUDED ONLY IF you pass a path to a .txt file via --class.
- Materials/ and outputs/ folders are ignored (case-insensitive) when gathering submissions.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

# ---------- OpenAI Agents SDK ----------
from dotenv import load_dotenv; load_dotenv(override=True)
from agents import Agent, Runner  # type: ignore

# ---------- PDF/Image handling ----------
import fitz  # PyMuPDF
from PIL import Image

# ---------- Optional OCR ----------
try:
    import pytesseract  # type: ignore
    OCR_AVAILABLE = True
except Exception:
    OCR_AVAILABLE = False


# --------------------------- Data Models ---------------------------

@dataclass
class CLIConfig:
    submissions_dir: Path
    problem_set_path: Optional[Path]
    solutions_path: Optional[Path]
    notes: Optional[str]
    class_info_path: Optional[Path]   # may be None or non-.txt; only .txt is used
    max_async: int
    out_dir: Path

@dataclass
class SubmissionUnit:
    """One anonymized submission (may include multiple files)."""
    alias_id: str                  # "001", "002", ...
    original_name: str             # original top-level file or dir name
    original_path: Path            # path to original file or folder
    normalized_dir: Path           # where we copy/normalize inside out_dir/students/alias_id
    notes: str = ""                # e.g., "multi-file folder" or warnings


# --------------------------- Helpers ---------------------------

def _timestamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")

def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)

def _default_materials_base(hw_root: Path) -> Path:
    # case-insensitive convenience: prefer "Materials" if exists, else "materials"
    cand = hw_root / "Materials"
    return cand if cand.exists() else (hw_root / "materials")

def _resolve_homeworks_root(folder_arg: str) -> Path:
    """
    If the provided path already includes 'Homeworks' in any component, use as-is.
    Otherwise, prefix with './Homeworks/'.
    """
    p = Path(folder_arg).expanduser()
    if any(part.lower() == "homeworks" for part in p.parts):
        return p.resolve()
    return (Path("./Homeworks") / p).resolve()

def _resolve_defaults(hw_root: Path,
                      problem_set: Optional[str],
                      solutions: Optional[str]) -> Tuple[Optional[Path], Optional[Path]]:
    # Default to <hw_root>/Materials/problems.pdf and solutions.pdf (case-insensitive)
    if problem_set:
        ps = Path(problem_set).expanduser()
    else:
        base = _default_materials_base(hw_root)
        ps = base / "problems.pdf"
    if solutions:
        sol = Path(solutions).expanduser()
    else:
        base = _default_materials_base(hw_root)
        sol = base / "solutions.pdf"
    return ps, sol

def _is_hidden(p: Path) -> bool:
    return p.name.startswith(".") or p.name.startswith("__")

def _discover_submission_units(submissions_dir: Path, students_root: Path) -> List[SubmissionUnit]:
    """
    Treat each top-level file OR folder in submissions_dir as a submission unit.
    Skip internal dirs like 'materials' and 'outputs' (case-insensitive).
    Hidden files/dirs are ignored.
    """
    SKIP_DIRS = {"materials", "outputs"}
    entries = [
        e for e in sorted(submissions_dir.iterdir(), key=lambda p: p.name.lower())
        if not _is_hidden(e) and e.name.lower() not in SKIP_DIRS
    ]

    units: List[SubmissionUnit] = []
    alias_counter = 1

    for e in entries:
        alias_id = f"{alias_counter:03d}"
        alias_counter += 1

        norm_dir = students_root / alias_id
        _ensure_dir(norm_dir)

        if e.is_dir():
            dst = norm_dir / e.name
            shutil.copytree(e, dst, dirs_exist_ok=True)
            note = "multi-file folder"
        elif e.is_file():
            dst = norm_dir / e.name
            shutil.copy2(e, dst)
            note = "single file"
        else:
            # Unsupported entry type; do not consume an alias number
            alias_counter -= 1
            continue

        units.append(SubmissionUnit(
            alias_id=alias_id,
            original_name=e.name,
            original_path=e.resolve(),
            normalized_dir=norm_dir.resolve(),
            notes=note
        ))

    return units

def _write_anonymization_map(units: List[SubmissionUnit], out_path: Path) -> None:
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["alias_id", "original_name", "original_path", "normalized_dir", "notes"])
        for u in units:
            w.writerow([u.alias_id, u.original_name, str(u.original_path), str(u.normalized_dir), u.notes])

def _write_run_manifest(cfg: CLIConfig,
                        units: List[SubmissionUnit],
                        manifest_path: Path,
                        warnings: List[str]) -> None:
    payload = {
        "config": {
            "submissions_dir": str(cfg.submissions_dir.resolve()),
            "problem_set_path": str(cfg.problem_set_path.resolve()) if cfg.problem_set_path and cfg.problem_set_path.exists() else None,
            "solutions_path": str(cfg.solutions_path.resolve()) if cfg.solutions_path and cfg.solutions_path.exists() else None,
            "notes": cfg.notes or "",
            "class_info_path": str(cfg.class_info_path.resolve()) if cfg.class_info_path else None,
            "max_async": cfg.max_async,
            "out_dir": str(cfg.out_dir.resolve()),
        },
        "discovered_submissions": [
            {
                "alias_id": u.alias_id,
                "original_name": u.original_name,
                "original_path": str(u.original_path),
                "normalized_dir": str(u.normalized_dir),
                "notes": u.notes
            } for u in units
        ],
        "warnings": warnings,
        "next_steps": [
            "Run agents on each alias (PDF/PNG/JPG supported).",
            "Aggregate outputs into pregrading_results.csv."
        ]
    }
    manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# --------------------------- PDF/Image extraction ---------------------------

def _render_pdf_page_to_image(page: fitz.Page, dpi: int = 200) -> Image.Image:
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)

def _ocr_image(img: Image.Image) -> str:
    if not OCR_AVAILABLE:
        return "[UNREADABLE] OCR not available."
    try:
        return pytesseract.image_to_string(img)
    except Exception as e:
        return f"[UNREADABLE] OCR failed: {e}"

def extract_pdf_text_with_ocr_fallback(pdf_path: Path,
                                       max_pages: Optional[int] = None,
                                       dpi: int = 200) -> Tuple[str, int, int]:
    """
    Extract text from a PDF. For pages with little/no text, rasterize and OCR (if available).
    Returns: (combined_text, total_pages, ocr_pages_used)
    """
    doc = fitz.open(pdf_path)
    total = len(doc)
    parts: List[str] = []
    ocr_pages = 0
    limit = min(total, max_pages) if max_pages is not None else total

    for i in range(limit):
        page = doc[i]
        txt = (page.get_text("text") or "").strip()
        if len(txt) < 10:
            img = _render_pdf_page_to_image(page, dpi=dpi)
            ocr_txt = _ocr_image(img)
            if not ocr_txt.startswith("[UNREADABLE]") and ocr_txt.strip():
                ocr_pages += 1
                txt = ocr_txt
            else:
                txt = "[UNREADABLE_PAGE] No extractable text; OCR unavailable/failed."
        parts.append(f"\n--- PAGE {i+1}/{total} ---\n{txt}\n")

    doc.close()
    return "".join(parts), total, ocr_pages

def extract_image_text(img_path: Path) -> str:
    try:
        img = Image.open(img_path).convert("RGB")
    except Exception as e:
        return f"[ERROR] Failed to open image {img_path.name}: {e}"
    ocr_txt = _ocr_image(img)
    if not ocr_txt.strip() or ocr_txt.startswith("[UNREADABLE]"):
        return f"[UNREADABLE_IMAGE] {img_path.name}: OCR unavailable or failed."
    return ocr_txt


# --------------------------- Agent bits ---------------------------

AGENT_SYSTEM_PROMPT_TEMPLATE = """
You are a math-focused pre-grading assistant. Do NOT assign grades or points.
Your job: identify likely errors, gaps in understanding, missing steps, and misapplied theorems.
Be concise and concrete. Prefer math-specific tags (e.g., chain_rule_misuse, missing_justification, units_mismatch).
Classify errors in terms of severity (major, moderate, minor), where major implies completely wrong approach/understanding, moderate implies significant mistake, and minor minor mistakes.
Only assess errors associated with the assigned problems. For example, if a problem does not require an explanation, then do not claim that the lack of one is an error.
Do not be excessively nitpicky, unless rigor is demanded and the problem requires it. For example, if numeric tests are not required, do not mention them.

{classinfo_clause}

Rules:
- If the student's material is unreadable (or mostly unreadable), say so and STOP (no analysis).
- Otherwise, list problem points as concise bullets with brief evidence (page/section hints).
- If solution is missing or mismatched, state the limitation.
- Keep output ≤ 300 words. No grades, no percentages.
""".strip()

def make_agent(classinfo_txt: Optional[str]) -> Agent:
    if classinfo_txt:
        classinfo_clause = f"Class information (for expectations and tone):\n{classinfo_txt}"
    else:
        classinfo_clause = "Class information: [none provided]"
    system_prompt = AGENT_SYSTEM_PROMPT_TEMPLATE.format(classinfo_clause=classinfo_clause)
    return Agent(name="PreGraderPDFImage", instructions=system_prompt, model="gpt-5-mini")


# --------------------------- Solution Synthesis (gpt-5) ---------------------------

def _write_text_pdf(out_pdf: Path, text: str, *, fontname: str = "helv", fontsize: int = 11, margin: int = 36) -> None:
    """
    Minimal text-to-PDF using PyMuPDF. Not a markdown/LaTeX renderer; writes plain text
    into one or more pages using simple insert_text calls.
    """
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    width, height = fitz.paper_size("letter")  # 612 x 792 pts
    x = margin
    y = margin
    max_y = height - margin
    line_height = fontsize * 1.35
    page = doc.new_page(width=width, height=height)
    for line in text.split("\n"):
        if y + line_height > max_y:
            page = doc.new_page(width=width, height=height)
            y = margin
        page.insert_text((x, y), line, fontname=fontname, fontsize=fontsize)
        y += line_height
    doc.save(out_pdf)
    doc.close()

def _call_agent_in_thread(agent: Agent, user_prompt: str) -> str:
    """
    Run Runner.run_sync from a worker thread or sync path where no loop exists.
    Ensures a loop is set in the current thread (needed on Windows).
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    result = Runner.run_sync(agent, user_prompt)
    return str(getattr(result, "output_text", result))

def _make_solution_agent_gpt5(classinfo_txt: Optional[str]) -> Agent:
    system = (
        "You are a math solution author. Write a clear, correct, step-by-step solution "
        "in plain text (no LaTeX required). Use numbered steps, define symbols, and justify key steps. "
        "If multiple problems exist, separate them with clear headers like 'Problem 1', 'Problem 2', etc. "
        "Be concise but complete; avoid extraneous commentary."
    )
    if classinfo_txt:
        system += "\n\nClass information:\n" + classinfo_txt
    return Agent(name="SolutionSynthesizer", instructions=system, model="gpt-5")

def synthesize_solution_if_missing(
    problem_pdf: Optional[Path],
    classinfo_text: Optional[str],
    out_dir: Path,
    *,
    max_pages_problem: int = 20,
) -> Optional[Path]:
    """
    If we lack a solution PDF, ask gpt-5 to produce a plain-text solution from the problem PDF text,
    then emit a minimal PDF we can feed back into the grader. Returns the generated PDF path, or None on failure.
    """
    if not problem_pdf or not problem_pdf.exists():
        print("[WARN] Cannot synthesize solution: problem PDF missing.", file=sys.stderr)
        return None

    print("[INFO] No solution PDF found. Generating one with gpt-5 …", file=sys.stderr)

    # 1) Extract problem text
    try:
        prob_text, prob_pages, prob_ocr = extract_pdf_text_with_ocr_fallback(problem_pdf, max_pages=max_pages_problem)
    except Exception as e:
        print(f"[ERROR] Failed to read problem PDF for synthesis: {e}", file=sys.stderr)
        return None

    # 2) Build prompt and call gpt-5
    user = (
        "Generate a complete, plain-text solution (no LaTeX) to the following problem set.\n"
        "Use numbered steps, and separate multiple problems with clear headers.\n\n"
        f"Problem PDF text (pages={prob_pages}, ocr_pages={prob_ocr}):\n{prob_text}\n"
    )
    agent = _make_solution_agent_gpt5(classinfo_text or None)
    try:
        solution_text = _call_agent_in_thread(agent, user)
    except Exception as e:
        print(f"[ERROR] Solution synthesis failed: {e}", file=sys.stderr)
        return None

    # 3) Save as text and as a simple PDF
    gen_dir = out_dir / "generated"
    gen_dir.mkdir(parents=True, exist_ok=True)
    txt_path = gen_dir / "solution_generated.txt"
    pdf_path = gen_dir / "solution_generated.pdf"
    try:
        txt_path.write_text(solution_text, encoding="utf-8")
        _write_text_pdf(pdf_path, solution_text)
        print(f"[INFO] Generated solution at: {pdf_path}", file=sys.stderr)
        return pdf_path
    except Exception as e:
        print(f"[ERROR] Writing generated solution files failed: {e}", file=sys.stderr)
        return None


# --------------------------- Async runner per alias ---------------------------

PDF_EXTS = {".pdf"}
IMG_EXTS = {".png", ".jpg", ".jpeg"}
SKIP_DIRS_CI = {"materials", "outputs"}  # case-insensitive

async def _run_all_coroutines(coros):
    # Keep the batch running; exceptions are returned as list items
    return await asyncio.gather(*coros, return_exceptions=True)

def _gather_submission_files(alias_dir: Path) -> List[Path]:
    files: List[Path] = []
    for root, dirs, filenames in os.walk(alias_dir):
        # prune skip dirs (case-insensitive)
        dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS_CI]
        for name in filenames:
            p = Path(root) / name
            if p.suffix.lower() in PDF_EXTS.union(IMG_EXTS):
                files.append(p)
    files.sort(key=lambda x: x.name.lower())
    return files

def _read_classinfo_if_txt(class_info_path: Optional[Path]) -> Optional[str]:
    if not class_info_path:
        return None
    p = class_info_path.expanduser()
    if not p.exists() or not p.is_file() or p.suffix.lower() != ".txt":
        # Only use if a concrete .txt file is provided
        return None
    try:
        return p.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        return f"[WARN] Failed to read class info: {e}"

def _count_severities(text: str) -> Tuple[int, int]:
    """
    Very simple heuristic: count occurrences of 'major' and 'moderate' tokens.
    (We ignore 'minor' in the CSV per request.)
    """
    t = text.lower()
    major = len(re.findall(r"\bmajor\b", t))
    moderate = len(re.findall(r"\bmoderate\b", t))
    return major, moderate

async def _grade_one_alias(
    sem: asyncio.Semaphore,
    agent: Agent,
    problem_pdf: Optional[Path],
    solution_pdf: Optional[Path],
    alias: SubmissionUnit,
    *,
    max_pages_problem: int = 15,
    max_pages_submission: int = 25,
    max_pages_solution: int = 15,
) -> Tuple[str, str, int, int, str]:
    """
    Returns: (alias_id, original_name, major_count, moderate_count, agent_output)
    """
    async with sem:
        try:
            # Gather student files (PDF/Images), ignoring Materials/outputs
            sub_files = _gather_submission_files(alias.normalized_dir)

            if not sub_files:
                agent_output = "[No supported files (PDF/PNG/JPG) found for this submission.]"
                return (alias.alias_id, alias.original_name, 0, 0, agent_output)

            # Build USER prompt:
            prob_text, prob_pages, prob_ocr = "", 0, 0
            if problem_pdf and problem_pdf.exists():
                prob_text, prob_pages, prob_ocr = extract_pdf_text_with_ocr_fallback(
                    problem_pdf, max_pages=max_pages_problem
                )
            else:
                prob_text = "[INFO] Problem PDF not provided or not found."

            if solution_pdf and solution_pdf.exists():
                sol_text, sol_pages, sol_ocr = extract_pdf_text_with_ocr_fallback(
                    solution_pdf, max_pages=max_pages_solution
                )
                solution_clause = f"Solution (PDF→text; pages={sol_pages}, ocr_pages={sol_ocr}):\n{sol_text}"
            else:
                solution_clause = "[WARN] No solution PDF provided; analysis may be limited."

            subs_blobs: List[str] = []
            for p in sub_files:
                if p.suffix.lower() in PDF_EXTS:
                    txt, pages, ocr_used = extract_pdf_text_with_ocr_fallback(
                        p, max_pages=max_pages_submission
                    )
                    subs_blobs.append(
                        f"\n=== SUBMISSION (PDF): {p.name} ===\n[meta] pages={pages}, ocr_pages={ocr_used}\n{txt}\n"
                    )
                elif p.suffix.lower() in IMG_EXTS:
                    txt = extract_image_text(p)
                    subs_blobs.append(f"\n=== SUBMISSION (IMAGE): {p.name} ===\n{txt}\n")

            USER = f"""
Problem (PDF→text; pages={prob_pages}, ocr_pages={prob_ocr}):
{prob_text}

{solution_clause}

Student submission(s) (analyze for problem points):
{''.join(subs_blobs)}

Task:
Identify likely problem points in the student's work. Classify by severity (major, moderate, minor). Do not grade.
If unreadable, say so and stop.
""".strip()

            # Call model (Runner.run_sync is blocking; run it in a thread for concurrency)
            agent_output = await asyncio.to_thread(_call_agent_in_thread, agent, USER)
            major, moderate = _count_severities(agent_output)
            return (alias.alias_id, alias.original_name, major, moderate, agent_output)

        except Exception as e:
            # Never let an exception bubble up; return a tuple with context
            msg = f"[ERROR] {type(e).__name__}: {e}"
            return (alias.alias_id, alias.original_name, 0, 0, msg)


# --------------------------- CLI ---------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pregrade-cli",
        description="Simple CLI to set up and run a pre-grading pass (PDF/Images) with async agents.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--folder",
        required=True,
        help=("Folder containing completed assignments. "
              "If it already includes 'Homeworks' in the path, it's used as-is; "
              "otherwise './Homeworks/<folder>' is assumed.")
    )
    parser.add_argument(
        "--problem-set",
        default=None,
        help="Path to problem set PDF (default: <hw_root>/Materials/problems.pdf)."
    )
    parser.add_argument(
        "--solutions",
        default=None,
        help="Path to solutions PDF (default: <hw_root>/Materials/solutions.pdf)."
    )
    parser.add_argument(
        "--notes",
        default=None,
        help="Additional notes to pass to the grader/agents (currently unused, reserved)."
    )
    parser.add_argument(
        "--class",
        dest="class_info",
        default=None,  # IMPORTANT: only use if a .txt file is passed
        help="Path to a SINGLE .txt file to include as ClassInfo. If omitted or not a .txt, no ClassInfo is used."
    )
    parser.add_argument(
        "--max-async",
        type=int,
        default=5,  # per your request
        help="Max number of agents running concurrently."
    )
    parser.add_argument(
        "--out",
        default=None,
        help=("Optional output directory. "
              "Defaults to <hw_root>/outputs/run_<timestamp>/")
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Resolve the homework root
    hw_root = _resolve_homeworks_root(args.folder)
    if not hw_root.exists() or not hw_root.is_dir():
        print(f"[ERROR] Homework folder not found or not a directory: {hw_root}", file=sys.stderr)
        return 2

    # Resolve materials
    ps_path, sol_path = _resolve_defaults(hw_root, args.problem_set, args.solutions)

    # ClassInfo: ONLY use if a concrete .txt file is provided
    class_info_path = Path(args.class_info).expanduser() if args.class_info else None
    classinfo_text = _read_classinfo_if_txt(class_info_path)

    # Output directory
    default_out = hw_root / "outputs" / f"run_{_timestamp()}"
    out_dir = Path(args.out).expanduser().resolve() if args.out else default_out.resolve()
    students_root = out_dir / "students"
    _ensure_dir(out_dir)
    _ensure_dir(students_root)

    # Discover & anonymize (skip materials/outputs at top-level)
    units = _discover_submission_units(hw_root, students_root)
    if not units:
        print("[WARN] No submission units discovered (check your homework folder for files/folders).", file=sys.stderr)

    # Emit anonymization map
    anonym_csv = out_dir / "anonymization_map.csv"
    _write_anonymization_map(units, anonym_csv)

    # If no solution PDF, synthesize one with gpt-5 (plain text → simple PDF)
    sol_effective = sol_path if (sol_path and sol_path.exists()) else None
    if sol_effective is None:
        generated = synthesize_solution_if_missing(
            problem_pdf=ps_path if (ps_path and ps_path.exists()) else None,
            classinfo_text=classinfo_text,
            out_dir=out_dir,
            max_pages_problem=20,
        )
        if generated:
            sol_effective = generated
        else:
            print("[WARN] Proceeding without a solution (synthesis failed).", file=sys.stderr)

    # Warnings (still record for manifest)
    warnings: List[str] = []
    if ps_path and not ps_path.exists():
        warnings.append(f"Problem set not found at {ps_path}.")
    if not sol_path or not sol_path.exists():
        if sol_effective and sol_effective.exists():
            warnings.append(f"No author-provided solutions found; synthesized at {sol_effective}.")
        else:
            warnings.append("Solutions not found and synthesis failed; proceeding without solutions.")

    # Write manifest
    cfg = CLIConfig(
        submissions_dir=hw_root,
        problem_set_path=ps_path if ps_path.exists() else None,
        solutions_path=sol_effective if (sol_effective and sol_effective.exists()) else None,
        notes=args.notes,
        class_info_path=class_info_path if (classinfo_text and class_info_path) else None,
        max_async=max(1, int(args.max_async)),
        out_dir=out_dir
    )
    manifest_path = out_dir / "run_manifest.json"
    _write_run_manifest(cfg, units, manifest_path, warnings)

    # ---- Build agent (once) with or without ClassInfo ----
    agent = make_agent(classinfo_text)

    # ---- Async: grade all aliases with concurrency limit ----
    sem = asyncio.Semaphore(cfg.max_async)
    tasks = [
        _grade_one_alias(
            sem=sem,
            agent=agent,
            problem_pdf=cfg.problem_set_path,
            solution_pdf=cfg.solutions_path,  # may be synthesized one
            alias=u,
        )
        for u in units
    ]

    results = asyncio.run(_run_all_coroutines(tasks))

    # ---- Write pregrading_results.csv ----
    results_csv = out_dir / "pregrading_results.csv"
    with results_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["alias_id", "original_name", "major_count", "moderate_count", "agent_output"])
        for r in results:
            if isinstance(r, Exception):
                w.writerow(["UNKNOWN", "UNKNOWN", 0, 0, f"[ERROR] {type(r).__name__}: {r}"])
                continue
            alias_id, original_name, major, moderate, agent_out = r
            w.writerow([alias_id, original_name, major, moderate, agent_out])

    # ---- Summary ----
    print("\n=== Pre-grading Run Complete ===")
    print(f" Homework root   : {hw_root}")
    print(f" Problem set     : {ps_path} {'[FOUND]' if ps_path.exists() else '[MISSING]'}")
    if cfg.solutions_path:
        print(f" Solutions       : {cfg.solutions_path} [IN USE]")
    else:
        print(f" Solutions       : [MISSING/GENERATE FAILED]")
    print(f" Class info used : {bool(classinfo_text)}")
    print(f" Max async       : {cfg.max_async}")
    print(f" Output dir      : {out_dir}")
    print(f" Units discovered: {len(units)}")
    print("\nArtifacts:")
    print(f" - {anonym_csv}")
    print(f" - {manifest_path}")
    print(f" - {results_csv}")
    if cfg.solutions_path and cfg.solutions_path.parent.name == "generated":
        print(f" - Generated solution: {cfg.solutions_path} and {cfg.solutions_path.with_suffix('.txt')}")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
