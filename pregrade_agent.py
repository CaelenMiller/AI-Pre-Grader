#!/usr/bin/env python3
# grading_agent_pdf_image_min.py
"""
Very simple pre-grading agent for PDFs and images only.

- Ingests ClassInfo (txt/md) into the system prompt.
- Accepts: problem PDF (required), solution PDF (optional), submissions (list of PDF/PNG/JPG paths).
- For PDFs: extract text; if a page is image-only, render -> OCR (if pytesseract available).
- For images: OCR (if available); else mark unreadable for that image.
- Produces a concise analysis of "problem points" (no grades, no scores).

Dependencies:
  pip install PyMuPDF PyPDF2 pillow
  (optional for OCR) pip install pytesseract
  (and have Tesseract installed on your system for best results)

Environment:
  OPENAI_API_KEY must be available (the same way your Agents SDK expects it).

This is intentionally minimal and synchronous.
"""

from __future__ import annotations
import os
from pathlib import Path
from typing import List, Optional, Tuple
import io

# --- OpenAI Agents SDK ---
from agents import Agent, Runner  # type: ignore

# --- PDF & image handling ---
import fitz  # PyMuPDF (robust text + rasterization)
from PIL import Image

# --- Optional OCR (graceful fallback if unavailable) ---
try:
    import pytesseract
    OCR_AVAILABLE = True
except Exception:
    OCR_AVAILABLE = False


# ===================== Utilities =====================

def read_classinfo_text(classinfo_dir: Path) -> str:
    """Concatenate *.txt and *.md files (non-recursive) from ClassInfo."""
    if not classinfo_dir.exists() or not classinfo_dir.is_dir():
        return "[INFO] ClassInfo not found or not a directory."
    parts: List[str] = []
    for p in sorted(classinfo_dir.iterdir(), key=lambda x: x.name.lower()):
        if p.is_file() and p.suffix.lower() in (".txt", ".md"):
            try:
                parts.append(f"\n--- {p.name} ---\n{p.read_text(encoding='utf-8', errors='ignore')}\n")
            except Exception as e:
                parts.append(f"\n--- {p.name} ---\n[WARN] Failed to read: {e}\n")
    return "".join(parts) if parts else "[INFO] No .txt/.md files in ClassInfo."


def _ocr_image_pil(img: Image.Image) -> str:
    if not OCR_AVAILABLE:
        return "[WARN] OCR not available; install pytesseract and Tesseract."
    try:
        # Ensure reasonable DPI for OCR; PIL images often have no dpi metadata.
        return pytesseract.image_to_string(img)
    except Exception as e:
        return f"[WARN] OCR failed: {e}"


def _render_pdf_page_to_image(page: fitz.Page, dpi: int = 200) -> Image.Image:
    """Render a PDF page to a PIL Image at given DPI."""
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)  # no alpha for OCR
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    return img


def extract_pdf_text_with_ocr_fallback(pdf_path: Path,
                                       max_pages: Optional[int] = None,
                                       dpi: int = 200) -> Tuple[str, int, int]:
    """
    Extract text from a PDF. For pages with no text, rasterize and OCR (if available).
    Returns: (combined_text, total_pages, ocr_pages_used)
    """
    doc = fitz.open(pdf_path)
    total = len(doc)
    text_parts: List[str] = []
    ocr_count = 0

    limit = min(total, max_pages) if max_pages is not None else total
    for i in range(limit):
        page = doc[i]
        txt = (page.get_text("text") or "").strip()
        if len(txt) < 10:  # treat as image-only / too little text
            # Try OCR fallback
            img = _render_pdf_page_to_image(page, dpi=dpi)
            ocr_txt = _ocr_image_pil(img)
            if ocr_txt.strip() and not ocr_txt.startswith("[WARN] OCR not available"):
                ocr_count += 1
                txt = ocr_txt
            else:
                txt = "[UNREADABLE_PAGE] No extractable text; OCR unavailable or failed."
        text_parts.append(f"\n--- PAGE {i+1}/{total} ---\n{txt}\n")

    doc.close()
    return "".join(text_parts), total, ocr_count


def extract_image_text(img_path: Path) -> str:
    """OCR a single PNG/JPG to text; fallback to unreadable note if OCR not available."""
    try:
        img = Image.open(img_path).convert("RGB")
    except Exception as e:
        return f"[ERROR] Failed to open image: {img_path.name}: {e}"
    ocr_txt = _ocr_image_pil(img)
    if not ocr_txt.strip() or ocr_txt.startswith("[WARN] OCR not available"):
        return f"[UNREADABLE_IMAGE] {img_path.name}: OCR unavailable or produced no text."
    return ocr_txt


# ===================== Agent =====================

