#!/usr/bin/env python3
"""Rebuild per-shard AC-OPF execution reports and ledgers from stored samples.

Shard reports and ledgers of a map/reduce round are derived from the solutions in
``samples.jsonl``. After samples are rewritten in place (cost rescale, quadratic
re-solve merge) those derived files are stale; resumed map jobs also overwrote each
shard report with only their own session's rows while appending to the ledgers.

run-part recomputes, for every shard of the requested rounds, the report rows and
the diversity / active-constraint / security-boundary ledgers with the map step's
own helpers, replacing the shard files atomically. finalize verifies all parts and
that no legacy-cost PowerModels record remains. Re-run reduce_campaign_shards.py
--force per round afterwards.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from grid_data_factory.boundaries.security_margin import classify_security_margin_band, compute_security_margin  # noqa: E402
from grid_data_factory.campaigns.execution_policy import load_execution_policy, settings_for_case  # noqa: E402
from grid_data_factory.campaigns.round_runner import (  # noqa: E402
    _build_margins,
    _descriptor_from_result,
    _load_bands,
    classify_feasibility,
)
from grid_data_factory.constraints.active_sets import build_active_constraint_signature  # noqa: E402
from grid_data_factory.diversity.duplicate_detection import classify_duplicate_status  # noqa: E402
from grid_data_factory.sources.registry import dataset_for, grid_family_for  # noqa: E402
from grid_data_factory.storage import paths  # noqa: E402

LEDGERS = ("diversity_ledger", "active_constraint_ledger", "security_boundary_ledger")


def _parse_rounds(value: str) -> list[int]:
    rounds: set[int] = set()
    for token in value.split(","):
        token = token.strip()
        if "-" in token:
            start, end = (int(item) for item in token.split("-", 1))
            rounds.update(range(start, end + 1))
        elif token:
            rounds.add(int(token))
    return sorted(rounds)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".rebuild_in_progress")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def _jsonl(rows: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(row, ensure_ascii=True, default=str) + "\n" for row in rows)


def shard_units(repo_root: Path, campaign_id: str, rounds: list[int]) -> list[tuple[int, str]]:
    campaign_root = paths.campaign_root(repo_root, campaign_id)
    units = []
    for round_index in rounds:
        ids_path = campaign_root / "round_summaries" / f"round_{round_index:03d}_shards" / "shard_campaign_ids.txt"
        ids = sorted(line.strip() for line in ids_path.read_text(encoding="utf-8").splitlines() if line.strip())
        units.extend((round_index, shard_id) for shard_id in ids)
    return units


def rebuild_shard(
    repo_root: Path,
    campaign_id: str,
    round_index: int,
    shard_id: str,
    runs_root: Path,
    bands: dict[str, dict[str, float]],
    policy: dict[str, Any],
    policy_path: str | None,
    timeout_s: float,
) -> dict[str, int]:
    shard_number = shard_id.rsplit("__s", 1)[1]
    round_pad = f"{round_index:03d}"
    samples_path = runs_root / f"mapreduce_round_{round_pad}" / f"shard_{shard_number}" / "ac_opf" / "samples.jsonl"
    shard_root = paths.campaigns_root(repo_root) / shard_id
    report_path = shard_root / "round_summaries" / f"round_{round_pad}_ac_execution_report.json"
    selected_path = (
        paths.campaign_root(repo_root, campaign_id) / "round_summaries" / f"round_{round_pad}_shards"
        / f"selected_{shard_number}.jsonl"
    )
    old_report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
    counts = {"shards": 1, "records": 0, "malformed_rows": 0, "duplicate_candidate_rows": 0,
              "legacy_cost_records": 0, "unmarked_without_solution": 0, "missing_samples": 0}

    # Last occurrence of a candidate wins; positive activation keys are kept per
    # candidate so a superseded record's ledger contribution can be removed.
    entries: dict[str, dict[str, Any]] = {}
    constraint_keys: set[str] = set()
    if not samples_path.exists():
        counts["missing_samples"] = 1
    else:
        with samples_path.open(encoding="utf-8") as handle:
            for raw_line in handle:
                try:
                    record = json.loads(raw_line)
                except json.JSONDecodeError:
                    counts["malformed_rows"] += 1
                    continue
                if record.get("task", "ac_opf") != "ac_opf":
                    continue
                result = dict(record.get("result") or {})
                if result.get("solver_name") == "powermodels" and not result.get("cost_convention"):
                    # Without a dispatch and objective there is no cost value to be stale.
                    solution = (result.get("raw_result") or {}).get("solution") or {}
                    if solution.get("gen") and record.get("objective", result.get("objective")) is not None:
                        counts["legacy_cost_records"] += 1
                    else:
                        counts["unmarked_without_solution"] += 1
                inputs = record.get("inputs") or {}
                candidate = dict(inputs.get("candidate") or {})
                case_data = inputs.get("resolved_case") or {}
                candidate_id = str(record.get("candidate_id", candidate.get("candidate_id")))
                case_id = str(record.get("case_id", candidate.get("case_id")))
                run_id = record.get("run_id")
                result["_run_id"] = run_id

                margins = _build_margins(case_data, result)
                active_sig = build_active_constraint_signature(margins)
                sec_margin = compute_security_margin(margins)
                sec_band = classify_security_margin_band(sec_margin, bands)
                constraint_keys.update(active_sig)

                family = candidate.get("grid_family") or grid_family_for(repo_root, case_id)
                dataset = candidate.get("dataset") or dataset_for(repo_root, case_id)
                desc = _descriptor_from_result(candidate, case_data, result, sec_margin, active_sig)
                desc["attempt_dir"] = str(samples_path)
                desc["round_index"] = round_index
                desc["grid_family"] = family
                desc["dataset"] = dataset
                desc["topology_id"] = candidate.get("topology_id", "topology_000000_baseline")
                desc["switched_branch_count"] = int(candidate.get("switched_branch_count", 0))
                expansion_plan = candidate.get("expansion")
                if expansion_plan:
                    desc["expansion_id"] = expansion_plan.get("expansion_id")
                    desc["expansion_class"] = expansion_plan.get("expansion_class")

                settings = settings_for_case(policy, case_id, timeout_s)
                runtime_meta = record.get("runtime_metadata") or result.get("runtime_metadata") or {}
                exec_ctx = runtime_meta.get("execution_context") or {}
                if candidate_id in entries:
                    counts["duplicate_candidate_rows"] += 1
                entries[candidate_id] = {
                    "desc": desc,
                    "active": {key: state for key, state in active_sig.items() if state},
                    "boundary": {
                        "stress_trajectory_id": f"{candidate_id}::trajectory",
                        "base_operating_point": candidate_id,
                        "stress_direction": candidate.get("candidate_generation_mechanism", "unknown"),
                        "secure_endpoint": None,
                        "insecure_endpoint": None,
                        "boundary_estimate": sec_margin,
                        "margin_band": sec_band,
                        "limiting_constraint": min(margins, key=lambda k: margins[k]) if margins else "unknown",
                        "related_run_ids": [run_id],
                    },
                    "solved": {
                        "candidate_id": candidate_id,
                        "run_id": run_id,
                        "attempt_dir": str(samples_path),
                        "grid_family": family,
                        "dataset": dataset,
                        "topology_id": candidate.get("topology_id", "topology_000000_baseline"),
                        "topology_class": candidate.get("topology_class", "baseline"),
                        "success": bool(record.get("success", result.get("success", False))),
                        "termination_status": record.get("termination_status", result.get("termination_status")),
                        "feasibility_label": classify_feasibility(result),
                        "objective": record.get("objective", result.get("objective")),
                        "runtime": record.get("solve_time", result.get("solve_time", result.get("runtime"))),
                        "wallclock_seconds": runtime_meta.get("wallclock_seconds", record.get("wallclock_seconds")),
                        "mpi_processes": exec_ctx.get("mpi_processes"),
                        "gpu_enabled": exec_ctx.get("gpu_enabled"),
                        "gpu_type": exec_ctx.get("gpu_type"),
                        "security_margin": sec_margin,
                        "security_margin_band": sec_band,
                        "execution_tier": settings.tier,
                        "configured_timeout_s": settings.timeout_s,
                        "cost_convention": result.get("cost_convention"),
                    },
                }
                counts["records"] += 1

    diversity_rows: list[dict[str, Any]] = []
    active_counts: dict[str, list[int]] = {key: [0, 0] for key in constraint_keys}
    for entry in entries.values():
        desc = entry["desc"]
        status, distance = classify_duplicate_status(desc, diversity_rows)
        desc["duplicate_status"] = status
        desc["nearest_neighbor_distance"] = distance
        desc["training_weight_recommendation"] = 0.3 if status == "near_duplicate" else 1.0
        diversity_rows.append(desc)
        for key, state in entry["active"].items():
            active_counts[key][0 if state == 2 else 1] += 1
    active_rows = []
    for key in sorted(active_counts):
        family, component_id = (key.split(":", 1) + ["unknown"])[:2]
        active, near = active_counts[key]
        active_rows.append({"constraint_family": family, "component_id": component_id, "active_count": active,
                            "near_active_count": near, "last_discovery_round": round_index})

    solved_ids = set(entries)
    failed_rows = [row for row in old_report.get("failed", []) if str(row.get("candidate_id")) not in solved_ids]
    failed_ids = {str(row.get("candidate_id")) for row in failed_rows}
    # Unsampled candidates of policy-disabled cases were skipped by the map step.
    input_count = skipped = 0
    if selected_path.exists():
        with selected_path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                input_count += 1
                candidate = json.loads(line)
                candidate_id = str(candidate.get("candidate_id"))
                if candidate_id in solved_ids or candidate_id in failed_ids:
                    continue
                if not settings_for_case(policy, str(candidate.get("case_id")), timeout_s).enabled:
                    skipped += 1
    else:
        input_count = int(old_report.get("input_candidate_count", 0))
    ledger_paths = {name: shard_root / f"{name}.parquet" for name in LEDGERS}
    _atomic_write_text(ledger_paths["diversity_ledger"].with_suffix(".parquet.jsonl"), _jsonl(diversity_rows))
    _atomic_write_text(ledger_paths["active_constraint_ledger"].with_suffix(".parquet.jsonl"), _jsonl(active_rows))
    _atomic_write_text(
        ledger_paths["security_boundary_ledger"].with_suffix(".parquet.jsonl"),
        _jsonl([entry["boundary"] for entry in entries.values()]),
    )
    solvable = input_count - skipped
    report = {
        "ok": True,
        "campaign_id": shard_id,
        "round_index": round_index,
        "input_candidate_count": input_count,
        "solved_count": len(entries),
        "failed_count": len(failed_rows),
        "skipped_count": skipped,
        "unsampled_candidate_count": max(0, input_count - len(entries) - len(failed_rows) - skipped),
        "resume": False,
        "execution_policy": policy_path,
        "failure_fraction": round(len(failed_rows) / solvable, 6) if solvable else 0.0,
        "max_failure_fraction": old_report.get("max_failure_fraction", 0.5),
        "solved": [entry["solved"] for entry in entries.values()],
        "failed": failed_rows,
        "updated_ledgers": [str(path) for path in ledger_paths.values()],
        "rebuilt_from_samples": {
            "rebuilt_at": datetime.now(timezone.utc).isoformat(),
            "samples": str(samples_path),
            **{key: value for key, value in counts.items() if key != "shards"},
        },
    }
    _atomic_write_text(report_path, json.dumps(report, indent=2) + "\n")
    return counts


def run_part(
    repo_root: Path,
    campaign_id: str,
    rounds: list[int],
    runs_root: Path,
    config: str,
    execution_policy: str | None,
    timeout_s: float,
    work_dir: Path,
    part_index: int,
    part_count: int,
) -> dict[str, Any]:
    parts_dir = work_dir / "parts"
    manifest_path = parts_dir / f"part_{part_index:05d}.json"
    if manifest_path.exists():
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    bands = _load_bands(repo_root, config)
    policy = load_execution_policy((repo_root / execution_policy).resolve() if execution_policy else None)
    progress_path = parts_dir / f"part_{part_index:05d}.progress.jsonl"
    totals = {"shards": 0, "records": 0, "malformed_rows": 0, "duplicate_candidate_rows": 0,
              "legacy_cost_records": 0, "unmarked_without_solution": 0, "missing_samples": 0}
    done: set[str] = set()
    if progress_path.exists():
        for line in progress_path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            done.add(item["shard_id"])
            for key in totals:
                totals[key] += int(item["counts"].get(key, 0))
    parts_dir.mkdir(parents=True, exist_ok=True)
    with progress_path.open("a", encoding="utf-8") as progress:
        for round_index, shard_id in shard_units(repo_root, campaign_id, rounds)[part_index::part_count]:
            if shard_id in done:
                continue
            counts = rebuild_shard(repo_root, campaign_id, round_index, shard_id, runs_root, bands, policy,
                                   execution_policy, timeout_s)
            for key in totals:
                totals[key] += counts[key]
            progress.write(json.dumps({"shard_id": shard_id, "counts": counts}) + "\n")
            progress.flush()
    manifest = {"schema_version": 1, "campaign_id": campaign_id, "rounds": rounds,
                "part_index": part_index, "part_count": part_count, **totals}
    _atomic_write_text(manifest_path, json.dumps(manifest, indent=2) + "\n")
    return manifest


def finalize(work_dir: Path, part_count: int, output: Path) -> dict[str, Any]:
    totals = {"shards": 0, "records": 0, "malformed_rows": 0, "duplicate_candidate_rows": 0,
              "legacy_cost_records": 0, "unmarked_without_solution": 0, "missing_samples": 0}
    missing = []
    for index in range(part_count):
        path = work_dir / "parts" / f"part_{index:05d}.json"
        if not path.exists():
            missing.append(index)
            continue
        part = json.loads(path.read_text(encoding="utf-8"))
        for key in totals:
            totals[key] += int(part.get(key, 0))
    summary = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "complete": not missing and totals["legacy_cost_records"] == 0,
        "part_count": part_count,
        "missing_parts": missing,
        **totals,
    }
    _atomic_write_text(output, json.dumps(summary, indent=2) + "\n")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    part = sub.add_parser("run-part")
    part.add_argument("--campaign-id", required=True)
    part.add_argument("--rounds", required=True)
    part.add_argument("--runs-root", required=True)
    part.add_argument("--config", required=True)
    part.add_argument("--execution-policy", default=None)
    part.add_argument("--timeout-s", type=float, default=1800.0)
    part.add_argument("--work-dir", required=True)
    part.add_argument("--part-index", type=int, required=True)
    part.add_argument("--part-count", type=int, required=True)
    fin = sub.add_parser("finalize")
    fin.add_argument("--work-dir", required=True)
    fin.add_argument("--part-count", type=int, required=True)
    fin.add_argument("--output", required=True)
    args = parser.parse_args()

    def resolve(value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else (REPO_ROOT / path).resolve()

    if args.command == "run-part":
        out = run_part(REPO_ROOT, args.campaign_id, _parse_rounds(args.rounds), resolve(args.runs_root), args.config,
                       args.execution_policy, args.timeout_s, resolve(args.work_dir), args.part_index, args.part_count)
    else:
        out = finalize(resolve(args.work_dir), args.part_count, resolve(args.output))
    print(json.dumps(out, indent=2))
    if args.command == "finalize" and not out["complete"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
