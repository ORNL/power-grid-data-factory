#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from grid_data_factory.boundaries.security_margin import classify_security_margin_band, compute_security_margin
    from grid_data_factory.campaigns.ledgers import append_parquet_rows
    from grid_data_factory.constraints.active_sets import build_active_constraint_signature
    from grid_data_factory.constraints.coverage_ledger import update_active_constraint_ledger
    from grid_data_factory.contingencies.apply import apply_contingency
    from grid_data_factory.diversity.clustering import adaptive_bin_id
    from grid_data_factory.diversity.descriptors import SolvedStateDescriptor
    from grid_data_factory.diversity.duplicate_detection import classify_duplicate_status
    from grid_data_factory.parsers.matpower import parse_matpower_case
    from grid_data_factory.preservation.artifacts import build_artifacts_manifest
    from grid_data_factory.preservation.checksums import verify_checksums, write_checksums
    from grid_data_factory.solvers.powermodels_adapter import PowerModelsAdapter
    from grid_data_factory.sources.registry import dataset_for, grid_family_for, resolve_case_file
    from grid_data_factory.topology.generation import apply_topology
    from grid_data_factory.scenarios.load_snapshots import get_snapshot_bus_loads
    from grid_data_factory.scenarios.operating_points import apply_operating_point
    from grid_data_factory.storage.layout import create_attempt_directory, finalize_attempt_directory, get_solver_directory
    from grid_data_factory.storage.naming import format_attempt_id
except ModuleNotFoundError:
    _repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(_repo_root / "src"))
    from grid_data_factory.boundaries.security_margin import classify_security_margin_band, compute_security_margin
    from grid_data_factory.campaigns.ledgers import append_parquet_rows
    from grid_data_factory.constraints.active_sets import build_active_constraint_signature
    from grid_data_factory.constraints.coverage_ledger import update_active_constraint_ledger
    from grid_data_factory.contingencies.apply import apply_contingency
    from grid_data_factory.diversity.clustering import adaptive_bin_id
    from grid_data_factory.diversity.descriptors import SolvedStateDescriptor
    from grid_data_factory.diversity.duplicate_detection import classify_duplicate_status
    from grid_data_factory.parsers.matpower import parse_matpower_case
    from grid_data_factory.preservation.artifacts import build_artifacts_manifest
    from grid_data_factory.preservation.checksums import verify_checksums, write_checksums
    from grid_data_factory.solvers.powermodels_adapter import PowerModelsAdapter
    from grid_data_factory.sources.registry import dataset_for, grid_family_for, resolve_case_file
    from grid_data_factory.topology.generation import apply_topology
    from grid_data_factory.scenarios.load_snapshots import get_snapshot_bus_loads
    from grid_data_factory.scenarios.operating_points import apply_operating_point
    from grid_data_factory.storage.layout import create_attempt_directory, finalize_attempt_directory, get_solver_directory
    from grid_data_factory.storage.naming import format_attempt_id


def _require_yaml():
    try:
        import yaml  # type: ignore
    except ModuleNotFoundError:
        return None
    return yaml


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run selected adaptive-campaign candidates through AC-OPF and update post-solve ledgers.")
    p.add_argument("--campaign-id", required=True)
    p.add_argument("--round-index", type=int, required=True)
    p.add_argument("--selected-candidates-jsonl", required=True)
    p.add_argument("--config", default="configs/campaign_default.yaml")
    p.add_argument("--runs-root", default="data/runs")
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
    return p.parse_args()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _normalize(text: str) -> str:
    t = text.strip().lower().replace(" ", "_")
    return re.sub(r"[^a-z0-9_\-]", "", t)


def _next_attempt_index(solver_dir: Path) -> int:
    attempts_dir = solver_dir / "attempts"
    if not attempts_dir.exists():
        return 1
    max_idx = 0
    for p in attempts_dir.iterdir():
        name = p.name
        if name.startswith(".attempt_") and name.endswith(".in_progress"):
            core = name[len(".attempt_") : -len(".in_progress")]
        elif name.startswith("attempt_"):
            core = name[len("attempt_") :]
        else:
            continue
        if core.isdigit():
            max_idx = max(max_idx, int(core))
    return max_idx + 1