def make_simple_grading_agent(classinfo_text: str) -> Agent:
    SYSTEM = f"""
You are a math-focused pre-grading assistant. Do NOT assign grades or points.
Your job: identify likely errors, gaps in understanding, missing steps, and misapplied theorems.
Be concise and concrete. Prefer math-specific tags (e.g., chain_rule_misuse, missing_justification, units_mismatch).
Classify errors in terms of severity (major, moderate, minor), where major implies completely wrong approach/understanding, moderate implies significant mistake, and minor minor mistakes. 
Only assess errors associated with the assigned problems. For example, if a problem does not require an explanation, then do not claim that the lack of one is an error.
Do not be excessively nitpicky, unless rigor is demanded and the problem requires it. For example, if numeric tests are not required, do not mention them

Class information (for expectations and tone):
{classinfo_text}

Rules:
- If the student's material is unreadable (or mostly unreadable), say so and STOP (no analysis).
- Otherwise, list problem points as concise bullets with brief evidence (page/section hints).
- If solution is missing or mismatched, state the limitation.
- Keep output ≤ 300 words. No grades, no percentages.
""".strip()

    return Agent(
        name="PreGraderPDFImage",
        instructions=SYSTEM,
        model="gpt-5-mini"  # keep small; can switch to "gpt-5" for tougher math
    )


def run_pregrade_pdf_image(
    classinfo_dir: Path,
    problem_pdf: Path,
    submission_paths: List[Path],
    solution_pdf: Optional[Path] = None,
    *,
    max_pages_problem: Optional[int] = 15,
    max_pages_submission: Optional[int] = 25,
    max_pages_solution: Optional[int] = 15,
) -> str:
    """
    One-shot run:
    - Read ClassInfo
    - Extract problem text (PDF)
    - Extract solution text (PDF or "[INFO] missing")
    - For each submission file (PDF or image): extract/ocr to text
    - Ask the model for problem points (no grades)
    """
    classinfo_text = read_classinfo_text(classinfo_dir)

    if not problem_pdf.exists():
        raise FileNotFoundError(f"Problem PDF not found: {problem_pdf}")

    prob_text, prob_pages, prob_ocr = extract_pdf_text_with_ocr_fallback(problem_pdf, max_pages=max_pages_problem)

    if solution_pdf and solution_pdf.exists():
        sol_text, sol_pages, sol_ocr = extract_pdf_text_with_ocr_fallback(solution_pdf, max_pages=max_pages_solution)
    else:
        sol_text, sol_pages, sol_ocr = "[INFO] No solution PDF provided.", 0, 0

    subs_blobs: List[str] = []
    for p in submission_paths:
        if not p.exists():
            subs_blobs.append(f"\n=== SUBMISSION: {p.name} ===\n[ERROR] File does not exist.\n")
            continue

        if p.suffix.lower() in (".pdf",):
            txt, pages, ocr_used = extract_pdf_text_with_ocr_fallback(p, max_pages=max_pages_submission)
            subs_blobs.append(f"\n=== SUBMISSION (PDF): {p.name} ===\n[meta] pages={pages}, ocr_pages={ocr_used}\n{txt}\n")
        elif p.suffix.lower() in (".png", ".jpg", ".jpeg"):
            txt = extract_image_text(p)
            subs_blobs.append(f"\n=== SUBMISSION (IMAGE): {p.name} ===\n{txt}\n")
        else:
            subs_blobs.append(f"\n=== SUBMISSION: {p.name} ===\n[UNSUPPORTED] Only PDF/PNG/JPG are handled in this minimal agent.\n")

    USER = f"""
Problem (PDF → text; pages={prob_pages}, ocr_pages={prob_ocr}):
{prob_text}

Solution (PDF → text; pages={sol_pages}, ocr_pages={sol_ocr}):
{sol_text}

Student submission(s) (to analyze for problem points):
{''.join(subs_blobs)}

Task:
Identify likely problem points in the student's work. Do not grade. If unreadable, say so and stop.
""".strip()

    agent = make_simple_grading_agent(classinfo_text)
    result = Runner.run_sync(agent, USER)
    return str(getattr(result, "output_text", result))


# ===================== Minimal Test Harness =====================

if __name__ == "__main__":
    """
    Adjust these paths to your local structure.

    Example layout:
    ./ClassInfo/
      Expectations.md
    ./Homeworks/Sample Homework 1/
      Materials/
        Problems.pdf
        Solution.pdf
      Assignments/
        student1.pdf
        student2.png
    """
    classinfo_dir = Path("./ClassInfo")
    hw_root = Path("./Homeworks/Homework 11")
    problem_pdf = hw_root / "Materials" / "problems.pdf"
    solution_pdf = hw_root / "Materials" / "solutions.pdf"  # optional

    submissions = [
        hw_root / "Boyack-Ben.(55944).homework-11.svd-image-compression_1.20250917T081719.[111246] (1).pdf",
    ]

    print("[Debug] OCR available:", OCR_AVAILABLE)
    print("[Debug] Problem:", problem_pdf.resolve(), problem_pdf.exists())
    print("[Debug] Solution:", solution_pdf.resolve(), solution_pdf.exists())
    for s in submissions:
        print("[Debug] Sub:", s.resolve(), s.exists())

    try:
        out = run_pregrade_pdf_image(
            classinfo_dir=classinfo_dir,
            problem_pdf=problem_pdf,
            submission_paths=[p for p in submissions if p.exists()],
            solution_pdf=solution_pdf if solution_pdf.exists() else None,
            max_pages_problem=12,
            max_pages_submission=20,
            max_pages_solution=12,
        )
        print("\n=== PRE-GRADER OUTPUT ===\n")
        print(out)
        print("\n=========================\n")
    except Exception as e:
        print(f"[ERROR] {e}")
