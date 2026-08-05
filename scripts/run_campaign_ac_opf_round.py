#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    from grid_data_factory.boundaries.security_margin import classify_security_margin_band, compute_security_margin
    from grid_data_factory.campaigns.ledgers import append_parquet_rows
    from grid_data_factory.campaigns.round_runner import (
        _append_sample,
        _build_margins,
        _candidate_solver_dir,
        _descriptor_from_result,
        _load_bands,
        _loaded_sample_ids,
        _read_existing_diversity,
        _read_jsonl,
        _resolve_case_file,
        _write_shard_manifest,
        SampleSink,
    )
    from grid_data_factory.constraints.active_sets import build_active_constraint_signature
    from grid_data_factory.constraints.coverage_ledger import update_active_constraint_ledger
    from grid_data_factory.contingencies.apply import apply_contingency
    from grid_data_factory.diversity.duplicate_detection import classify_duplicate_status
    from grid_data_factory.parsers.matpower import parse_matpower_case
    from grid_data_factory.solvers.powermodels_adapter import PowerModelsAdapter
    from grid_data_factory.sources.registry import dataset_for, grid_family_for
    from grid_data_factory.topology.generation import apply_topology
    from grid_data_factory.scenarios.load_snapshots import get_snapshot_bus_loads
    from grid_data_factory.scenarios.operating_points import apply_operating_point
    from grid_data_factory.storage.layout import has_finalized_attempt
except ModuleNotFoundError:
    _repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(_repo_root / "src"))
    from grid_data_factory.boundaries.security_margin import classify_security_margin_band, compute_security_margin
    from grid_data_factory.campaigns.ledgers import append_parquet_rows
    from grid_data_factory.campaigns.round_runner import (
        _append_sample,
        _build_margins,
        _candidate_solver_dir,
        _descriptor_from_result,
        _load_bands,
        _loaded_sample_ids,
        _read_existing_diversity,
        _read_jsonl,
        _resolve_case_file,
        _write_shard_manifest,
        SampleSink,
    )
    from grid_data_factory.constraints.active_sets import build_active_constraint_signature
    from grid_data_factory.constraints.coverage_ledger import update_active_constraint_ledger
    from grid_data_factory.contingencies.apply import apply_contingency
    from grid_data_factory.diversity.duplicate_detection import classify_duplicate_status
    from grid_data_factory.parsers.matpower import parse_matpower_case
    from grid_data_factory.solvers.powermodels_adapter import PowerModelsAdapter
    from grid_data_factory.sources.registry import dataset_for, grid_family_for
    from grid_data_factory.topology.generation import apply_topology
    from grid_data_factory.scenarios.load_snapshots import get_snapshot_bus_loads
    from grid_data_factory.scenarios.operating_points import apply_operating_point
    from grid_data_factory.storage.layout import has_finalized_attempt