def _resolve_case_file(repo_root: Path, case_id: str) -> Path:
    return resolve_case_file(repo_root, case_id)


def _quantiles(values: list[float]) -> tuple[float, float, float]:
    if not values:
        return (0.0, 0.0, 0.0)
    vals = sorted(values)

    def pick(q: float) -> float:
        idx = int(round((len(vals) - 1) * q))
        return float(vals[max(0, min(len(vals) - 1, idx))])

    return pick(0.1), pick(0.5), pick(0.9)


def _build_margins(case_data: dict[str, Any], raw_result: dict[str, Any]) -> dict[str, float]:
    margins: dict[str, float] = {}
    solution = ((raw_result.get("raw_result") or {}).get("solution") or {})

    gen_sol = solution.get("gen") or {}
    for i, gen in enumerate(case_data.get("generators", []), start=1):
        gid = str(gen.get("gen_id", f"gen_{i:06d}"))
        s = gen_sol.get(str(i), {})
        pg = float(s.get("pg", 0.0)) * float(case_data.get("base_mva", 100.0))
        qg = float(s.get("qg", 0.0)) * float(case_data.get("base_mva", 100.0))
        pmin, pmax = float(gen["pmin"]), float(gen["pmax"])
        qmin, qmax = float(gen["qmin"]), float(gen["qmax"])
        margins[f"generator_pmin:{gid}"] = (pg - pmin) / max(abs(pmax), 1.0)
        margins[f"generator_pmax:{gid}"] = (pmax - pg) / max(abs(pmax), 1.0)
        margins[f"generator_qmin:{gid}"] = (qg - qmin) / max(abs(qmax), 1.0)
        margins[f"generator_qmax:{gid}"] = (qmax - qg) / max(abs(qmax), 1.0)

    bus_sol = solution.get("bus") or {}
    for i, bus in enumerate(case_data.get("buses", []), start=1):
        bid = str(bus.get("bus_id", i))
        s = bus_sol.get(str(i), {})
        vm = float(s.get("vm", bus.get("vm", 1.0)))
        vmin = float(bus.get("vmin", 0.95))
        vmax = float(bus.get("vmax", 1.05))
        margins[f"voltage_min:{bid}"] = vm - vmin
        margins[f"voltage_max:{bid}"] = vmax - vm

    branch_sol = solution.get("branch") or {}
    for i, br in enumerate(case_data.get("branches", []), start=1):
        brid = str(br.get("branch_id", f"branch_{i:06d}"))
        s = branch_sol.get(str(i), {})
        pf = abs(float(s.get("pf", 0.0))) * float(case_data.get("base_mva", 100.0))
        pt = abs(float(s.get("pt", 0.0))) * float(case_data.get("base_mva", 100.0))
        flow = max(pf, pt)
        rate = max(float(br.get("rate_a", 1.0)), 1.0)
        margins[f"branch_thermal:{brid}"] = (rate - flow) / rate

    return margins


