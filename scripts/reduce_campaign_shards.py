#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

try:
    from grid_data_factory.campaigns.ledgers import create_campaign_layout
    from grid_data_factory.storage import paths
except ModuleNotFoundError:
    import sys

    _repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(_repo_root / "src"))
    from grid_data_factory.campaigns.ledgers import create_campaign_layout
    from grid_data_factory.storage import paths


LEDGER_NAMES = (
    "diversity_ledger.parquet",
    "active_constraint_ledger.parquet",
    "security_boundary_ledger.parquet",
    "contingency_portfolio.parquet",
)
CHECKPOINT_INTERVAL = 128


def _require_yaml():
    try:
        import yaml  # type: ignore
    except ModuleNotFoundError:
        return None
    return yaml


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Validate shard outputs and publish a deterministic round manifest.")
    p.add_argument("--campaign-id", required=True)
    p.add_argument("--round-index", type=int, required=True)
    p.add_argument("--config", default="configs/campaign_default.yaml")
    p.add_argument("--shard-campaign-ids-file", required=True)
    p.add_argument("--force", action="store_true", help="Rebuild an existing reduce report.")
    return p.parse_args()


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".in_progress")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def _ledger_path(shard_root: Path, ledger_name: str) -> Path | None:
    parquet = shard_root / ledger_name
    fallback = parquet.with_suffix(parquet.suffix + ".jsonl")
    if fallback.exists():
        return fallback
    return parquet if parquet.exists() else None


def _initial_state(campaign_id: str, round_index: int, shard_ids: list[str]) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "campaign_id": campaign_id,
        "round_index": round_index,
        "shard_ids": shard_ids,
        "next_shard_index": 0,
        "input_candidates": 0,
        "solved": 0,
        "failed": 0,
        "skipped": 0,
        "missing_reports": [],
        "invalid_reports": [],
        "ledger_fragments": {name: [] for name in LEDGER_NAMES},
    }


def _load_or_initialize_state(
    state_path: Path,
    campaign_id: str,
    round_index: int,
    shard_ids: list[str],
) -> dict[str, Any]:
    if not state_path.exists():
        return _initial_state(campaign_id, round_index, shard_ids)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if (
        state.get("schema_version") != 2
        or state.get("campaign_id") != campaign_id
        or state.get("round_index") != round_index
        or state.get("shard_ids") != shard_ids
    ):
        raise SystemExit(f"Reducer checkpoint does not match this invocation: {state_path}")
    return state


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


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


def reduce_shards(
    repo_root: Path,
    campaign_id: str,
    round_index: int,
    config_rel: str,
    ids_file: Path,
    force: bool = False,
) -> dict[str, Any]:
    campaign_root = paths.campaign_root(repo_root, campaign_id)
    round_pad = f"{round_index:03d}"
    marker_path = campaign_root / "round_summaries" / f"round_{round_pad}_mapreduce_reduce_report.json"
    state_path = campaign_root / "round_summaries" / f"round_{round_pad}_reduce_state.json"
    if marker_path.exists() and not force:
        try:
            if json.loads(marker_path.read_text(encoding="utf-8")).get("ok") is True:
                raise SystemExit(f"Reduce marker already exists: {marker_path}. Use --force to rebuild it.")
        except json.JSONDecodeError:
            pass
        marker_path.unlink()
    if force:
        marker_path.unlink(missing_ok=True)
        state_path.unlink(missing_ok=True)

    create_campaign_layout(campaign_root, _load_config(repo_root, config_rel))
    shard_ids = sorted(line.strip() for line in ids_file.read_text(encoding="utf-8").splitlines() if line.strip())
    state = _load_or_initialize_state(state_path, campaign_id, round_index, shard_ids)
    for index in range(int(state["next_shard_index"]), len(shard_ids)):
        shard_id = shard_ids[index]
        shard_root = paths.campaigns_root(repo_root) / shard_id
        report_path = shard_root / "round_summaries" / f"round_{round_pad}_ac_execution_report.json"
        report_name = str(report_path)
        state["missing_reports"] = [path for path in state["missing_reports"] if path != report_name]
        state["invalid_reports"] = [path for path in state["invalid_reports"] if path != report_name]
        if not report_path.exists():
            state["missing_reports"].append(report_name)
            _atomic_write_json(state_path, state)
            break
        report = _read_json(report_path)
        if report.get("ok") is False:
            state["invalid_reports"].append(report_name)
            _atomic_write_json(state_path, state)
            break
        state["input_candidates"] += int(report.get("input_candidate_count", 0))
        state["solved"] += int(report.get("solved_count", len(report.get("solved", []))))
        state["failed"] += int(report.get("failed_count", len(report.get("failed", []))))
        state["skipped"] += int(report.get("skipped_count", 0))
        for ledger_name in LEDGER_NAMES:
            ledger_path = _ledger_path(shard_root, ledger_name)
            if ledger_path is not None:
                state["ledger_fragments"][ledger_name].append(str(ledger_path))
        state["next_shard_index"] = index + 1
        if state["next_shard_index"] % CHECKPOINT_INTERVAL == 0:
            _atomic_write_json(state_path, state)

    summary = {
        "ok": not state["missing_reports"] and not state["invalid_reports"],
        "schema_version": 2,
        "campaign_id": campaign_id,
        "round_index": round_index,
        "mode": "authoritative_shard_manifest",
        "shard_count": len(shard_ids),
        "shard_campaign_ids": shard_ids,
        "merged_counts": {
            "input_candidates": state["input_candidates"],
            "solved": state["solved"],
            "failed": state["failed"],
            "skipped": state["skipped"],
        },
        "missing_shard_reports": state["missing_reports"],
        "invalid_shard_reports": state["invalid_reports"],
        "ledger_fragments": state["ledger_fragments"],
        "updated_ledgers": [],
    }
    _atomic_write_json(marker_path, summary)
    if summary["ok"]:
        state_path.unlink(missing_ok=True)
    return summary


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    ids_file = Path(args.shard_campaign_ids_file)
    ids_file = ids_file if ids_file.is_absolute() else repo_root / ids_file
    summary = reduce_shards(repo_root, args.campaign_id, args.round_index, args.config, ids_file, args.force)
    print(json.dumps(summary, indent=2))
    if not summary["ok"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
