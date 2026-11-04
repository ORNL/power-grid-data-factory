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
SELECT_MODES = ("failed", "legacy-quadratic")
LEGACY_QUADRATIC_REASON = "cost_convention_quadratic_resolve"


def _is_legacy_quadratic(record: dict[str, Any]) -> bool:
    """PowerModels AC-OPF record solved before the per-unit cost fix with c2 != 0."""
    result = record.get("result")
    if record.get("task") != "ac_opf" or not isinstance(result, dict):
        return False
    if result.get("solver_name") != "powermodels" or result.get("cost_convention"):
        return False
    generators = ((record.get("inputs") or {}).get("resolved_case") or {}).get("generators") or []
    return any(
        int(gen.get("status", 1)) != 0 and float((gen.get("cost") or [0.0])[0]) != 0.0 for gen in generators
    )


def _selected(record: dict[str, Any], select: str) -> bool:
    if select == "legacy-quadratic":
        return _is_legacy_quadratic(record)
    return not bool(record.get("success", False))


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


def _source_files(runs_root: Path, rounds: list[int], shard_count: int = 8192) -> list[Path]:
    # Deterministic construction avoids Lustre directory-enumeration stalls: a
    # wildcard glob() here must readdir every shard subdirectory (tens of
    # thousands of entries), which has been observed to hang for hours.
    sources: list[Path] = []
    for round_index in rounds:
        round_dir = runs_root / f"mapreduce_round_{round_index:03d}"
        for shard_index in range(shard_count):
            sources.append(round_dir / f"shard_{shard_index:05d}" / "ac_opf" / "samples.jsonl")
    return sources


def _parse_source_line(line: str) -> tuple[Path, str | None]:
    # Optional "samples<TAB>report" form; an empty report means the root has none.
    samples, tab, report = line.partition("\t")
    return Path(samples.strip()), (report.strip() if tab else None)


def _source_metadata(repo_root: Path, source: Path, report: str | None) -> tuple[int, str]:
    round_name = source.parents[2].name
    round_index = int(round_name.removeprefix("mapreduce_round_")) if round_name.startswith("mapreduce_round_") else 0
    if report is not None:
        report_path = Path(report)
        if report and report_path.is_absolute():
            report = str(report_path.relative_to(repo_root))
        return round_index, report
    shard_id = source.parents[1].name.removeprefix("shard_")
    return round_index, _report_relpath(f"ultrascale_3b__r{round_index:03d}__s{shard_id}", round_index)


def _extract_sources(
    repo_root: Path,
    sources: list[Path | tuple[Path, str | None]],
    cases: set[str] | None,
    destination: TextIO,
    select: str = "failed",
) -> dict[str, Any]:
    counts = {case_id: 0 for case_id in sorted(cases or set())}
    malformed_rows = 0
    extracted = 0
    processed_sources = 0
    missing_sources = 0

    for item in sources:
        source, report = item if isinstance(item, tuple) else (item, None)
        source_relpath = str(source.relative_to(repo_root))
        round_index, report_relpath = _source_metadata(repo_root, source, report)
        # Deterministic source lists are not existence-checked up front, so a
        # missing shard (e.g. one that never produced ac_opf output) is expected.
        try:
            handle = source.open(encoding="utf-8")
        except FileNotFoundError:
            missing_sources += 1
            continue
        processed_sources += 1
        with handle:
            for raw_line in handle:
                try:
                    record = json.loads(raw_line)
                except json.JSONDecodeError:
                    malformed_rows += 1
                    continue
                case_id = str(record.get("case_id", ""))
                if (cases is not None and case_id not in cases) or not _selected(record, select):
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
                        "_repair_select": select,
                    }
                )
                # A legacy warm start would come from the wrong-cost solution and
                # its byte offsets may be stale after the linear backfill.
                if select == "legacy-quadratic":
                    candidate.pop("warm_start_source", None)
                destination.write(json.dumps(candidate, separators=(",", ":")) + "\n")
                counts[case_id] = counts.get(case_id, 0) + 1
                extracted += 1

    return {
        "source_files": processed_sources,
        "missing_sources": missing_sources,
        "extracted_failures": extracted,
        "counts_by_case": counts,
        "malformed_rows_skipped": malformed_rows,
    }


