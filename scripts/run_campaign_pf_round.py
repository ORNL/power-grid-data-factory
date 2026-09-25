#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import yaml

try:
    from grid_data_factory.boundaries.security_margin import classify_security_margin_band, compute_security_margin
    from grid_data_factory.campaigns.ledgers import append_parquet_rows, create_campaign_layout
    from grid_data_factory.campaigns.round_runner import (
        SampleSink,
        _build_margins,
        _descriptor_from_result,
        _load_bands,
        _loaded_sample_ids,
        _pf_outcome_class,
        _read_existing_diversity,
        _read_jsonl,
        _write_shard_manifest,
    )
    from grid_data_factory.constraints.active_sets import build_active_constraint_signature
    from grid_data_factory.constraints.coverage_ledger import update_active_constraint_ledger
    from grid_data_factory.diversity.duplicate_detection import classify_duplicate_status
    from grid_data_factory.pf.controls import apply_response_policy
    from grid_data_factory.pf.schemas import PFCandidate
    from grid_data_factory.pf.validation import validate_anchor_consistency, validate_pf_result
    from grid_data_factory.solvers.powermodels_adapter import PowerModelsAdapter
    from grid_data_factory.storage import paths
except ModuleNotFoundError:
    _repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(_repo_root / "src"))
    from grid_data_factory.boundaries.security_margin import classify_security_margin_band, compute_security_margin
    from grid_data_factory.campaigns.ledgers import append_parquet_rows, create_campaign_layout
    from grid_data_factory.campaigns.round_runner import (
        SampleSink,
        _build_margins,
        _descriptor_from_result,
        _load_bands,
        _loaded_sample_ids,
        _pf_outcome_class,
        _read_existing_diversity,
        _read_jsonl,
        _write_shard_manifest,
    )
    from grid_data_factory.constraints.active_sets import build_active_constraint_signature
    from grid_data_factory.constraints.coverage_ledger import update_active_constraint_ledger
    from grid_data_factory.diversity.duplicate_detection import classify_duplicate_status
    from grid_data_factory.pf.controls import apply_response_policy
    from grid_data_factory.pf.schemas import PFCandidate
    from grid_data_factory.pf.validation import validate_anchor_consistency, validate_pf_result
    from grid_data_factory.solvers.powermodels_adapter import PowerModelsAdapter
    from grid_data_factory.storage import paths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run validated PF candidates with explicit controls and response policies.")
    parser.add_argument("--candidates-jsonl", required=True)
    parser.add_argument("--campaign-id", default="riker_pf_complement_v1")
    parser.add_argument("--round-index", type=int, default=0)
    parser.add_argument("--config", default="configs/pf_campaign_riker.yaml")
    parser.add_argument("--runs-root", default="data/outputs/runs/riker_pf_complement_v1")
    parser.add_argument("--solver-id", default="powermodels_pf_ipopt")
    parser.add_argument("--timeout-s", type=float, default=1200.0)
    parser.add_argument("--max-candidates", type=int, default=0)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--max-failure-fraction", type=float, default=0.25)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    candidate_path = Path(args.candidates_jsonl)
    candidate_path = candidate_path if candidate_path.is_absolute() else repo_root / candidate_path
    runs_root = Path(args.runs_root)
    runs_root = runs_root if runs_root.is_absolute() else repo_root / runs_root
    config_path = Path(args.config)
    config_path = config_path if config_path.is_absolute() else repo_root / config_path
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    validation_tolerances = config.get("validation") or {}
    security_bands = config.get("security_margin_bands") or _load_bands(repo_root, "configs/campaign_default.yaml")
    campaign_root = paths.campaign_root(repo_root, args.campaign_id)
    create_campaign_layout(campaign_root, config)
    raw_candidates = _read_jsonl(candidate_path)
    if args.max_candidates > 0:
        raw_candidates = raw_candidates[: args.max_candidates]
    candidates = [PFCandidate.model_validate(candidate).model_dump(mode="json") for candidate in raw_candidates]
    done = _loaded_sample_ids(runs_root, task="pf") if args.resume else set()
    report = {
        "task": "pf",
        "campaign_id": args.campaign_id,
        "round_index": args.round_index,
        "candidate_count": len(candidates),
        "solved": 0,
        "failed": 0,
        "skipped": 0,
    }
    existing_diversity = _read_existing_diversity(campaign_root)
    diversity_rows = []
    active_ledger_rows: list[dict] = []
    boundary_rows = []
    coverage_rows = []
    outcome_counts: Counter[str] = Counter()

    adapter = PowerModelsAdapter(repo_root=repo_root)
    with SampleSink(runs_root, args.solver_id, task="pf") as sink, adapter.persistent_pf_session(
        options={"timeout_s": args.timeout_s}
    ) as solver:
        for candidate in candidates:
            if candidate["candidate_id"] in done:
                report["skipped"] += 1
                continue
            try:
                case_data = candidate["resolved_case"]
                post_case, controls, response_metadata = apply_response_policy(
                    case_data,
                    candidate.get("contingency"),
                    candidate["controls"],
                    candidate["response_policy"],
                )
                candidate["response_metadata"] = response_metadata
                result = solver.solve_pf(post_case, controls=controls.model_dump(mode="json"))
                validation = validate_pf_result(post_case, controls, result, validation_tolerances)
                if candidate["control_distance_stratum"] == "exact_consistency":
                    consistency = validate_anchor_consistency(
                        candidate.get("source_ac_opf_solution") or {}, result, validation_tolerances
                    )
                    validation["anchor_consistency"] = consistency
                    validation["validation_passed"] = validation["validation_passed"] and consistency["passed"]
                    if not consistency["passed"]:
                        validation["reason"] = "anchor_consistency_failed"
                result["validation"] = validation
                result["validation_passed"] = validation["validation_passed"]
                _, run_id = sink.append(candidate, post_case, result)
                result["_run_id"] = run_id
                report["solved"] += 1
                outcome = _pf_outcome_class(result)
                outcome_counts[outcome] += 1
                if outcome != "converged_valid":
                    report["failed"] += 1

                if result.get("success"):
                    margins = _build_margins(post_case, result)
                    signature = build_active_constraint_signature(margins)
                    security_margin = compute_security_margin(margins)
                    descriptor = _descriptor_from_result(candidate, post_case, result, security_margin, signature)
                    duplicate_status, nearest_distance = classify_duplicate_status(descriptor, existing_diversity)
                    descriptor.update(
                        {
                            "round_index": args.round_index,
                            "control_distance": candidate["control_distance"],
                            "control_distance_stratum": candidate["control_distance_stratum"],
                            "response_policy_id": candidate["response_policy"]["policy_id"],
                            "duplicate_status": duplicate_status,
                            "nearest_neighbor_distance": nearest_distance,
                            "outcome_class": outcome,
                        }
                    )
                    diversity_rows.append(descriptor)
                    existing_diversity.append(descriptor)
                    active_ledger_rows = update_active_constraint_ledger(
                        active_ledger_rows,
                        {"active_constraint_signature": signature, "round_index": args.round_index},
                    )
                    boundary_rows.append(
                        {
                            "candidate_id": candidate["candidate_id"],
                            "round_index": args.round_index,
                            "control_distance_stratum": candidate["control_distance_stratum"],
                            "security_margin": security_margin,
                            "security_margin_band": classify_security_margin_band(
                                security_margin, security_bands
                            ),
                            "limiting_constraint": min(margins, key=margins.get) if margins else "unknown",
                            "outcome_class": outcome,
                        }
                    )
                coverage_rows.append(
                    {
                        "candidate_id": candidate["candidate_id"],
                        "round_index": args.round_index,
                        "dataset_split": candidate["dataset_split"],
                        "topology_class": candidate.get("topology_class", "intact"),
                        "control_distance_stratum": candidate["control_distance_stratum"],
                        "response_policy_id": candidate["response_policy"]["policy_id"],
                        "outcome_class": outcome,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                report["failed"] += 1
                if not args.continue_on_error:
                    raise
                print(json.dumps({"candidate_id": candidate["candidate_id"], "error": f"{type(exc).__name__}: {exc}"}), file=sys.stderr)

    append_parquet_rows(campaign_root / "candidate_registry.parquet", candidates)
    append_parquet_rows(campaign_root / "diversity_ledger.parquet", diversity_rows)
    append_parquet_rows(campaign_root / "active_constraint_ledger.parquet", active_ledger_rows)
    append_parquet_rows(campaign_root / "security_boundary_ledger.parquet", boundary_rows)
    append_parquet_rows(campaign_root / "pf_coverage_ledger.parquet", coverage_rows)
    report["outcome_counts"] = dict(outcome_counts)
    nonvalid_outcomes = sum(count for outcome, count in outcome_counts.items() if outcome != "converged_valid")
    exception_count = report["failed"] - nonvalid_outcomes
    attempted = report["solved"] + exception_count
    report["failure_fraction"] = report["failed"] / max(1, attempted)
    report["ok"] = report["failure_fraction"] <= args.max_failure_fraction
    manifest = _write_shard_manifest(runs_root, report, task="pf")
    report_path = campaign_root / "round_summaries" / f"round_{args.round_index:03d}_pf_execution_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**report, "manifest": str(manifest)}, indent=2))
    if not report["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()