#!/usr/bin/env python3
# pregrade_cli.py
"""
Simple CLI for pre-grading pipeline (skeleton, path rules updated).
- Resolves ./Homeworks/<folder> unless the provided path already includes "Homeworks"
- Creates outputs under ./Homeworks/<folder>/outputs/run_<timestamp>/
- Builds anonymization map (001, 002, …)
- Emits a run manifest JSON for downstream agents
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple


# --------------------------- Data Models ---------------------------

@dataclass
class CLIConfig:
    submissions_dir: Path
    problem_set_path: Optional[Path]
    solutions_path: Optional[Path]
    notes: Optional[str]
    class_info_path: Path
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
    # <hw_root>/materials/
    return hw_root / "materials"

def _resolve_homeworks_root(folder_arg: str) -> Path:
    """
    If the provided path already includes 'Homeworks' in any component, use as-is.
    Otherwise, prefix with './Homeworks/'.
    """
    p = Path(folder_arg).expanduser()
    if "Homeworks" in p.parts:
        return p.resolve()
    return (Path("./Homeworks") / p).resolve()

def _resolve_defaults(hw_root: Path,
                      problem_set: Optional[str],
                      solutions: Optional[str]) -> Tuple[Optional[Path], Optional[Path]]:
    ps = Path(problem_set).expanduser() if problem_set else (_default_materials_base(hw_root) / "problems.pdf")
    sol = Path(solutions).expanduser() if solutions else (_default_materials_base(hw_root) / "solutions.pdf")
    return ps, sol

def _is_hidden(p: Path) -> bool:
    return p.name.startswith(".") or p.name.startswith("__")

def _discover_submission_units(submissions_dir: Path, students_root: Path) -> List[SubmissionUnit]:
    """
    Treat each top-level file OR folder in submissions_dir as a submission unit.
    Skip internal dirs like 'materials' and 'outputs'.
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
            "class_info_path": str(cfg.class_info_path.resolve()),
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
            "Run Intake/Sanity to evaluate readability and parseability per alias.",
            "If solutions are missing/unreadable, schedule Solution Synthesizer Agent to produce solution.pdf/json.",
            "Launch up to `max_async` Worker Agents to pre-grade each alias against the solution checkpoints.",
            "Aggregate outputs into pregrading_results.csv and cohort_summary.*"
        ]
    }
    manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# --------------------------- CLI ---------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pregrade-cli",
        description="Simple CLI to set up a pre-grading run (inputs, defaults, anonymization, manifest).",
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
        help="Path to problem set PDF (default: <hw_root>/materials/problems.pdf)."
    )
    parser.add_argument(
        "--solutions",
        default=None,
        help="Path to solutions PDF (default: <hw_root>/materials/solutions.pdf)."
    )
    parser.add_argument(
        "--notes",
        default=None,
        help="Additional notes to pass to the grader/agents."
    )
    parser.add_argument(
        "--class",
        dest="class_info",
        default="./ClassInfo",
        help="Path to class information directory."
    )
    parser.add_argument(
        "--max-async",
        type=int,
        default=10,
        help="Max number of agents running concurrently (planning value; not used yet)."
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

    # Resolve the homework root per your rule
    hw_root = _resolve_homeworks_root(args.folder)
    if not hw_root.exists() or not hw_root.is_dir():
        print(f"[ERROR] Homework folder not found or not a directory: {hw_root}", file=sys.stderr)
        return 2

    # Defaults for materials based on the resolved root
    ps_path, sol_path = _resolve_defaults(hw_root, args.problem_set, args.solutions)

    class_info_path = Path(args.class_info).expanduser()
    if not class_info_path.exists():
        print(f"[WARN] Class info path does not exist yet: {class_info_path}", file=sys.stderr)

    # Output directory under <hw_root>/outputs/
    default_out = hw_root / "outputs" / f"run_{_timestamp()}"
    out_dir = Path(args.out).expanduser().resolve() if args.out else default_out.resolve()
    students_root = out_dir / "students"

    # Prepare directories
    _ensure_dir(out_dir)
    _ensure_dir(students_root)

    # Discover & anonymize (skip materials/outputs in this folder)
    units = _discover_submission_units(hw_root, students_root)
    if not units:
        print("[WARN] No submission units discovered (check your homework folder for files/folders).", file=sys.stderr)

    # Emit anonymization map
    anonym_csv = out_dir / "anonymization_map.csv"
    _write_anonymization_map(units, anonym_csv)

    # Collect warnings
    warnings: List[str] = []
    if ps_path and not ps_path.exists():
        warnings.append(f"Problem set not found at {ps_path}. (If omitted intentionally, this will need solution synthesis.)")
    if sol_path and not sol_path.exists():
        warnings.append(f"Solutions not found at {sol_path}. (Workers will need synthesized solution.)")

    # Write a skeletal run manifest
    cfg = CLIConfig(
        submissions_dir=hw_root,
        problem_set_path=ps_path if ps_path.exists() else None,
        solutions_path=sol_path if sol_path.exists() else None,
        notes=args.notes,
        class_info_path=class_info_path,
        max_async=max(1, int(args.max_async)),
        out_dir=out_dir
    )
    manifest_path = out_dir / "run_manifest.json"
    _write_run_manifest(cfg, units, manifest_path, warnings)

    # Friendly summary
    print("\n=== Pre-grading CLI Setup Complete ===")
    print(f" Homework root   : {hw_root}")
    print(f" Problem set     : {ps_path} {'[FOUND]' if ps_path.exists() else '[MISSING]'}")
    print(f" Solutions       : {sol_path} {'[FOUND]' if sol_path.exists() else '[MISSING]'}")
    print(f" Class info      : {class_info_path} {'[FOUND]' if class_info_path.exists() else '[MISSING]'}")
    print(f" Max async       : {cfg.max_async}")
    print(f" Output dir      : {out_dir}")
    print(f" Students root   : {students_root}")
    print(f" Units discovered: {len(units)}")
    if warnings:
        print("\n[Warnings]")
        for w in warnings:
            print(f" - {w}")
    print(f"\nArtifacts:")
    print(f" - {anonym_csv}")
    print(f" - {manifest_path}")
    print("\nNext: hook this manifest into your Agents SDK orchestrator to run intake/worker/aggregator passes.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