def prepare(
    repo_root: Path,
    runs_root: Path,
    rounds: list[int],
    cases: set[str] | None,
    output: Path,
    manifest_path: Path,
    shard_count: int = 8192,
    select: str = "failed",
) -> dict[str, Any]:
    if output.exists():
        output.unlink()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as destination:
        counts = _extract_sources(
            repo_root, _source_files(runs_root, rounds, shard_count), cases, destination, select
        )
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
        "select": select,
        **counts,
        "candidates_jsonl": str(output),
    }
    _atomic_write_json(manifest_path, manifest)
    return manifest


def write_source_list(runs_root: Path, rounds: list[int], output: Path, shard_count: int = 8192) -> dict[str, Any]:
    sources = _source_files(runs_root, rounds, shard_count)
    _atomic_write_text(output, "".join(f"{source}\n" for source in sources))
    return {"source_files": len(sources), "source_list": str(output), "rounds": rounds}


def prepare_part(
    repo_root: Path,
    source_list: Path,
    part_index: int,
    part_count: int,
    cases: set[str] | None,
    output: Path,
    manifest_path: Path,
    select: str = "failed",
) -> dict[str, Any]:
    if part_count <= 0 or not 0 <= part_index < part_count:
        raise ValueError("part index must satisfy 0 <= part_index < part_count")
    sources = [_parse_source_line(line) for line in source_list.read_text(encoding="utf-8").splitlines() if line.strip()]
    start = len(sources) * part_index // part_count
    end = len(sources) * (part_index + 1) // part_count
    temporary = output.with_suffix(output.suffix + ".in_progress")
    output.parent.mkdir(parents=True, exist_ok=True)
    with temporary.open("w", encoding="utf-8") as destination:
        counts = _extract_sources(repo_root, sources[start:end], cases, destination, select)
        destination.flush()
        os.fsync(destination.fileno())
    temporary.replace(output)
    manifest = {
        "schema_version": 1,
        "mode": "prepare_part",
        "part_index": part_index,
        "part_count": part_count,
        "source_start": start,
        "source_end": end,
        "total_source_files": len(sources),
        "cases": sorted(cases) if cases is not None else None,
        "select": select,
        **counts,
        "candidates_jsonl": str(output),
    }
    _atomic_write_json(manifest_path, manifest)
    return manifest


def finalize_prepare(
    parts_dir: Path,
    part_count: int,
    runs_root: Path,
    rounds: list[int],
    cases: set[str] | None,
    output: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    totals: dict[str, Any] = {
        "source_files": 0,
        "extracted_failures": 0,
        "counts_by_case": {case_id: 0 for case_id in sorted(cases or set())},
        "malformed_rows_skipped": 0,
    }
    expected_start = 0
    total_source_files: int | None = None
    temporary = output.with_suffix(output.suffix + ".in_progress")
    output.parent.mkdir(parents=True, exist_ok=True)
    with temporary.open("wb") as destination:
        for part_index in range(part_count):
            part_output = parts_dir / f"part_{part_index:05d}.jsonl"
            part_manifest = parts_dir / f"part_{part_index:05d}.json"
            if not part_output.exists() or not part_manifest.exists():
                raise RuntimeError(f"missing prepare part {part_index}")
            item = json.loads(part_manifest.read_text(encoding="utf-8"))
            if item.get("part_index") != part_index or item.get("part_count") != part_count:
                raise RuntimeError(f"invalid prepare manifest for part {part_index}")
            if item.get("source_start") != expected_start:
                raise RuntimeError(f"noncontiguous source range at part {part_index}")
            expected_start = int(item["source_end"])
            if total_source_files is None:
                total_source_files = int(item["total_source_files"])
            elif total_source_files != int(item["total_source_files"]):
                raise RuntimeError("prepare parts disagree on source-file count")
            with part_output.open("rb") as source:
                shutil.copyfileobj(source, destination, length=16 * 1024 * 1024)
            totals["source_files"] += int(item["source_files"])
            totals["extracted_failures"] += int(item["extracted_failures"])
            totals["malformed_rows_skipped"] += int(item["malformed_rows_skipped"])
            for case_id, count in item["counts_by_case"].items():
                totals["counts_by_case"][case_id] = totals["counts_by_case"].get(case_id, 0) + int(count)
        destination.flush()
        os.fsync(destination.fileno())
    if total_source_files is None or expected_start != total_source_files:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"prepare parts cover {expected_start} of {total_source_files} source files")
    temporary.replace(output)
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": "prepare_parallel",
        "runs_root": str(runs_root),
        "rounds": rounds,
        "cases": sorted(cases) if cases is not None else None,
        "all_cases": cases is None,
        "parallel_parts": part_count,
        **totals,
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