from grid_data_factory.storage import paths  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run selected adaptive-campaign candidates through AC-OPF and update post-solve ledgers.")
    p.add_argument("--campaign-id", required=True)
    p.add_argument("--round-index", type=int, required=True)
    p.add_argument("--selected-candidates-jsonl", required=True)
    p.add_argument("--config", default="configs/campaign_default.yaml")
    p.add_argument("--runs-root", default="data/outputs/runs")
    p.add_argument("--solver-id", default="powermodels_ac_opf_ipopt_campaign")
    p.add_argument("--timeout-s", type=float, default=1200.0)
    p.add_argument("--max-candidates", type=int, default=0)
    p.add_argument("--continue-on-error", action="store_true")
    p.add_argument(
        "--max-failure-fraction",
        type=float,
        default=0.0,
        help="Round is marked not-ok when solve failures exceed this fraction of candidates.",
    )
    p.add_argument(
        "--resume",
        action="store_true",
        help="Skip candidates whose deterministic output directory already has a finalized attempt.",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    campaign_root = paths.campaign_root(repo_root, args.campaign_id)
    runs_root = (repo_root / args.runs_root).resolve()

    selected_path = Path(args.selected_candidates_jsonl)
    selected_path = selected_path if selected_path.is_absolute() else (repo_root / selected_path).resolve()
    candidates = _read_jsonl(selected_path)
    if args.max_candidates > 0:
        candidates = candidates[: args.max_candidates]

    adapter = PowerModelsAdapter(repo_root=repo_root)
    bands = _load_bands(repo_root, args.config)

    existing_diversity = _read_existing_diversity(campaign_root)
    active_ledger_rows = []

    solved_rows: list[dict[str, Any]] = []
    diversity_rows: list[dict[str, Any]] = []
    boundary_rows: list[dict[str, Any]] = []
    failed_rows: list[dict[str, Any]] = []
    skipped_rows: list[dict[str, Any]] = []

    done_ids = _loaded_sample_ids(runs_root) if args.resume else set()

    # One open handle for the whole shard instead of reopening per sample.
    sink = SampleSink(runs_root, args.solver_id)

    for cand in candidates:
        case_id = str(cand.get("case_id"))
        case_family = grid_family_for(repo_root, case_id)
        case_dataset = dataset_for(repo_root, case_id)
        cand.setdefault("grid_family", case_family)
        cand.setdefault("dataset", case_dataset)
        try:
            if args.resume and str(cand.get("candidate_id")) in done_ids:
                skipped_rows.append(
                    {
                        "candidate_id": cand.get("candidate_id"),
                        "grid_family": case_family,
                        "dataset": case_dataset,
                    }
                )
                continue
            case_file = _resolve_case_file(repo_root, case_id)
            case_data = parse_matpower_case(case_file, case_id)
            case_data = apply_topology(case_data, cand.get("switched_off_branches"))
            op_params = dict(cand.get("operating_point_parameters", {}))
            snapshot_id = op_params.get("load_snapshot_id")
            if snapshot_id:
                bus_load = get_snapshot_bus_loads(repo_root, case_id, str(snapshot_id))
                if bus_load:
                    op_params["_load_snapshot_map"] = bus_load
            case_data = apply_operating_point(case_data, op_params)
            case_data = apply_contingency(case_data, cand.get("contingency"))

            result = adapter.solve_ac_opf(case_data, options={"timeout_s": args.timeout_s})
            samples_path, run_id = sink.append(cand, case_data, result)
            result["_run_id"] = run_id

            margins = _build_margins(case_data, result)
            active_sig = build_active_constraint_signature(margins)
            sec_margin = compute_security_margin(margins)
            sec_band = classify_security_margin_band(sec_margin, bands)

            desc = _descriptor_from_result(cand, case_data, result, sec_margin, active_sig)
            dup_status, dmin = classify_duplicate_status(desc, existing_diversity)
            desc["duplicate_status"] = dup_status
            desc["nearest_neighbor_distance"] = dmin
            desc["training_weight_recommendation"] = 0.3 if dup_status == "near_duplicate" else 1.0
            desc["attempt_dir"] = str(samples_path)
            desc["round_index"] = args.round_index
            desc["grid_family"] = case_family
            desc["dataset"] = case_dataset
            desc["topology_id"] = cand.get("topology_id", "topology_000000_baseline")
            desc["switched_branch_count"] = int(cand.get("switched_branch_count", 0))

            existing_diversity.append(desc)
            diversity_rows.append(desc)

            active_ledger_rows = update_active_constraint_ledger(
                active_ledger_rows,
                {
                    "active_constraint_signature": active_sig,
                    "round_index": args.round_index,
                },
            )

            boundary_rows.append(
                {
                    "stress_trajectory_id": f"{cand.get('candidate_id')}::trajectory",
                    "base_operating_point": cand.get("candidate_id"),
                    "stress_direction": cand.get("candidate_generation_mechanism", "unknown"),
                    "secure_endpoint": None,
                    "insecure_endpoint": None,
                    "boundary_estimate": sec_margin,
                    "margin_band": sec_band,
                    "limiting_constraint": min(margins, key=lambda k: margins[k]) if margins else "unknown",
                    "related_run_ids": [run_id],
                }
            )

            solved_rows.append(
                {
                    "candidate_id": cand.get("candidate_id"),
                    "run_id": run_id,
                    "attempt_dir": str(samples_path),
                    "grid_family": case_family,
                    "dataset": case_dataset,
                    "topology_id": cand.get("topology_id", "topology_000000_baseline"),
                    "topology_class": cand.get("topology_class", "baseline"),
                    "success": bool(result.get("success", False)),
                    "termination_status": result.get("termination_status"),
                    "objective": result.get("objective"),
                    "runtime": result.get("solve_time", result.get("runtime")),
                    "wallclock_seconds": (result.get("runtime_metadata") or {}).get("wallclock_seconds"),
                    "mpi_processes": ((result.get("runtime_metadata") or {}).get("execution_context") or {}).get("mpi_processes"),
                    "gpu_enabled": ((result.get("runtime_metadata") or {}).get("execution_context") or {}).get("gpu_enabled"),
                    "gpu_type": ((result.get("runtime_metadata") or {}).get("execution_context") or {}).get("gpu_type"),
                    "security_margin": sec_margin,
                    "security_margin_band": sec_band,
                }
            )
        except Exception as exc:  # noqa: BLE001
            failed_rows.append(
                {
                    "candidate_id": cand.get("candidate_id"),
                    "grid_family": case_family,
                    "dataset": case_dataset,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            if not args.continue_on_error:
                break

    sink.close()

    append_parquet_rows(campaign_root / "diversity_ledger.parquet", diversity_rows)
    append_parquet_rows(campaign_root / "active_constraint_ledger.parquet", active_ledger_rows)
    append_parquet_rows(campaign_root / "security_boundary_ledger.parquet", boundary_rows)
    append_parquet_rows(campaign_root / "contingency_portfolio.parquet", candidates)

    total_candidates = len(candidates)
    solvable_candidates = total_candidates - len(skipped_rows)
    failure_fraction = (len(failed_rows) / solvable_candidates) if solvable_candidates else 0.0
    round_ok = failure_fraction <= args.max_failure_fraction

    out_report = {
        "ok": round_ok,
        "campaign_id": args.campaign_id,
        "round_index": args.round_index,
        "input_candidate_count": len(candidates),
        "solved_count": len(solved_rows),
        "failed_count": len(failed_rows),
        "skipped_count": len(skipped_rows),
        "resume": bool(args.resume),
        "failure_fraction": round(failure_fraction, 6),
        "max_failure_fraction": args.max_failure_fraction,
        "solved": solved_rows,
        "failed": failed_rows,
        "updated_ledgers": [
            str(campaign_root / "diversity_ledger.parquet"),
            str(campaign_root / "active_constraint_ledger.parquet"),
            str(campaign_root / "security_boundary_ledger.parquet"),
            str(campaign_root / "contingency_portfolio.parquet"),
        ],
    }

    report_path = campaign_root / "round_summaries" / f"round_{args.round_index:03d}_ac_execution_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(out_report, indent=2), encoding="utf-8")

    _write_shard_manifest(
        runs_root,
        {
            "campaign_id": args.campaign_id,
            "round_index": args.round_index,
            "solved_count": len(solved_rows),
            "failed_count": len(failed_rows),
            "skipped_count": len(skipped_rows),
        },
    )

    print(json.dumps({"ok": out_report["ok"], "report": str(report_path), "skipped_count": len(skipped_rows)}, indent=2))
    if not out_report["ok"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