def _descriptor_from_result(candidate: dict[str, Any], case_data: dict[str, Any], result: dict[str, Any], security_margin: float, active_sig: dict[str, int]) -> dict[str, Any]:
    base_mva = float(case_data.get("base_mva", 100.0))
    solution = ((result.get("raw_result") or {}).get("solution") or {})

    total_p = sum(float(x.get("pd", 0.0)) for x in case_data.get("loads", []))
    total_q = sum(float(x.get("qd", 0.0)) for x in case_data.get("loads", []))

    bus_vals = [float(v.get("vm", 1.0)) for v in (solution.get("bus") or {}).values()]
    branch_vals = []
    for v in (solution.get("branch") or {}).values():
        pf = abs(float(v.get("pf", 0.0))) * base_mva
        pt = abs(float(v.get("pt", 0.0))) * base_mva
        branch_vals.append(max(pf, pt))

    gen_p = [abs(float(v.get("pg", 0.0)) * base_mva) for v in (solution.get("gen") or {}).values()]
    gen_q = [abs(float(v.get("qg", 0.0)) * base_mva) for v in (solution.get("gen") or {}).values()]

    v10, v50, v90 = _quantiles(bus_vals)
    b10, b50, b90 = _quantiles(branch_vals)
    p10, p50, p90 = _quantiles(gen_p)
    q10, q50, q90 = _quantiles(gen_q)

    active_keys = sorted([k for k, s in active_sig.items() if s == 2])
    near_keys = sorted([k for k, s in active_sig.items() if s == 1])

    desc = SolvedStateDescriptor(
        candidate_id=str(candidate.get("candidate_id")),
        total_active_load=total_p,
        total_reactive_load=total_q,
        renewable_penetration=float(candidate.get("operating_point_parameters", {}).get("renewable_scale", 1.0)),
        reserve_margin=float(candidate.get("operating_point_parameters", {}).get("reserve_margin", 0.15)),
        voltage_p10=v10,
        voltage_p50=v50,
        voltage_p90=v90,
        branch_loading_p90=b90,
        generator_p_p90=p90,
        generator_q_p90=q90,
        network_losses=0.0,
        active_constraint_signature=";".join(active_keys),
        near_active_constraint_signature=";".join(near_keys),
        active_constraint_count=len(active_keys),
        near_active_constraint_count=len(near_keys),
        contingency_order=int(candidate.get("contingency_order", 0)),
        topology_class=str(candidate.get("topology_class", "baseline")),
        security_margin=security_margin,
    ).to_record()

    desc["descriptor_cluster"] = adaptive_bin_id(desc)
    desc["candidate_generation_mechanism"] = candidate.get("candidate_generation_mechanism")
    desc["run_id"] = result.get("_run_id")
    return desc


def _read_existing_diversity(campaign_root: Path) -> list[dict[str, Any]]:
    parquet = campaign_root / "diversity_ledger.parquet"
    fallback = campaign_root / "diversity_ledger.parquet.jsonl"
    if fallback.exists():
        return _read_jsonl(fallback)
    try:
        import pandas as pd  # type: ignore
    except ModuleNotFoundError:
        return []
    if parquet.exists():
        return pd.read_parquet(parquet).to_dict(orient="records")
    return []