def _spool_successes(
    repo_root: Path, repair_runs_root: Path, spool_root: Path, include_failed: bool = False
) -> dict[str, int]:
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
                    if not include_failed and not bool(record.get("success", False)):
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


def _replacement_record(original: dict[str, Any], retry: dict[str, Any], reason: str = "") -> dict[str, Any]:
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
    if reason:
        replacement["repair_provenance"]["reason"] = reason
        replacement["repair_provenance"]["previous_objective_legacy"] = original.get("objective")
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
        success = bool(replacement.get("success", False))
        row.update(
            {
                "success": success,
                "termination_status": replacement.get("termination_status"),
                "feasibility_label": replacement.get("feasibility_label", "feasible" if success else "error"),
                "objective": replacement.get("objective"),
                "runtime": replacement.get("solve_time"),
                "wallclock_seconds": replacement.get("wallclock_seconds"),
                "repaired": True,
            }
        )
        cost_convention = (replacement.get("result") or {}).get("cost_convention")
        if cost_convention:
            row["cost_convention"] = cost_convention
        updated += 1
    if updated:
        _atomic_write_json(report_path, report)
    return updated


def _merge_spool_file(
    repo_root: Path, spool_path: Path, backup_root: Path | None, select: str = "failed"
) -> tuple[int, int]:
    retries: dict[str, dict[str, Any]] = {}
    source_relpath = ""
    with spool_path.open(encoding="utf-8") as handle:
        for line in handle:
            item = json.loads(line)
            source_relpath = str(item["source_relpath"])
            retries[str(item["repair_key"])] = item["record"]
    if not retries or not source_relpath:
        return 0, 0
    return _apply_retries(repo_root, source_relpath, retries, backup_root, select)


def _apply_retries(
    repo_root: Path,
    source_relpath: str,
    retries: dict[str, dict[str, Any]],
    backup_root: Path | None,
    select: str = "failed",
) -> tuple[int, int]:
    source = repo_root / source_relpath
    temporary = source.with_suffix(source.suffix + ".repair_in_progress")
    backup = backup_root / source_relpath if backup_root is not None else None
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
            if retry is None or not _selected(original, select):
                output_handle.write(raw_line)
                continue
            reason = ""
            if select == "legacy-quadratic":
                reason = LEGACY_QUADRATIC_REASON
                retry_result = retry.get("result")
                if not isinstance(retry_result, dict) or not retry_result.get("cost_convention"):
                    # Python-side failures (timeouts, process errors) carry no
                    # Julia marker; a converged retry without it used old code.
                    if bool(retry.get("success", False)) or not isinstance(retry_result, dict):
                        output_handle.close()
                        temporary.unlink(missing_ok=True)
                        raise RuntimeError(f"retry for {candidate_id} in {source_relpath} lacks cost_convention")
                    retry = dict(retry)
                    retry["result"] = {**retry_result, "cost_convention": "per_unit_scaled_v2"}
            replacement = _replacement_record(original, retry, reason)
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
    if backup is not None and not backup.exists():
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, backup)

    # Report first: if killed before the swap, a rerun redoes both; after the swap the
    # rows are no longer selected, so a report left behind could never be refreshed.
    retry_candidate = (next(iter(retries.values())).get("inputs") or {}).get("candidate") or {}
    report_relpath = str(retry_candidate.get("_repair_source_report_relpath", ""))
    report_updates = _update_report(repo_root, report_relpath, replacements_for_report) if report_relpath else 0
    temporary.replace(source)
    return replaced, report_updates


