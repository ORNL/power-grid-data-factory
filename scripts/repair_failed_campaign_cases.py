#!/usr/bin/env python3
"""Prepare and merge selective or all-failure AC-OPF campaign repairs.

The prepare phase extracts unsuccessful records for selected cases from completed
map/reduce rounds. The merge phase accepts only successful retry records and
atomically replaces their original sample rows and shard-report status fields.
Retries are expected to run in an isolated runs tree.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

DEFAULT_CASES = ("activsg2000", "pglib_opf_case300_ieee")


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".in_progress")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    _atomic_write_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def _parse_rounds(value: str) -> list[int]:
    rounds: set[int] = set()
    for token in value.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            start, end = (int(item) for item in token.split("-", 1))
            if end < start:
                raise ValueError(f"invalid descending round range: {token}")
            rounds.update(range(start, end + 1))
        else:
            rounds.add(int(token))
    if not rounds:
        raise ValueError("at least one round is required")
    return sorted(rounds)


def _repair_key(source_relpath: str, candidate_id: str, run_id: str) -> str:
    value = json.dumps([source_relpath, candidate_id, run_id], separators=(",", ":"))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _report_relpath(campaign_id: str, round_index: int) -> str:
    return (
        f"data/outputs/campaigns/{campaign_id}/round_summaries/"
        f"round_{round_index:03d}_ac_execution_report.json"
    )


def prepare(
    repo_root: Path,
    runs_root: Path,
    rounds: list[int],
    cases: set[str] | None,
    output: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    if output.exists():
        output.unlink()
    output.parent.mkdir(parents=True, exist_ok=True)
    counts = {case_id: 0 for case_id in sorted(cases or set())}
    source_files = 0
    malformed_rows = 0
    extracted = 0

    with output.open("w", encoding="utf-8") as destination:
        for round_index in rounds:
            pattern = f"mapreduce_round_{round_index:03d}/shard_*/ac_opf/samples.jsonl"
            for source in sorted(runs_root.glob(pattern)):
                source_files += 1
                source_relpath = str(source.relative_to(repo_root))
                shard_name = source.parents[1].name
                shard_id = shard_name.removeprefix("shard_")
                campaign_id = f"ultrascale_3b__r{round_index:03d}__s{shard_id}"
                report_relpath = _report_relpath(campaign_id, round_index)
                with source.open(encoding="utf-8") as handle:
                    for raw_line in handle:
                        try:
                            record = json.loads(raw_line)
                        except json.JSONDecodeError:
                            malformed_rows += 1
                            continue
                        case_id = str(record.get("case_id", ""))
                        if (cases is not None and case_id not in cases) or bool(record.get("success", False)):
                            continue
                        candidate = dict((record.get("inputs") or {}).get("candidate") or {})
                        candidate_id = str(record.get("candidate_id", candidate.get("candidate_id", "")))
                        run_id = str(record.get("run_id", ""))
                        if not candidate_id or not run_id or not candidate:
                            malformed_rows += 1
                            continue
                        key = _repair_key(source_relpath, candidate_id, run_id)
                        candidate.update(
                            {
                                "candidate_id": f"{candidate_id}::repair::{key[:16]}",
                                "_repair_key": key,
                                "_repair_original_candidate_id": candidate_id,
                                "_repair_source_samples_relpath": source_relpath,
                                "_repair_source_report_relpath": report_relpath,
                                "_repair_source_round": round_index,
                                "_repair_original_run_id": run_id,
                                "_repair_original_status": str(record.get("termination_status", "unknown")),
                            }
                        )
                        destination.write(json.dumps(candidate, separators=(",", ":")) + "\n")
                        counts[case_id] = counts.get(case_id, 0) + 1
                        extracted += 1
        destination.flush()
        os.fsync(destination.fileno())

    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": "prepare",
        "runs_root": str(runs_root),
        "rounds": rounds,
        "cases": sorted(cases) if cases is not None else None,
        "all_cases": cases is None,
        "source_files": source_files,
        "extracted_failures": extracted,
        "counts_by_case": counts,
        "malformed_rows_skipped": malformed_rows,
        "candidates_jsonl": str(output),
    }
    _atomic_write_json(manifest_path, manifest)
    return manifest


class _HandleCache:
    def __init__(self, limit: int = 128) -> None:
        self.limit = limit
        self.handles: OrderedDict[Path, TextIO] = OrderedDict()

    def write(self, path: Path, line: str) -> None:
        handle = self.handles.pop(path, None)
        if handle is None:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = path.open("a", encoding="utf-8")
        self.handles[path] = handle
        handle.write(line)
        if len(self.handles) > self.limit:
            _, oldest = self.handles.popitem(last=False)
            oldest.close()

    def close(self) -> None:
        for handle in self.handles.values():
            handle.close()
        self.handles.clear()


def _spool_successes(repo_root: Path, repair_runs_root: Path, spool_root: Path) -> dict[str, int]:
    if spool_root.exists():
        shutil.rmtree(spool_root)
    cache = _HandleCache()
    counts = {"retry_rows": 0, "converged_retries": 0, "invalid_retry_rows": 0}
    try:
        for retry_path in sorted(repair_runs_root.glob("shard_*/ac_opf/samples.jsonl")):
            with retry_path.open(encoding="utf-8") as handle:
                for raw_line in handle:
                    counts["retry_rows"] += 1
                    try:
                        record = json.loads(raw_line)
                    except json.JSONDecodeError:
                        counts["invalid_retry_rows"] += 1
                        continue
                    if not bool(record.get("success", False)):
                        continue
                    candidate = (record.get("inputs") or {}).get("candidate") or {}
                    source_relpath = str(candidate.get("_repair_source_samples_relpath", ""))
                    key = str(candidate.get("_repair_key", ""))
                    if not source_relpath or not key:
                        counts["invalid_retry_rows"] += 1
                        continue
                    spool_path = spool_root / f"{hashlib.sha256(source_relpath.encode()).hexdigest()}.jsonl"
                    cache.write(
                        spool_path,
                        json.dumps(
                            {"source_relpath": source_relpath, "repair_key": key, "record": record},
                            separators=(",", ":"),
                        )
                        + "\n",
                    )
                    counts["converged_retries"] += 1
    finally:
        cache.close()
    return counts


def _clean_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in candidate.items() if not key.startswith("_repair_")}


def _replacement_record(original: dict[str, Any], retry: dict[str, Any]) -> dict[str, Any]:
    replacement = dict(retry)
    retry_inputs = dict(retry.get("inputs") or {})
    original_inputs = dict(original.get("inputs") or {})
    retry_inputs["candidate"] = original_inputs.get("candidate", _clean_candidate(retry_inputs.get("candidate") or {}))
    replacement["inputs"] = retry_inputs
    replacement["candidate_id"] = original.get("candidate_id", retry.get("candidate_id"))
    replacement["run_id"] = original.get("run_id", retry.get("run_id"))
    replacement["solver_id"] = original.get("solver_id", retry.get("solver_id"))
    replacement["repair_provenance"] = {
        "repaired_at": datetime.now(timezone.utc).isoformat(),
        "previous_success": bool(original.get("success", False)),
        "previous_termination_status": original.get("termination_status"),
        "previous_feasibility_label": original.get("feasibility_label"),
        "repair_solver_id": retry.get("solver_id"),
    }
    return replacement


def _update_report(repo_root: Path, report_relpath: str, replacements: dict[tuple[str, str], dict[str, Any]]) -> int:
    report_path = repo_root / report_relpath
    if not report_path.exists():
        return 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    updated = 0
    for row in report.get("solved", []):
        key = (str(row.get("candidate_id", "")), str(row.get("run_id", "")))
        replacement = replacements.get(key)
        if replacement is None:
            continue
        row.update(
            {
                "success": True,
                "termination_status": replacement.get("termination_status"),
                "feasibility_label": replacement.get("feasibility_label", "feasible"),
                "objective": replacement.get("objective"),
                "runtime": replacement.get("solve_time"),
                "wallclock_seconds": replacement.get("wallclock_seconds"),
                "repaired": True,
            }
        )
        updated += 1
    if updated:
        _atomic_write_json(report_path, report)
    return updated


def _merge_spool_file(repo_root: Path, spool_path: Path, backup_root: Path) -> tuple[int, int]:
    retries: dict[str, dict[str, Any]] = {}
    source_relpath = ""
    with spool_path.open(encoding="utf-8") as handle:
        for line in handle:
            item = json.loads(line)
            source_relpath = str(item["source_relpath"])
            retries[str(item["repair_key"])] = item["record"]
    if not retries or not source_relpath:
        return 0, 0

    source = repo_root / source_relpath
    temporary = source.with_suffix(source.suffix + ".repair_in_progress")
    backup = backup_root / source_relpath
    backup.parent.mkdir(parents=True, exist_ok=True)
    replacements_for_report: dict[tuple[str, str], dict[str, Any]] = {}
    replaced = 0

    with source.open(encoding="utf-8") as input_handle, temporary.open("w", encoding="utf-8") as output_handle:
        for raw_line in input_handle:
            try:
                original = json.loads(raw_line)
            except json.JSONDecodeError:
                output_handle.write(raw_line)
                continue
            candidate_id = str(original.get("candidate_id", ""))
            run_id = str(original.get("run_id", ""))
            key = _repair_key(source_relpath, candidate_id, run_id)
            retry = retries.get(key)
            if retry is None or bool(original.get("success", False)):
                output_handle.write(raw_line)
                continue
            replacement = _replacement_record(original, retry)
            output_handle.write(json.dumps(replacement, separators=(",", ":")) + "\n")
            replacements_for_report[(candidate_id, run_id)] = replacement
            replaced += 1
        output_handle.flush()
        os.fsync(output_handle.fileno())

    if replaced == 0:
        temporary.unlink(missing_ok=True)
        return 0, 0
    if len(replacements_for_report) != len(retries):
        temporary.unlink(missing_ok=True)
        raise RuntimeError(
            f"matched {len(replacements_for_report)} of {len(retries)} converged retries in {source_relpath}"
        )
    if not backup.exists():
        shutil.copy2(source, backup)
    temporary.replace(source)

    retry_candidate = (next(iter(retries.values())).get("inputs") or {}).get("candidate") or {}
    report_relpath = str(retry_candidate.get("_repair_source_report_relpath", ""))
    report_updates = _update_report(repo_root, report_relpath, replacements_for_report) if report_relpath else 0
    return replaced, report_updates


def merge(
    repo_root: Path,
    repair_runs_root: Path,
    work_dir: Path,
    manifest_path: Path,
    apply: bool,
) -> dict[str, Any]:
    spool_root = work_dir / "merge_spool"
    backup_root = work_dir / "backups"
    counts = _spool_successes(repo_root, repair_runs_root, spool_root)
    source_files = sorted(spool_root.glob("*.jsonl"))
    counts["source_files_with_converged_retries"] = len(source_files)
    counts["rows_replaced"] = 0
    counts["report_rows_updated"] = 0
    if apply:
        for spool_path in source_files:
            replaced, report_updates = _merge_spool_file(repo_root, spool_path, backup_root)
            counts["rows_replaced"] += replaced
            counts["report_rows_updated"] += report_updates

    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": "merge",
        "applied": apply,
        "repair_runs_root": str(repair_runs_root),
        "backup_root": str(backup_root),
        **counts,
    }
    _atomic_write_json(manifest_path, manifest)
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_parser = subparsers.add_parser("prepare", help="Extract failed target-case candidates.")
    prepare_parser.add_argument("--runs-root", default="data/outputs/runs_3b")
    prepare_parser.add_argument("--rounds", default="0-5")
    case_group = prepare_parser.add_mutually_exclusive_group()
    case_group.add_argument("--cases", nargs="+")
    case_group.add_argument("--all-cases", action="store_true", help="Extract failures for every case.")
    prepare_parser.add_argument("--output", required=True)
    prepare_parser.add_argument("--manifest", required=True)

    merge_parser = subparsers.add_parser("merge", help="Merge only converged retry rows.")
    merge_parser.add_argument("--repair-runs-root", required=True)
    merge_parser.add_argument("--work-dir", required=True)
    merge_parser.add_argument("--manifest", required=True)
    merge_parser.add_argument("--apply", action="store_true", help="Apply atomic replacements; default is a dry run.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    if args.command == "prepare":
        runs_root = Path(args.runs_root)
        runs_root = runs_root if runs_root.is_absolute() else repo_root / runs_root
        result = prepare(
            repo_root,
            runs_root.resolve(),
            _parse_rounds(args.rounds),
            None if args.all_cases else set(args.cases or DEFAULT_CASES),
            (repo_root / args.output).resolve() if not Path(args.output).is_absolute() else Path(args.output),
            (repo_root / args.manifest).resolve() if not Path(args.manifest).is_absolute() else Path(args.manifest),
        )
    else:
        repair_runs_root = Path(args.repair_runs_root)
        work_dir = Path(args.work_dir)
        manifest = Path(args.manifest)
        result = merge(
            repo_root,
            (repo_root / repair_runs_root).resolve() if not repair_runs_root.is_absolute() else repair_runs_root,
            (repo_root / work_dir).resolve() if not work_dir.is_absolute() else work_dir,
            (repo_root / manifest).resolve() if not manifest.is_absolute() else manifest,
            args.apply,
        )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
