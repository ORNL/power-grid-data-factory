#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import pyarrow.parquet as pq

try:
    from grid_data_factory.campaigns.ledgers import append_parquet_rows, create_campaign_layout
    from grid_data_factory.storage import paths
except ModuleNotFoundError:
    _repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(_repo_root / "src"))
    from grid_data_factory.campaigns.ledgers import append_parquet_rows, create_campaign_layout
    from grid_data_factory.storage import paths


LEDGERS = ("diversity_ledger.parquet", "security_boundary_ledger.parquet", "pf_coverage_ledger.parquet")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deterministically reduce PF shard ledgers and reports.")
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--round-index", type=int, required=True)
    parser.add_argument("--config", default="configs/pf_campaign_riker.yaml")
    parser.add_argument("--shard-campaign-ids-file", required=True)
    parser.add_argument("--runs-root", default="", help="PF runs root used to aggregate per-sample solver provenance.")
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def iter_rows(root: Path, name: str) -> Iterable[dict[str, Any]]:
    parquet = root / name
    fallback = parquet.with_suffix(parquet.suffix + ".jsonl")
    if fallback.exists():
        with fallback.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    yield json.loads(line)
    elif parquet.exists() and parquet.stat().st_size > 0:
        try:
            for batch in pq.ParquetFile(parquet).iter_batches():
                yield from batch.to_pylist()
        except Exception:
            return


def write_atomic_jsonl(path: Path, rows: Iterable[dict[str, Any]], retain_existing: bool = True) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".in_progress")
    count = 0
    with temporary.open("w", encoding="utf-8") as handle:
        if retain_existing and path.exists():
            with path.open("r", encoding="utf-8") as existing:
                for line in existing:
                    if line.strip():
                        handle.write(line if line.endswith("\n") else line + "\n")
                        count += 1
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")
            count += 1
    temporary.replace(path)
    return count


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    config_path = Path(args.config)
    config_path = config_path if config_path.is_absolute() else repo_root / config_path
    import yaml

    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    campaign_root = paths.campaign_root(repo_root, args.campaign_id)
    create_campaign_layout(campaign_root, config)
    marker = campaign_root / "round_summaries" / f"round_{args.round_index:03d}_pf_reduce_report.json"
    if marker.exists() and not args.force:
        raise SystemExit(f"Reduce marker already exists: {marker}")
    ids_path = Path(args.shard_campaign_ids_file)
    ids_path = ids_path if ids_path.is_absolute() else repo_root / ids_path
    shard_ids = sorted(line.strip() for line in ids_path.read_text(encoding="utf-8").splitlines() if line.strip())
    shard_roots = [paths.campaign_root(repo_root, shard_id) for shard_id in shard_ids]

    missing_reports = []
    reports = []
    for shard_id, shard_root in zip(shard_ids, shard_roots):
        report_path = shard_root / "round_summaries" / f"round_{args.round_index:03d}_pf_execution_report.json"
        if not report_path.exists():
            missing_reports.append(shard_id)
        else:
            reports.append(json.loads(report_path.read_text(encoding="utf-8")))

    ledger_counts = {}
    for ledger in LEDGERS:
        output = (campaign_root / ledger).with_suffix(".parquet.jsonl")
        ledger_counts[ledger] = write_atomic_jsonl(
            output,
            (row for shard_root in shard_roots for row in iter_rows(shard_root, ledger)),
        )

    active: dict[tuple[str, str], dict[str, Any]] = {}
    active_output = (campaign_root / "active_constraint_ledger.parquet").with_suffix(".parquet.jsonl")
    if active_output.exists():
        for row in iter_rows(campaign_root, "active_constraint_ledger.parquet"):
            key = (str(row.get("constraint_family", "unknown")), str(row.get("component_id", "unknown")))
            active[key] = dict(row)
    for shard_root in shard_roots:
        for row in iter_rows(shard_root, "active_constraint_ledger.parquet"):
            key = (str(row.get("constraint_family", "unknown")), str(row.get("component_id", "unknown")))
            aggregate = active.setdefault(
                key,
                {"constraint_family": key[0], "component_id": key[1], "active_count": 0, "near_active_count": 0, "last_discovery_round": -1},
            )
            aggregate["active_count"] += int(row.get("active_count", 0))
            aggregate["near_active_count"] += int(row.get("near_active_count", 0))
            aggregate["last_discovery_round"] = max(aggregate["last_discovery_round"], int(row.get("last_discovery_round", -1)))
    ledger_counts["active_constraint_ledger.parquet"] = write_atomic_jsonl(
        active_output,
        (active[key] for key in sorted(active)),
        retain_existing=False,
    )

    outcomes: Counter[str] = Counter()
    for report in reports:
        outcomes.update(report.get("outcome_counts") or {})

    runs_root = Path(args.runs_root) if args.runs_root else repo_root / "data" / "outputs" / "runs" / args.campaign_id
    runs_root = runs_root if runs_root.is_absolute() else repo_root / runs_root
    linear_solvers: Counter[str] = Counter()
    solver_attempts: Counter[str] = Counter()
    sample_pattern = f"mapreduce_round_{args.round_index:03d}/shard_*/pf/samples.jsonl"
    for samples_path in sorted(runs_root.glob(sample_pattern)):
        with samples_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    result = (json.loads(line).get("result") or {}) if line.strip() else {}
                except json.JSONDecodeError:
                    continue
                linear_solvers[str(result.get("linear_solver", "unknown"))] += 1
                for attempt in result.get("solver_attempts") or []:
                    key = f"{attempt.get('linear_solver', 'unknown')}::{attempt.get('termination_status', 'unknown')}"
                    solver_attempts[key] += 1
    summary = {
        "ok": not missing_reports and all(bool(report.get("ok")) for report in reports),
        "campaign_id": args.campaign_id,
        "round_index": args.round_index,
        "shard_count": len(shard_ids),
        "reports_found": len(reports),
        "missing_reports": missing_reports,
        "candidate_count": sum(int(report.get("candidate_count", 0)) for report in reports),
        "solved": sum(int(report.get("solved", 0)) for report in reports),
        "failed": sum(int(report.get("failed", 0)) for report in reports),
        "skipped": sum(int(report.get("skipped", 0)) for report in reports),
        "outcome_counts": dict(outcomes),
        "linear_solver_counts": dict(linear_solvers),
        "solver_attempt_counts": dict(solver_attempts),
        "ledger_counts": ledger_counts,
    }
    temporary = marker.with_suffix(marker.suffix + ".in_progress")
    temporary.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    temporary.replace(marker)
    print(json.dumps(summary, indent=2))
    if not summary["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()