def merge(
    repo_root: Path,
    repair_runs_root: Path,
    work_dir: Path,
    manifest_path: Path,
    apply: bool,
    select: str = "failed",
    backup: bool = True,
    part_index: int = 0,
    part_count: int = 1,
) -> dict[str, Any]:
    spool_root = work_dir / "merge_spool"
    backup_root = work_dir / "backups" if backup else None
    if part_count > 1:
        # Parallel apply reuses the spool written by a prior single-process dry run.
        if not 0 <= part_index < part_count or not spool_root.is_dir():
            raise RuntimeError("partitioned merge needs a valid part index and an existing merge_spool")
        counts: dict[str, int] = {}
        source_files = sorted(spool_root.glob("*.jsonl"))[part_index::part_count]
    else:
        # Legacy-quadratic originals are invalid regardless of outcome, so every
        # new-convention retry (converged or not) replaces its original.
        counts = _spool_successes(repo_root, repair_runs_root, spool_root, include_failed=select == "legacy-quadratic")
        source_files = sorted(spool_root.glob("*.jsonl"))
    counts["source_files_with_converged_retries"] = len(source_files)
    counts["rows_replaced"] = 0
    counts["report_rows_updated"] = 0
    if apply:
        for spool_path in source_files:
            replaced, report_updates = _merge_spool_file(repo_root, spool_path, backup_root, select)
            counts["rows_replaced"] += replaced
            counts["report_rows_updated"] += report_updates

    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": "merge",
        "select": select,
        "applied": apply,
        "part_index": part_index,
        "part_count": part_count,
        "repair_runs_root": str(repair_runs_root),
        "backup_root": str(backup_root) if backup_root is not None else None,
        **counts,
    }
    _atomic_write_json(manifest_path, manifest)
    return manifest


def _retry_files(repair_runs_root: Path) -> list[Path]:
    # One readdir of the runs root instead of a recursive glob (Lustre MDS load).
    names = sorted(name for name in os.listdir(repair_runs_root) if name.startswith("shard_"))
    return [repair_runs_root / name / "ac_opf" / "samples.jsonl" for name in names]


def _bucket(source_hash: str, part_count: int) -> int:
    return int(source_hash[:15], 16) % part_count


