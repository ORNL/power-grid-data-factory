#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    from grid_data_factory.campaigns.ledgers import append_parquet_rows, create_campaign_layout
except ModuleNotFoundError:
    import sys

    _repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(_repo_root / "src"))
    from grid_data_factory.campaigns.ledgers import append_parquet_rows, create_campaign_layout


def _require_yaml():
    try:
        import yaml  # type: ignore
    except ModuleNotFoundError:
        return None
    return yaml


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Reduce shard campaign outputs into a single deterministic campaign ledger update.")
    p.add_argument("--campaign-id", required=True)
    p.add_argument("--round-index", type=int, required=True)
    p.add_argument("--config", default="configs/campaign_default.yaml")
    p.add_argument("--shard-campaign-ids-file", required=True)
    p.add_argument("--force", action="store_true", help="Allow rerun even if reduce marker exists.")
    return p.parse_args()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _read_ledger_rows(campaign_root: Path, ledger_name: str) -> list[dict[str, Any]]:
    parquet_path = campaign_root / ledger_name
    fallback_path = parquet_path.with_suffix(parquet_path.suffix + ".jsonl")

    if fallback_path.exists():
        return _read_jsonl(fallback_path)

    try:
        import pandas as pd  # type: ignore
    except ModuleNotFoundError:
        return []

    if parquet_path.exists():
        try:
            return pd.read_parquet(parquet_path).to_dict(orient="records")
        except Exception:
            return []
    return []


def _stable_sort(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def _key(row: dict[str, Any]) -> tuple[str, str, str, str]:
        return (
            str(row.get("round_index", "")),
            str(row.get("candidate_id", "")),
            str(row.get("run_id", "")),
            str(row.get("attempt_dir", "")),
        )

    return sorted(rows, key=_key)


def _dedup_by_key(rows: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for row in rows:
        k = tuple(str(row.get(name, "")) for name in keys)
        if k in seen:
            continue
        seen.add(k)
        out.append(row)
    return out


def _aggregate_active_constraint(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (str(row.get("constraint_family", "unknown")), str(row.get("component_id", "unknown")))
        prev = by_key.get(
            key,
            {
                "constraint_family": key[0],
                "component_id": key[1],
                "active_count": 0,
                "near_active_count": 0,
                "last_discovery_round": -1,
            },
        )
        prev["active_count"] = int(prev.get("active_count", 0)) + int(row.get("active_count", 0))
        prev["near_active_count"] = int(prev.get("near_active_count", 0)) + int(row.get("near_active_count", 0))
        prev["last_discovery_round"] = max(int(prev.get("last_discovery_round", -1)), int(row.get("last_discovery_round", -1)))
        by_key[key] = prev

    return [by_key[k] for k in sorted(by_key)]


def _load_config(repo_root: Path, config_rel: str) -> dict[str, Any]:
    path = (repo_root / config_rel).resolve()
    yaml = _require_yaml()
    if yaml is None:
        return {
            "acquisition_budget": {
                "broad_coverage": 0.25,
                "active_constraint_novelty": 0.20,
                "security_boundary": 0.20,
                "credible_contingencies": 0.15,
                "high_severity_and_uncertainty": 0.10,
                "unscreened_audit": 0.10,
            }
        }
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    campaign_root = repo_root / "data" / "campaigns" / args.campaign_id
    round_pad = f"{args.round_index:03d}"

    marker_path = campaign_root / "round_summaries" / f"round_{round_pad}_mapreduce_reduce_report.json"
    if marker_path.exists() and not args.force:
        raise SystemExit(f"Reduce marker already exists: {marker_path}. Use --force to rerun.")

    config = _load_config(repo_root, args.config)
    create_campaign_layout(campaign_root, config)

    ids_file = (repo_root / args.shard_campaign_ids_file).resolve()
    shard_ids = [line.strip() for line in ids_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    shard_ids = sorted(shard_ids)

    all_diversity: list[dict[str, Any]] = []
    all_active: list[dict[str, Any]] = []
    all_boundary: list[dict[str, Any]] = []
    all_contingency: list[dict[str, Any]] = []
    solved: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    missing_reports: list[str] = []

    for shard_id in shard_ids:
        shard_root = repo_root / "data" / "campaigns" / shard_id
        all_diversity.extend(_read_ledger_rows(shard_root, "diversity_ledger.parquet"))
        all_active.extend(_read_ledger_rows(shard_root, "active_constraint_ledger.parquet"))
        all_boundary.extend(_read_ledger_rows(shard_root, "security_boundary_ledger.parquet"))
        all_contingency.extend(_read_ledger_rows(shard_root, "contingency_portfolio.parquet"))

        report_path = shard_root / "round_summaries" / f"round_{round_pad}_ac_execution_report.json"
        if report_path.exists():
            report = _read_json(report_path)
            solved.extend(list(report.get("solved", [])))
            failed.extend(list(report.get("failed", [])))
        else:
            missing_reports.append(str(report_path))

    all_diversity = _dedup_by_key(_stable_sort(all_diversity), ("candidate_id", "run_id"))
    all_boundary = _dedup_by_key(_stable_sort(all_boundary), ("stress_trajectory_id", "base_operating_point"))
    all_contingency = _dedup_by_key(_stable_sort(all_contingency), ("candidate_id",))
    all_active = _aggregate_active_constraint(all_active)

    append_parquet_rows(campaign_root / "diversity_ledger.parquet", all_diversity)
    append_parquet_rows(campaign_root / "active_constraint_ledger.parquet", all_active)
    append_parquet_rows(campaign_root / "security_boundary_ledger.parquet", all_boundary)
    append_parquet_rows(campaign_root / "contingency_portfolio.parquet", all_contingency)

    summary = {
        "ok": len(missing_reports) == 0,
        "campaign_id": args.campaign_id,
        "round_index": args.round_index,
        "mode": "map_reduce_shard_merge",
        "shard_campaign_ids": shard_ids,
        "merged_counts": {
            "diversity": len(all_diversity),
            "active_constraint": len(all_active),
            "security_boundary": len(all_boundary),
            "contingency_portfolio": len(all_contingency),
            "solved": len(solved),
            "failed": len(failed),
        },
        "solved": _stable_sort(solved),
        "failed": _stable_sort(failed),
        "missing_shard_reports": missing_reports,
        "updated_ledgers": [
            str(campaign_root / "diversity_ledger.parquet"),
            str(campaign_root / "active_constraint_ledger.parquet"),
            str(campaign_root / "security_boundary_ledger.parquet"),
            str(campaign_root / "contingency_portfolio.parquet"),
        ],
    }

    marker_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({"ok": summary["ok"], "report": str(marker_path)}, indent=2))

    if not summary["ok"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
