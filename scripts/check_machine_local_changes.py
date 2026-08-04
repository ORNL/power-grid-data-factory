#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


DEFAULT_RISK_PATTERNS = {
    "generated_output_data": r"^data/outputs/",
    "generated_derived_data": r"^data/derived/",
    "generated_scratch_data": r"^data/scratch/",
    "external_dependency_tree": r"^external/",
    "local_build_dir": r"^build(?:/|$)|^build-[^/]+/",
    "local_install_dir": r"^install(?:/|$)",
    "julia_local_state": r"^julia/\.julia/|^\.julia_depot_",
    "machine_launcher_script": r"^scripts/.*(?:frontier|andes).+\.sh$",
    "machine_named_script": r"^scripts/.*(?:frontier|andes).*$",
}

SAFE_ALLOW_PATTERNS = [
    r"^docs/",
    r"^src/",
    r"^configs/",
    r"^tests/",
    r"^scripts/(?!.*(?:frontier|andes)).+\.py$",
    r"^README\.md$",
    r"^pyproject\.toml$",
    r"^setup\.py$",
    r"^requirements.*\.txt$",
    r"^\.gitignore$",
]


@dataclass
class PathStatus:
    code: str
    path: str


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Guardrail check for machine-local or generated files before commit.")
    scope = p.add_mutually_exclusive_group()
    scope.add_argument("--staged", action="store_true", help="Check staged files only (default).")
    scope.add_argument("--all", action="store_true", help="Check both staged and unstaged changes.")
    p.add_argument("--warn-only", action="store_true", help="Always exit 0; print warnings only.")
    p.add_argument("--json", action="store_true", help="Print machine-readable JSON output.")
    p.add_argument("--root", default=".", help="Repository root path.")
    return p.parse_args()


def _run_git(repo: Path, args: list[str]) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "git command failed")
    return proc.stdout


def _collect_paths(repo: Path, all_changes: bool) -> list[PathStatus]:
    if all_changes:
        out = _run_git(repo, ["status", "--porcelain"])
        lines = out.splitlines()
    else:
        out = _run_git(repo, ["diff", "--cached", "--name-status"])
        lines = out.splitlines()

    rows: list[PathStatus] = []
    for line in lines:
        if not line.strip():
            continue
        if all_changes:
            code = line[:2].strip() or "??"
            path = line[3:]
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
        else:
            parts = line.split("\t")
            code = parts[0].strip()
            path = parts[-1].strip()
        rows.append(PathStatus(code=code, path=path.strip()))
    return rows


def _matches_any(path: str, patterns: Iterable[str]) -> bool:
    return any(re.search(p, path) for p in patterns)


def classify(paths: list[PathStatus]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for row in paths:
        p = row.path
        if _matches_any(p, SAFE_ALLOW_PATTERNS):
            continue
        for reason, pattern in DEFAULT_RISK_PATTERNS.items():
            if re.search(pattern, p):
                findings.append({"path": p, "status": row.code, "reason": reason})
                break
    return findings


def main() -> None:
    args = parse_args()
    repo = Path(args.root).resolve()
    all_changes = bool(args.all)

    try:
        paths = _collect_paths(repo, all_changes=all_changes)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        raise SystemExit(2)

    findings = classify(paths)
    payload = {
        "ok": len(findings) == 0,
        "mode": "all" if all_changes else "staged",
        "checked_count": len(paths),
        "finding_count": len(findings),
        "findings": findings,
        "note": "No risky machine-local/generated paths detected." if not findings else "Risky machine-local/generated paths detected.",
    }

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"check_mode={payload['mode']}")
        print(f"checked_count={payload['checked_count']}")
        print(f"finding_count={payload['finding_count']}")
        if findings:
            print("risky_paths:")
            for item in findings:
                print(f"- {item['path']} [{item['status']}] reason={item['reason']}")
        else:
            print("no risky paths found")

    if findings and not args.warn_only:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