def build_merge_index(
    repair_runs_root: Path,
    work_dir: Path,
    part_index: int,
    part_count: int,
    select: str = "failed",
) -> dict[str, Any]:
    """Index one partition of retry files by byte offset, so the merge never copies records."""
    index_dir = work_dir / "merge_index"
    manifest_path = index_dir / f"part_{part_index:05d}.json"
    if manifest_path.exists():
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    include_failed = select == "legacy-quadratic"
    counts = {"retry_files": 0, "retry_rows": 0, "indexed": 0, "invalid_retry_rows": 0}
    lines: list[str] = []
    for retry_path in _retry_files(repair_runs_root)[part_index::part_count]:
        if not retry_path.exists():
            continue
        counts["retry_files"] += 1
        retry_relpath = str(retry_path.relative_to(repair_runs_root))
        offset = 0
        with retry_path.open("rb") as handle:
            for raw_line in handle:
                length = len(raw_line)
                start, offset = offset, offset + length
                counts["retry_rows"] += 1
                try:
                    record = json.loads(raw_line)
                except json.JSONDecodeError:
                    counts["invalid_retry_rows"] += 1
                    continue
                if not include_failed and not bool(record.get("success", False)):
                    continue
                candidate = (record.get("inputs") or {}).get("candidate") or {}
                source_relpath = str(candidate.get("_repair_source_samples_relpath", ""))
                key = str(candidate.get("_repair_key", ""))
                if not source_relpath or not key or "\t" in source_relpath:
                    counts["invalid_retry_rows"] += 1
                    continue
                source_hash = hashlib.sha256(source_relpath.encode()).hexdigest()
                lines.append(f"{source_hash}\t{source_relpath}\t{key}\t{retry_relpath}\t{start}\t{length}\n")
                counts["indexed"] += 1
    _atomic_write_text(index_dir / f"part_{part_index:05d}.tsv", "".join(lines))
    manifest = {"schema_version": 1, "mode": "merge_index", "select": select,
                "part_index": part_index, "part_count": part_count, **counts}
    _atomic_write_json(manifest_path, manifest)
    return manifest


def _index_parts(work_dir: Path) -> list[Path]:
    index_dir = work_dir / "merge_index"
    first = index_dir / "part_00000.json"
    if not first.exists():
        raise RuntimeError(f"merge index missing: {first}")
    count = int(json.loads(first.read_text(encoding="utf-8"))["part_count"])
    missing = [i for i in range(count) if not (index_dir / f"part_{i:05d}.json").exists()]
    if missing:
        raise RuntimeError(f"merge index incomplete: {len(missing)} of {count} parts missing")
    return [index_dir / f"part_{i:05d}.tsv" for i in range(count)]


def merge_from_index(
    repo_root: Path,
    repair_runs_root: Path,
    work_dir: Path,
    manifest_path: Path,
    select: str = "failed",
    backup: bool = True,
    part_index: int = 0,
    part_count: int = 1,
) -> dict[str, Any]:
    """Apply the retries of every source file whose path hashes to this part."""
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        if previous.get("applied") is True and previous.get("from_index") and previous.get("part_count") == part_count:
            return previous
    by_source: dict[str, list[tuple[str, str, int, int]]] = {}
    for index_path in _index_parts(work_dir):
        with index_path.open(encoding="utf-8") as handle:
            for line in handle:
                source_hash, source_relpath, key, retry_relpath, offset, length = line.rstrip("\n").split("\t")
                if _bucket(source_hash, part_count) == part_index:
                    by_source.setdefault(source_relpath, []).append((key, retry_relpath, int(offset), int(length)))
    backup_root = work_dir / "backups" if backup else None
    counts = {"source_files_with_converged_retries": len(by_source), "rows_replaced": 0, "report_rows_updated": 0}
    for source_relpath in sorted(by_source):
        retries: dict[str, dict[str, Any]] = {}
        for key, retry_relpath, offset, length in by_source[source_relpath]:
            with (repair_runs_root / retry_relpath).open("rb") as handle:
                handle.seek(offset)
                retries[key] = json.loads(handle.read(length))
        replaced, report_updates = _apply_retries(repo_root, source_relpath, retries, backup_root, select)
        counts["rows_replaced"] += replaced
        counts["report_rows_updated"] += report_updates
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": "merge",
        "from_index": True,
        "select": select,
        "applied": True,
        "part_index": part_index,
        "part_count": part_count,
        "repair_runs_root": str(repair_runs_root),
        "backup_root": str(backup_root) if backup_root is not None else None,
        **counts,
    }
    _atomic_write_json(manifest_path, manifest)
    return manifest


