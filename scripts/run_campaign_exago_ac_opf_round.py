#!/usr/bin/env python3
"""ExaGO map-stage worker for the adaptive campaign.

Mirrors scripts/run_campaign_ac_opf_round.py (the live PowerModels/Andes worker)
but solves each selected candidate with ExaGO OPFLOW on AMD GPUs. The ledger,
attempt-directory, resume and reporting contracts are reused verbatim from the
PowerModels worker so the reduce/drive stages need no changes.

Per candidate the GPU sparse solver is tried first
(-opflow_solver HIOPSPARSEGPU -opflow_model PBPOLRAJAHIOPSPARSE); on any
failure/non-convergence it falls back to the CPU interior-point solver
(-opflow_solver IPOPT -opflow_model POWER_BALANCE_POLAR).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPTS_DIR.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

# Single source of truth for the ledger / attempt / descriptor contract.
from grid_data_factory.campaigns import round_runner as base  # noqa: E402

from grid_data_factory.solvers.exago_adapter import resolve_opflow_bin, run_exago_case  # noqa: E402

from grid_data_factory.boundaries.security_margin import (  # noqa: E402
    classify_security_margin_band,
    compute_security_margin,
)
from grid_data_factory.campaigns.ledgers import append_parquet_rows  # noqa: E402
from grid_data_factory.constraints.active_sets import build_active_constraint_signature  # noqa: E402
from grid_data_factory.constraints.coverage_ledger import update_active_constraint_ledger  # noqa: E402
from grid_data_factory.contingencies.apply import apply_contingency  # noqa: E402
from grid_data_factory.diversity.duplicate_detection import classify_duplicate_status  # noqa: E402
from grid_data_factory.parsers.matpower import parse_matpower_case, write_matpower_case  # noqa: E402
from grid_data_factory.scenarios.load_snapshots import get_snapshot_bus_loads  # noqa: E402
from grid_data_factory.storage import paths  # noqa: E402
from grid_data_factory.scenarios.operating_points import apply_operating_point  # noqa: E402
from grid_data_factory.sources.registry import dataset_for, grid_family_for  # noqa: E402
from grid_data_factory.storage.layout import has_finalized_attempt  # noqa: E402
from grid_data_factory.topology.generation import apply_topology  # noqa: E402

GPU_SOLVER = "HIOPSPARSEGPU"
GPU_MODEL = "PBPOLRAJAHIOPSPARSE"
CPU_SOLVER = "IPOPT"
CPU_MODEL = "POWER_BALANCE_POLAR"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run selected adaptive-campaign candidates through ExaGO OPFLOW (GPU) and update post-solve ledgers.")
    p.add_argument("--campaign-id", required=True)
    p.add_argument("--round-index", type=int, required=True)
    p.add_argument("--selected-candidates-jsonl", required=True)
    p.add_argument("--config", default="configs/campaign_default.yaml")
    p.add_argument("--runs-root", default="data/outputs/runs")
    p.add_argument("--solver-id", default="exago_ac_opf_hiopsparsegpu_campaign")
    p.add_argument("--timeout-s", type=float, default=1800.0)
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
    # ExaGO binary resolution (mirrors scripts/run_exago_ac_opf.py).
    p.add_argument("--exago-root", default="external/ExaGO")
    p.add_argument("--exago-install", default=os.environ.get("PGDF_EXAGO_INSTALL_PREFIX", ""))
    p.add_argument("--build-profile", default=os.environ.get("PGDF_EXAGO_BUILD_PROFILE", ""))
    p.add_argument("--opflow-bin", default=os.environ.get("PGDF_EXAGO_OPFLOW_BIN", ""))
    p.add_argument(
        "--solver-mode",
        default="gpu_then_ipopt",
        choices=["gpu_then_ipopt", "ipopt_only", "gpu_only"],
        help="gpu_then_ipopt: try GPU sparse, fall back to IPOPT; ipopt_only / gpu_only force a single solver.",
    )
    return p.parse_args()


def _solve_candidate_exago(
    exago_root: Path,
    opflow_bin: Path,
    case_data: dict[str, Any],
    solver_mode: str,
    timeout_s: float,
    tmp_dir: Path,
    tag: str,
) -> dict[str, Any]:
    """Write the transformed case to a temp .m file and solve with ExaGO.

    GPU sparse is attempted first (when requested); IPOPT is used as fallback or
    when explicitly forced. Returns a PowerModels-compatible result dict with an
    added ``solver_attempts`` audit trail.
    """
    tmp_m = tmp_dir / f"{tag}.m"
    write_matpower_case(case_data, tmp_m, case_name=tag)

    attempts: list[dict[str, Any]] = []
    result: dict[str, Any] | None = None

    if solver_mode in ("gpu_then_ipopt", "gpu_only"):
        gpu = run_exago_case(
            exago_root, opflow_bin, str(tmp_m), GPU_SOLVER, GPU_MODEL, timeout_s,
            export_base=tmp_dir / f"{tag}_gpu",
        )
        gpu["solver_used"] = GPU_SOLVER
        gpu["opflow_model_used"] = GPU_MODEL
        gpu["fallback_used"] = False
        attempts.append({
            "solver": GPU_SOLVER,
            "model": GPU_MODEL,
            "success": bool(gpu.get("success")),
            "termination_status": gpu.get("termination_status"),
            "exit_code": gpu.get("exit_code"),
        })
        if bool(gpu.get("success")) or solver_mode == "gpu_only":
            result = gpu

    if result is None:
        cpu = run_exago_case(
            exago_root, opflow_bin, str(tmp_m), CPU_SOLVER, CPU_MODEL, timeout_s,
            export_base=tmp_dir / f"{tag}_ipopt",
        )
        cpu["solver_used"] = CPU_SOLVER
        cpu["opflow_model_used"] = CPU_MODEL
        cpu["fallback_used"] = solver_mode == "gpu_then_ipopt"
        attempts.append({
            "solver": CPU_SOLVER,
            "model": CPU_MODEL,
            "success": bool(cpu.get("success")),
            "termination_status": cpu.get("termination_status"),
            "exit_code": cpu.get("exit_code"),
        })
        result = cpu

    result["solver_attempts"] = attempts
    try:
        tmp_m.unlink(missing_ok=True)
    except OSError:
        pass
    return result


def main() -> None:
    args = parse_args()
    repo_root = _REPO_ROOT
    campaign_root = paths.campaign_root(repo_root, args.campaign_id)
    runs_root = (repo_root / args.runs_root).resolve()

    exago_root = (repo_root / args.exago_root).resolve()
    opflow_bin = resolve_opflow_bin(
        repo_root,
        exago_root,
        opflow_bin=args.opflow_bin,
        exago_install=args.exago_install,
        build_profile=args.build_profile,
    )
    if not opflow_bin.exists():
        raise SystemExit(json.dumps({"ok": False, "error": "opflow_bin_missing", "opflow_bin": str(opflow_bin)}, indent=2))
    if not os.access(opflow_bin, os.X_OK):
        raise SystemExit(json.dumps({"ok": False, "error": "opflow_bin_not_executable", "opflow_bin": str(opflow_bin)}, indent=2))

    selected_path = Path(args.selected_candidates_jsonl)
    selected_path = selected_path if selected_path.is_absolute() else (repo_root / selected_path).resolve()
    candidates = base._read_jsonl(selected_path)
    if args.max_candidates > 0:
        candidates = candidates[: args.max_candidates]

    bands = base._load_bands(repo_root, args.config)
    existing_diversity = base._read_existing_diversity(campaign_root)
    active_ledger_rows: list[dict[str, Any]] = []

    solved_rows: list[dict[str, Any]] = []
    diversity_rows: list[dict[str, Any]] = []
    boundary_rows: list[dict[str, Any]] = []
    failed_rows: list[dict[str, Any]] = []
    skipped_rows: list[dict[str, Any]] = []

    tmp_root = (paths.tmp_dir(repo_root) / "exago_campaign").resolve()
    tmp_root.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(dir=str(tmp_root), prefix=f"{args.campaign_id}_") as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        for cand in candidates:
            case_id = str(cand.get("case_id"))
            case_family = grid_family_for(repo_root, case_id)
            case_dataset = dataset_for(repo_root, case_id)
            cand.setdefault("grid_family", case_family)
            cand.setdefault("dataset", case_dataset)
            try:
                if args.resume and has_finalized_attempt(base._candidate_solver_dir(runs_root, cand, args.solver_id)):
                    skipped_rows.append({"candidate_id": cand.get("candidate_id"), "grid_family": case_family, "dataset": case_dataset})
                    continue

                case_file = base._resolve_case_file(repo_root, case_id)
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

                tag = base._normalize(str(cand.get("candidate_id", "cand"))) or "cand"
                result = _solve_candidate_exago(
                    exago_root, opflow_bin, case_data, args.solver_mode, args.timeout_s, tmp_dir, tag,
                )

                final_dir, run_id = base._write_attempt(repo_root, runs_root, cand, case_data, result, args.solver_id)
                result["_run_id"] = run_id

                margins = base._build_margins(case_data, result)
                active_sig = build_active_constraint_signature(margins)
                sec_margin = compute_security_margin(margins)
                sec_band = classify_security_margin_band(sec_margin, bands)

                desc = base._descriptor_from_result(cand, case_data, result, sec_margin, active_sig)
                dup_status, dmin = classify_duplicate_status(desc, existing_diversity)
                desc["duplicate_status"] = dup_status
                desc["nearest_neighbor_distance"] = dmin
                desc["training_weight_recommendation"] = 0.3 if dup_status == "near_duplicate" else 1.0
                desc["attempt_dir"] = str(final_dir)
                desc["round_index"] = args.round_index
                desc["grid_family"] = case_family
                desc["dataset"] = case_dataset
                desc["topology_id"] = cand.get("topology_id", "topology_000000_baseline")
                desc["switched_branch_count"] = int(cand.get("switched_branch_count", 0))

                existing_diversity.append(desc)
                diversity_rows.append(desc)

                active_ledger_rows = update_active_constraint_ledger(
                    active_ledger_rows,
                    {"active_constraint_signature": active_sig, "round_index": args.round_index},
                )

                boundary_rows.append({
                    "stress_trajectory_id": f"{cand.get('candidate_id')}::trajectory",
                    "base_operating_point": cand.get("candidate_id"),
                    "stress_direction": cand.get("candidate_generation_mechanism", "unknown"),
                    "secure_endpoint": None,
                    "insecure_endpoint": None,
                    "boundary_estimate": sec_margin,
                    "margin_band": sec_band,
                    "limiting_constraint": min(margins, key=lambda k: margins[k]) if margins else "unknown",
                    "related_run_ids": [run_id],
                })

                runtime_meta = result.get("runtime_metadata") or {}
                exec_ctx = runtime_meta.get("execution_context") or {}
                solved_rows.append({
                    "candidate_id": cand.get("candidate_id"),
                    "run_id": run_id,
                    "attempt_dir": str(final_dir),
                    "grid_family": case_family,
                    "dataset": case_dataset,
                    "topology_id": cand.get("topology_id", "topology_000000_baseline"),
                    "topology_class": cand.get("topology_class", "baseline"),
                    "success": bool(result.get("success", False)),
                    "termination_status": result.get("termination_status"),
                    "objective": result.get("objective"),
                    "runtime": result.get("solve_time", result.get("runtime")),
                    "wallclock_seconds": runtime_meta.get("wallclock_seconds"),
                    "mpi_processes": exec_ctx.get("mpi_processes"),
                    "gpu_enabled": exec_ctx.get("gpu_enabled"),
                    "gpu_type": exec_ctx.get("gpu_type"),
                    "solver_used": result.get("solver_used"),
                    "fallback_used": result.get("fallback_used"),
                    "security_margin": sec_margin,
                    "security_margin_band": sec_band,
                })
            except Exception as exc:  # noqa: BLE001
                failed_rows.append({
                    "candidate_id": cand.get("candidate_id"),
                    "grid_family": case_family,
                    "dataset": case_dataset,
                    "error": f"{type(exc).__name__}: {exc}",
                })
                if not args.continue_on_error:
                    break

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
        "solver_backend": "exago",
        "solver_mode": args.solver_mode,
        "opflow_bin": str(opflow_bin),
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

    print(json.dumps({"ok": out_report["ok"], "report": str(report_path), "skipped_count": len(skipped_rows)}, indent=2))
    if not out_report["ok"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