def _write_attempt(
    repo_root: Path,
    runs_root: Path,
    candidate: dict[str, Any],
    case_data: dict[str, Any],
    result: dict[str, Any],
    solver_id: str,
) -> tuple[Path, str]:
    case_id = str(candidate.get("case_id"))
    regime = _normalize(str(candidate.get("operating_regime", "baseline")))

    cid = str(candidate.get("candidate_id", "c0"))
    m = re.search(r"::op::(\d+)", cid)
    op_index = int(m.group(1)) if m else 0
    operating_point_id = f"op_{op_index:06d}_{regime}"
    topology_id = str(candidate.get("topology_id") or "topology_000000_baseline")

    solver_dir = get_solver_directory(
        runs_root=runs_root,
        task="ac_opf",
        case_id=case_id,
        topology_id=topology_id,
        operating_point_id=operating_point_id,
        solver_id=solver_id,
    )
    attempt_id = format_attempt_id(_next_attempt_index(solver_dir))
    in_progress = create_attempt_directory(solver_dir, attempt_id)

    run_id = f"{case_id}-{topology_id}-{operating_point_id}-{solver_id}-{attempt_id}"
    run_yaml = {
        "run_id": run_id,
        "task": "ac_opf",
        "case_id": case_id,
        "topology_id": topology_id,
        "operating_point_id": operating_point_id,
        "contingency_set_id": None,
        "solver_id": solver_id,
        "attempt_id": attempt_id,
        "numerical_status": str(result.get("termination_status", "unknown")),
        "preservation_status": "in_progress",
    }

    runtime_meta = result.get("runtime_metadata") or {}
    exec_ctx = runtime_meta.get("execution_context") or {}
    wallclock_seconds = runtime_meta.get("wallclock_seconds", result.get("solve_time", result.get("runtime")))

    (in_progress / "run.yaml").write_text("\n".join(f"{k}: {v}" for k, v in run_yaml.items()) + "\n", encoding="utf-8")
    (in_progress / "inputs" / "resolved_case.json").write_text(json.dumps(case_data, indent=2), encoding="utf-8")
    (in_progress / "inputs" / "candidate.json").write_text(json.dumps(candidate, indent=2), encoding="utf-8")
    (in_progress / "raw_outputs" / "solver_result" / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    (in_progress / "timing" / "runtime_metadata.json").write_text(json.dumps(runtime_meta, indent=2), encoding="utf-8")
    (in_progress / "logs" / "stdout.log").write_text(str(result.get("stdout", "")), encoding="utf-8")
    (in_progress / "logs" / "stderr.log").write_text(str(result.get("stderr", "")), encoding="utf-8")
    (in_progress / "logs" / "combined.log").write_text(
        f"termination_status={result.get('termination_status')}\n"
        f"success={result.get('success')}\n"
        f"objective={result.get('objective')}\n",
        encoding="utf-8",
    )
    (in_progress / "logs" / "combined.log").write_text(
        (in_progress / "logs" / "combined.log").read_text(encoding="utf-8")
        + f"wallclock_seconds={wallclock_seconds}\n"
        + f"mpi_processes={exec_ctx.get('mpi_processes')}\n"
        + f"gpu_enabled={exec_ctx.get('gpu_enabled')}\n"
        + f"gpu_type={exec_ctx.get('gpu_type')}\n",
        encoding="utf-8",
    )

    build_artifacts_manifest(in_progress)
    write_checksums(in_progress)
    marker = "SUCCESS" if bool(result.get("success", False)) else "NONCONVERGENT"
    (in_progress / marker).write_text("", encoding="utf-8")
    final_dir = finalize_attempt_directory(in_progress)

    ok, errors = verify_checksums(final_dir)
    if not ok:
        raise RuntimeError(f"Checksum verification failed for {final_dir}: {errors}")

    return final_dir, run_id


def _load_bands(repo_root: Path, config_path: str) -> dict[str, dict[str, float]]:
    p = (repo_root / config_path).resolve()
    yaml = _require_yaml()
    if yaml is None:
        return {
            "comfortably_secure": {"minimum_margin": 0.10},
            "moderately_secure": {"minimum_margin": 0.03, "maximum_margin": 0.10},
            "near_boundary_secure": {"minimum_margin": 0.0, "maximum_margin": 0.03},
            "near_boundary_insecure": {"minimum_margin": -0.03, "maximum_margin": 0.0},
            "severely_insecure": {"maximum_margin": -0.03},
        }
    cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return cfg.get("security_margin_bands") or {}


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    campaign_root = repo_root / "data" / "campaigns" / args.campaign_id
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

    for cand in candidates:
        case_id = str(cand.get("case_id"))
        case_family = grid_family_for(repo_root, case_id)
        case_dataset = dataset_for(repo_root, case_id)
        cand.setdefault("grid_family", case_family)
        cand.setdefault("dataset", case_dataset)
        try:
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
            final_dir, run_id = _write_attempt(repo_root, runs_root, cand, case_data, result, args.solver_id)
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
                    "attempt_dir": str(final_dir),
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

    append_parquet_rows(campaign_root / "diversity_ledger.parquet", diversity_rows)
    append_parquet_rows(campaign_root / "active_constraint_ledger.parquet", active_ledger_rows)
    append_parquet_rows(campaign_root / "security_boundary_ledger.parquet", boundary_rows)
    append_parquet_rows(campaign_root / "contingency_portfolio.parquet", candidates)

    total_candidates = len(candidates)
    failure_fraction = (len(failed_rows) / total_candidates) if total_candidates else 0.0
    round_ok = failure_fraction <= args.max_failure_fraction

    out_report = {
        "ok": round_ok,
        "campaign_id": args.campaign_id,
        "round_index": args.round_index,
        "input_candidate_count": len(candidates),
        "solved_count": len(solved_rows),
        "failed_count": len(failed_rows),
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

    print(json.dumps({"ok": out_report["ok"], "report": str(report_path)}, indent=2))
    if not out_report["ok"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