def merge_finalize(parts_dir: Path, part_count: int, manifest_path: Path) -> dict[str, Any]:
    """Write the merge manifest only when every index-based apply part is recorded as applied."""
    totals = {"source_files_with_converged_retries": 0, "rows_replaced": 0, "report_rows_updated": 0}
    missing = []
    for index in range(part_count):
        path = parts_dir / f"part_{index:05d}.json"
        part = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if not (part.get("applied") is True and part.get("from_index") and part.get("part_count") == part_count):
            missing.append(index)
            continue
        for key in totals:
            totals[key] += int(part.get(key, 0))
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": "merge_finalize",
        "complete": not missing,
        "part_count": part_count,
        "missing_parts": missing,
        **totals,
    }
    if not missing:
        _atomic_write_json(manifest_path, manifest)
    return manifest
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_parser = subparsers.add_parser("prepare", help="Extract failed target-case candidates.")
    prepare_parser.add_argument("--runs-root", default="data/outputs/runs_3b")
    prepare_parser.add_argument("--rounds", default="0-5")
    prepare_parser.add_argument("--shard-count", type=int, default=8192)
    case_group = prepare_parser.add_mutually_exclusive_group()
    case_group.add_argument("--cases", nargs="+")
    case_group.add_argument("--all-cases", action="store_true", help="Extract failures for every case.")
    prepare_parser.add_argument("--output", required=True)
    prepare_parser.add_argument("--manifest", required=True)
    prepare_parser.add_argument("--select", choices=SELECT_MODES, default="failed")

    source_parser = subparsers.add_parser("list-sources", help="Write the deterministic repair source-file list.")
    source_parser.add_argument("--runs-root", default="data/outputs/runs_3b")
    source_parser.add_argument("--rounds", default="0-5")
    source_parser.add_argument("--shard-count", type=int, default=8192)
    source_parser.add_argument("--output", required=True)

    part_parser = subparsers.add_parser("prepare-part", help="Extract one contiguous partition of repair sources.")
    part_parser.add_argument("--source-list", required=True)
    part_parser.add_argument("--part-index", type=int, required=True)
    part_parser.add_argument("--part-count", type=int, required=True)
    part_case_group = part_parser.add_mutually_exclusive_group()
    part_case_group.add_argument("--cases", nargs="+")
    part_case_group.add_argument("--all-cases", action="store_true")
    part_parser.add_argument("--output", required=True)
    part_parser.add_argument("--manifest", required=True)
    part_parser.add_argument("--select", choices=SELECT_MODES, default="failed")

    finalize_parser = subparsers.add_parser("finalize-prepare", help="Combine deterministic parallel prepare parts.")
    finalize_parser.add_argument("--parts-dir", required=True)
    finalize_parser.add_argument("--part-count", type=int, required=True)
    finalize_parser.add_argument("--runs-root", default="data/outputs/runs_3b")
    finalize_parser.add_argument("--rounds", default="0-5")
    finalize_case_group = finalize_parser.add_mutually_exclusive_group()
    finalize_case_group.add_argument("--cases", nargs="+")
    finalize_case_group.add_argument("--all-cases", action="store_true")
    finalize_parser.add_argument("--output", required=True)
    finalize_parser.add_argument("--manifest", required=True)

    merge_parser = subparsers.add_parser("merge", help="Merge only converged retry rows.")
    merge_parser.add_argument("--repair-runs-root", required=True)
    merge_parser.add_argument("--work-dir", required=True)
    merge_parser.add_argument("--manifest", required=True)
    merge_parser.add_argument("--apply", action="store_true", help="Apply atomic replacements; default is a dry run.")
    merge_parser.add_argument("--select", choices=SELECT_MODES, default="failed")
    merge_parser.add_argument("--no-backup", action="store_true", help="Do not copy originals before replacing them.")
    merge_parser.add_argument("--part-index", type=int, default=0)
    merge_parser.add_argument("--part-count", type=int, default=1)
    merge_parser.add_argument("--from-index", action="store_true", help="Apply from merge-index output (requires --apply).")

    index_parser = subparsers.add_parser("merge-index", help="Index one partition of retry files for --from-index.")
    index_parser.add_argument("--repair-runs-root", required=True)
    index_parser.add_argument("--work-dir", required=True)
    index_parser.add_argument("--select", choices=SELECT_MODES, default="failed")
    index_parser.add_argument("--part-index", type=int, required=True)
    index_parser.add_argument("--part-count", type=int, required=True)

    final_parser = subparsers.add_parser("merge-finalize", help="Verify all --from-index apply parts.")
    final_parser.add_argument("--parts-dir", required=True)
    final_parser.add_argument("--part-count", type=int, required=True)
    final_parser.add_argument("--manifest", required=True)
    return parser.parse_args()


