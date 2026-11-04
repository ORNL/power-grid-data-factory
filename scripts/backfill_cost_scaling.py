#!/usr/bin/env python3
"""Exactly rescale legacy linear-cost PowerModels AC-OPF records to per-unit costs.

Legacy records (no ``result.cost_convention``) were solved with MW-unit cost
coefficients applied to per-unit ``pg``. For cases whose active generators all
have ``c2 == 0`` the optimal dispatch is unchanged by the fix, so the corrected
objective, per-generator ``pg_cost`` and nodal duals follow exactly:

    pg_cost_new = c1 * baseMVA * pg + c0
    objective_new = sum(pg_cost_new over active generators)
    lam_kcl_{r,i}_new = baseMVA * lam_kcl_{r,i}

Before rewriting, the stored objective is checked against the legacy formula
``sum(c2 pg^2 + c1 pg + c0)``; mismatching records are left untouched.
Quadratic-cost records are left byte-identical (they need a re-solve, see
``repair_failed_campaign_cases.py --select legacy-quadratic``). ExaGO, PF and
already-converted records are never modified, so the script is idempotent.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

COST_CONVENTION = "per_unit_scaled_v2"
REL_TOL = 1e-6
ABS_TOL = 1e-6


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
        if "-" in token:
            start, end = (int(item) for item in token.split("-", 1))
            rounds.update(range(start, end + 1))
        elif token:
            rounds.add(int(token))
    if not rounds:
        raise ValueError("at least one round is required")
    return sorted(rounds)


def _active_generators(record: dict[str, Any]) -> list[tuple[str, list[float]]]:
    """Return (1-based solution key, [c2, c1, c0]) for active generators."""
    generators = ((record.get("inputs") or {}).get("resolved_case") or {}).get("generators") or []
    active: list[tuple[str, list[float]]] = []
    for index, generator in enumerate(generators, start=1):
        if int(generator.get("status", 1)) == 0:
            continue
        cost = [float(value) for value in (generator.get("cost") or [0.0, 1.0, 0.0])]
        while len(cost) < 3:
            cost.insert(0, 0.0)
        active.append((str(index), cost[-3:]))
    return active


def classify(record: dict[str, Any]) -> str:
    """Classify a record: skipped_v2, skipped_other, quadratic, or linear."""
    result = record.get("result")
    if record.get("task") != "ac_opf" or not isinstance(result, dict):
        return "skipped_other"
    if result.get("solver_name") != "powermodels":
        return "skipped_other"
    if result.get("cost_convention"):
        return "skipped_v2"
    if any(cost[0] != 0.0 for _, cost in _active_generators(record)):
        return "quadratic"
    return "linear"


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(ABS_TOL, REL_TOL * max(abs(a), abs(b)))


def _objectives(record: dict[str, Any]) -> tuple[float, float, float] | None:
    """(stored, legacy, per-unit) objectives recomputed from the stored dispatch."""
    result = record["result"]
    raw = result.get("raw_result")
    solution = raw.get("solution") if isinstance(raw, dict) else None
    gen_solution = solution.get("gen") if isinstance(solution, dict) else None
    if not isinstance(gen_solution, dict) or not gen_solution:
        return None
    stored = raw.get("objective", result.get("objective", record.get("objective")))
    if stored is None:
        return None
    base_mva = float(((record.get("inputs") or {}).get("resolved_case") or {}).get("base_mva") or 100.0)
    legacy = per_unit = 0.0
    for key, (c2, c1, c0) in _active_generators(record):
        entry = gen_solution.get(key)
        if not isinstance(entry, dict) or entry.get("pg") is None:
            continue
        pg = float(entry["pg"])
        legacy += c2 * pg * pg + c1 * pg + c0
        per_unit += c2 * base_mva * base_mva * pg * pg + c1 * base_mva * pg + c0
    return float(stored), legacy, per_unit


def mark_already_v2(record: dict[str, Any], rescaled_at: str, fallback: str) -> str:
    """Mark an unmarked record whose objective already uses per-unit costs; values untouched."""
    objectives = _objectives(record)
    if objectives is None:
        return fallback
    stored, legacy, per_unit = objectives
    # Ambiguous when both formulas agree; keep the legacy treatment then.
    if _close(legacy, stored) or not _close(per_unit, stored):
        return fallback
    result = record["result"]
    result["cost_convention"] = COST_CONVENTION
    result["cost_rescale"] = {
        "status": "already_v2",
        "verified_rel_err": abs(per_unit - stored) / max(abs(stored), 1e-300),
        "rescaled_at": rescaled_at,
    }
    return "already_v2"


def rescale_linear(record: dict[str, Any], rescaled_at: str) -> str:
    """Rescale one legacy linear-cost record in place; return its status."""
    result = record["result"]
    raw = result.get("raw_result")
    solution = raw.get("solution") if isinstance(raw, dict) else None
    gen_solution = solution.get("gen") if isinstance(solution, dict) else None
    base_mva = float(((record.get("inputs") or {}).get("resolved_case") or {}).get("base_mva") or 100.0)
    old_objective = record.get("objective")

    if not isinstance(gen_solution, dict) or not gen_solution:
        # No dispatch to rescale (failed solve); the stored objective is not a
        # cost value. Mark the convention so consumers treat it uniformly.
        result["cost_convention"] = COST_CONVENTION
        result["cost_rescale"] = {"status": "no_solution", "factor": base_mva, "rescaled_at": rescaled_at}
        return "no_solution"

    active = _active_generators(record)
    legacy = 0.0
    new_costs: dict[str, float] = {}
    # Generators absent from the solution (e.g. dropped on isolated buses)
    # contribute nothing to the stored objective; the check below enforces it.
    absent = 0
    for key, (c2, c1, c0) in active:
        entry = gen_solution.get(key)
        if not isinstance(entry, dict) or entry.get("pg") is None:
            absent += 1
            continue
        pg = float(entry["pg"])
        legacy += c2 * pg * pg + c1 * pg + c0
        new_costs[key] = c1 * base_mva * pg + c0
    stored = raw.get("objective", result.get("objective", old_objective))
    if stored is None or not _close(legacy, float(stored)):
        return mark_already_v2(record, rescaled_at, "unverified")
    rel_err = abs(legacy - float(stored)) / max(abs(float(stored)), 1e-300)

    new_objective = sum(new_costs.values())
    for key, value in new_costs.items():
        gen_solution[key]["pg_cost"] = value
    for bus in (solution.get("bus") or {}).values():
        if not isinstance(bus, dict):
            continue
        for field in ("lam_kcl_r", "lam_kcl_i"):
            if isinstance(bus.get(field), (int, float)):
                bus[field] = float(bus[field]) * base_mva
    raw["objective"] = new_objective
    result["objective"] = new_objective
    record["objective"] = new_objective
    result["cost_convention"] = COST_CONVENTION
    result["cost_rescale"] = {
        "status": "exact_linear",
        "factor": base_mva,
        "old_objective": old_objective,
        "verified_rel_err": rel_err,
        "absent_active_generators": absent,
        "rescaled_at": rescaled_at,
    }
    return "exact_linear"


def _update_report(report_path: Path, objectives: dict[tuple[str, str], Any]) -> int:
    if not objectives or not report_path.exists():
        return 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    updated = 0
    for row in report.get("solved", []):
        key = (str(row.get("candidate_id", "")), str(row.get("run_id", "")))
        if key in objectives:
            row["objective"] = objectives[key]
            row["cost_convention"] = COST_CONVENTION
            updated += 1
    if updated:
        _atomic_write_json(report_path, report)
    return updated


def process_file(samples: Path, report: Path | None, dry_run: bool) -> dict[str, Any]:
    """Stream-rewrite one samples file. Returns per-status and per-case counts."""
    counts: Counter[str] = Counter()
    by_case: Counter[str] = Counter()
    objectives: dict[tuple[str, str], Any] = {}
    rescaled_at = datetime.now(timezone.utc).isoformat()
    temporary = samples.with_suffix(samples.suffix + ".cost_rescale_in_progress")
    changed = 0
    try:
        source = samples.open(encoding="utf-8")
    except FileNotFoundError:
        return {"missing": 1, "counts": {}, "by_case": {}, "report_rows_updated": 0, "rewritten": 0}
    output = None if dry_run else temporary.open("w", encoding="utf-8")
    try:
        with source:
            for raw_line in source:
                try:
                    record = json.loads(raw_line)
                except json.JSONDecodeError:
                    counts["malformed"] += 1
                    if output:
                        output.write(raw_line)
                    continue
                kind = classify(record)
                status = kind
                if kind == "linear":
                    status = rescale_linear(record, rescaled_at)
                elif kind == "quadratic":
                    status = mark_already_v2(record, rescaled_at, "quadratic")
                counts[status] += 1
                by_case[f"{record.get('case_id', '')}|{status}"] += 1
                if status in {"exact_linear", "no_solution", "already_v2"}:
                    changed += 1
                    objectives[(str(record.get("candidate_id", "")), str(record.get("run_id", "")))] = record.get(
                        "objective"
                    )
                    if output:
                        output.write(json.dumps(record) + "\n")
                elif output:
                    output.write(raw_line)
        if output:
            output.flush()
            os.fsync(output.fileno())
            output.close()
            output = None
            if changed:
                temporary.replace(samples)
            else:
                temporary.unlink(missing_ok=True)
    finally:
        if output:
            output.close()
            temporary.unlink(missing_ok=True)
    report_rows = 0
    if changed and report is not None and not dry_run:
        report_rows = _update_report(report, objectives)
    return {
        "missing": 0,
        "counts": dict(counts),
        "by_case": dict(by_case),
        "report_rows_updated": report_rows,
        "rewritten": int(bool(changed) and not dry_run),
    }


def _report_path(repo_root: Path, campaign_prefix: str, round_index: int, shard: int) -> Path:
    campaign_id = f"{campaign_prefix}__r{round_index:03d}__s{shard:05d}"
    return (
        repo_root
        / "data/outputs/campaigns"
        / campaign_id
        / "round_summaries"
        / f"round_{round_index:03d}_ac_execution_report.json"
    )


def list_sources(
    repo_root: Path, runs_root: Path, rounds: list[int], shard_count: int, campaign_prefix: str
) -> list[str]:
    """Deterministic ``samples<TAB>report`` lines (report empty when no prefix)."""
    lines = []
    for round_index in rounds:
        for shard in range(shard_count):
            samples = runs_root / f"mapreduce_round_{round_index:03d}" / f"shard_{shard:05d}" / "ac_opf" / "samples.jsonl"
            report = _report_path(repo_root, campaign_prefix, round_index, shard) if campaign_prefix else ""
            lines.append(f"{samples}\t{report}")
    return lines


def _read_source_list(path: Path) -> list[tuple[Path, Path | None]]:
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        samples, _, report = line.partition("\t")
        items.append((Path(samples.strip()), Path(report.strip()) if report.strip() else None))
    return items


def run_part(source_list: Path, part_index: int, part_count: int, manifest: Path, dry_run: bool) -> dict[str, Any]:
    if part_count <= 0 or not 0 <= part_index < part_count:
        raise ValueError("part index must satisfy 0 <= part_index < part_count")
    sources = _read_source_list(source_list)
    start = len(sources) * part_index // part_count
    end = len(sources) * (part_index + 1) // part_count
    counts: Counter[str] = Counter()
    by_case: Counter[str] = Counter()
    totals: Counter[str] = Counter()
    for samples, report in sources[start:end]:
        item = process_file(samples, report, dry_run)
        counts.update(item["counts"])
        by_case.update(item["by_case"])
        totals["missing_files"] += item["missing"]
        totals["files_processed"] += 1 - item["missing"]
        totals["files_rewritten"] += item["rewritten"]
        totals["report_rows_updated"] += item["report_rows_updated"]
    value = {
        "schema_version": 1,
        "mode": "run_part",
        "dry_run": dry_run,
        "part_index": part_index,
        "part_count": part_count,
        "source_start": start,
        "source_end": end,
        "total_source_files": len(sources),
        **dict(totals),
        "counts": dict(counts),
        "by_case": dict(by_case),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    _atomic_write_json(manifest, value)
    return value


def finalize(parts_dir: Path, part_count: int, output: Path) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    by_case: Counter[str] = Counter()
    totals: Counter[str] = Counter()
    missing_parts = []
    for part_index in range(part_count):
        path = parts_dir / f"part_{part_index:05d}.json"
        if not path.exists():
            missing_parts.append(part_index)
            continue
        item = json.loads(path.read_text(encoding="utf-8"))
        counts.update(item.get("counts", {}))
        by_case.update(item.get("by_case", {}))
        for key in ("missing_files", "files_processed", "files_rewritten", "report_rows_updated"):
            totals[key] += int(item.get(key, 0))
    value = {
        "schema_version": 1,
        "mode": "finalize",
        "part_count": part_count,
        "missing_parts": missing_parts,
        **dict(totals),
        "counts": dict(counts),
        "by_case": dict(sorted(by_case.items())),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _atomic_write_json(output, value)
    return value


def _resolve(repo_root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (repo_root / path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    ls = sub.add_parser("list-sources", help="Append deterministic samples/report pairs to a source list.")
    ls.add_argument("--runs-root", required=True)
    ls.add_argument("--rounds", default="0")
    ls.add_argument("--shard-count", type=int, default=8192)
    ls.add_argument("--campaign-prefix", default="", help="Per-shard report campaign prefix; empty for none.")
    ls.add_argument("--output", required=True)
    ls.add_argument("--append", action="store_true")
    rp = sub.add_parser("run-part", help="Rescale one contiguous partition of the source list.")
    rp.add_argument("--source-list", required=True)
    rp.add_argument("--part-index", type=int, required=True)
    rp.add_argument("--part-count", type=int, required=True)
    rp.add_argument("--manifest", required=True)
    rp.add_argument("--dry-run", action="store_true")
    one = sub.add_parser("file", help="Rescale a single samples file (testing/spot checks).")
    one.add_argument("--samples", required=True)
    one.add_argument("--report", default="")
    one.add_argument("--dry-run", action="store_true")
    fin = sub.add_parser("finalize", help="Combine part manifests.")
    fin.add_argument("--parts-dir", required=True)
    fin.add_argument("--part-count", type=int, required=True)
    fin.add_argument("--output", required=True)
    args = parser.parse_args()
    repo_root = Path(__file__).resolve().parents[1]

    if args.command == "list-sources":
        lines = list_sources(
            repo_root, _resolve(repo_root, args.runs_root), _parse_rounds(args.rounds), args.shard_count, args.campaign_prefix
        )
        output = _resolve(repo_root, args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("a" if args.append else "w", encoding="utf-8") as handle:
            handle.write("".join(line + "\n" for line in lines))
        result: dict[str, Any] = {"source_files": len(lines), "output": str(output)}
    elif args.command == "run-part":
        result = run_part(
            _resolve(repo_root, args.source_list),
            args.part_index,
            args.part_count,
            _resolve(repo_root, args.manifest),
            args.dry_run,
        )
    elif args.command == "file":
        result = process_file(
            _resolve(repo_root, args.samples), _resolve(repo_root, args.report) if args.report else None, args.dry_run
        )
    else:
        result = finalize(_resolve(repo_root, args.parts_dir), args.part_count, _resolve(repo_root, args.output))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
