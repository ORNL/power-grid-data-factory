#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Select PowerModels results for primal warm-start retries."
    )
    parser.add_argument("--source-runs-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--termination-status",
        action="append",
        default=[],
        help="Source status to select; repeat as needed (default: ALMOST_LOCALLY_SOLVED).",
    )
    return parser.parse_args()


def _repo_relative(repo_root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(repo_root))
    except ValueError:
        return str(path.resolve())


def collect_restart_candidates(
    repo_root: Path,
    source_runs_root: Path,
    statuses: set[str],
) -> list[dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    needles = [f'"termination_status": "{status}"'.encode() for status in statuses]
    compact_needles = [f'"termination_status":"{status}"'.encode() for status in statuses]

    sample_paths = sorted(
        path for path in source_runs_root.rglob("samples.jsonl") if path.parent.name == "ac_opf"
    )
    for samples_path in sample_paths:
        with samples_path.open("rb") as source:
            while True:
                offset = source.tell()
                line = source.readline()
                if not line:
                    break
                if not any(needle in line for needle in (*needles, *compact_needles)):
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                status = str(record.get("termination_status", ""))
                if status not in statuses:
                    continue
                candidate = dict(((record.get("inputs") or {}).get("candidate") or {}))
                candidate_id = str(record.get("candidate_id", candidate.get("candidate_id", "")))
                solution = (((record.get("result") or {}).get("raw_result") or {}).get("solution"))
                if not candidate_id or not isinstance(solution, dict):
                    continue
                candidate["candidate_id"] = candidate_id
                candidate["warm_start_source"] = {
                    "samples_path": _repo_relative(repo_root, samples_path),
                    "byte_offset": offset,
                    "byte_length": len(line),
                    "termination_status": status,
                    "objective": record.get("objective"),
                    "solver_id": record.get("solver_id"),
                    "run_id": record.get("run_id"),
                }
                selected[candidate_id] = candidate
    return [selected[candidate_id] for candidate_id in sorted(selected)]


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    source_runs_root = Path(args.source_runs_root)
    if not source_runs_root.is_absolute():
        source_runs_root = (repo_root / source_runs_root).resolve()
    output = Path(args.output)
    if not output.is_absolute():
        output = (repo_root / output).resolve()
    statuses = set(args.termination_status or ["ALMOST_LOCALLY_SOLVED"])

    candidates = collect_restart_candidates(repo_root, source_runs_root, statuses)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp.{os.getpid()}")
    with temporary.open("w", encoding="utf-8") as destination:
        for candidate in candidates:
            destination.write(json.dumps(candidate) + "\n")
        destination.flush()
        os.fsync(destination.fileno())
    temporary.replace(output)
    print(json.dumps({
        "ok": True,
        "source_runs_root": str(source_runs_root),
        "output": str(output),
        "termination_statuses": sorted(statuses),
        "selected_count": len(candidates),
    }, indent=2))


if __name__ == "__main__":
    main()