def _abs(repo_root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (repo_root / path).resolve()


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    if args.command in {"prepare", "list-sources", "finalize-prepare"}:
        runs_root = Path(args.runs_root)
        runs_root = runs_root if runs_root.is_absolute() else repo_root / runs_root
    if args.command == "prepare":
        result = prepare(
            repo_root,
            runs_root.resolve(),
            _parse_rounds(args.rounds),
            None if args.all_cases else set(args.cases or DEFAULT_CASES),
            (repo_root / args.output).resolve() if not Path(args.output).is_absolute() else Path(args.output),
            (repo_root / args.manifest).resolve() if not Path(args.manifest).is_absolute() else Path(args.manifest),
            args.shard_count,
            args.select,
        )
    elif args.command == "list-sources":
        output = Path(args.output)
        result = write_source_list(
            runs_root.resolve(),
            _parse_rounds(args.rounds),
            (repo_root / output).resolve() if not output.is_absolute() else output,
            args.shard_count,
        )
    elif args.command == "prepare-part":
        source_list = Path(args.source_list)
        output = Path(args.output)
        manifest = Path(args.manifest)
        result = prepare_part(
            repo_root,
            (repo_root / source_list).resolve() if not source_list.is_absolute() else source_list,
            args.part_index,
            args.part_count,
            None if args.all_cases else set(args.cases or DEFAULT_CASES),
            (repo_root / output).resolve() if not output.is_absolute() else output,
            (repo_root / manifest).resolve() if not manifest.is_absolute() else manifest,
            args.select,
        )
    elif args.command == "finalize-prepare":
        parts_dir = Path(args.parts_dir)
        output = Path(args.output)
        manifest = Path(args.manifest)
        result = finalize_prepare(
            (repo_root / parts_dir).resolve() if not parts_dir.is_absolute() else parts_dir,
            args.part_count,
            runs_root.resolve(),
            _parse_rounds(args.rounds),
            None if args.all_cases else set(args.cases or DEFAULT_CASES),
            (repo_root / output).resolve() if not output.is_absolute() else output,
            (repo_root / manifest).resolve() if not manifest.is_absolute() else manifest,
        )
    elif args.command == "merge-index":
        result = build_merge_index(
            _abs(repo_root, args.repair_runs_root), _abs(repo_root, args.work_dir),
            args.part_index, args.part_count, args.select,
        )
    elif args.command == "merge-finalize":
        result = merge_finalize(_abs(repo_root, args.parts_dir), args.part_count, _abs(repo_root, args.manifest))
        if not result["complete"]:
            print(json.dumps(result, indent=2, sort_keys=True))
            raise SystemExit(1)
    elif args.from_index:
        if not args.apply:
            raise SystemExit("--from-index requires --apply")
        result = merge_from_index(
            repo_root, _abs(repo_root, args.repair_runs_root), _abs(repo_root, args.work_dir),
            _abs(repo_root, args.manifest), args.select, not args.no_backup, args.part_index, args.part_count,
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
            args.select,
            not args.no_backup,
            args.part_index,
            args.part_count,
        )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
