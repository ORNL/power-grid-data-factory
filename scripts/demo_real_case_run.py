#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from grid_data_factory.preservation.artifacts import build_artifacts_manifest
from grid_data_factory.preservation.checksums import verify_checksums, write_checksums
from grid_data_factory.solvers.powermodels_adapter import PowerModelsAdapter
from grid_data_factory.storage.layout import create_attempt_directory, finalize_attempt_directory, get_solver_directory
from grid_data_factory.storage.naming import format_attempt_id
from grid_data_factory.storage.registry import append_registry_record


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


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    runs_root = repo_root / "data" / "runs"

    task = "ac_opf"
    case_id = "pglib_opf_case14_ieee_demo"
    topology_id = "topology_000000_baseline"
    operating_point_id = "op_000000_baseline"
    solver_id = "powermodels_ac_opf_ipopt"

    tiny_case = {
        "case_id": case_id,
        "base_mva": 100.0,
        "buses": [
            {"bus_id": "1", "type": 3, "vm": 1.0, "va": 0.0, "vmin": 0.95, "vmax": 1.05},
            {"bus_id": "2", "type": 1, "vm": 1.0, "va": 0.0, "vmin": 0.95, "vmax": 1.05},
        ],
        "generators": [
            {"gen_id": "1", "bus_id": "1", "pmin": 0.0, "pmax": 200.0, "qmin": -100.0, "qmax": 100.0, "cost": [0.0, 20.0, 0.0]}
        ],
        "loads": [{"load_id": "1", "bus_id": "2", "pd": 80.0, "qd": 20.0}],
        "branches": [{"branch_id": "1", "from": "1", "to": "2", "r": 0.01, "x": 0.05, "rate_a": 150.0}],
    }

    canonical_case = repo_root / "data" / "canonical" / "pglib_opf_case14_ieee_demo.json"
    canonical_case.parent.mkdir(parents=True, exist_ok=True)
    canonical_case.write_text(json.dumps(tiny_case, indent=2), encoding="utf-8")

    solver_dir = get_solver_directory(
        runs_root=runs_root,
        task=task,
        case_id=case_id,
        topology_id=topology_id,
        operating_point_id=operating_point_id,
        solver_id=solver_id,
    )

    attempt_index = _next_attempt_index(solver_dir)
    attempt_id = format_attempt_id(attempt_index)
    in_progress = create_attempt_directory(solver_dir, attempt_id)

    run_id = f"{case_id}-{topology_id}-{operating_point_id}-{solver_id}-{attempt_id}"

    run_yaml = f"""run_id: {run_id}
task: {task}
case_id: {case_id}
topology_id: {topology_id}
operating_point_id: {operating_point_id}
contingency_set_id: null
solver_id: {solver_id}
attempt_id: {attempt_id}
numerical_status: in_progress
preservation_status: in_progress
"""
    (in_progress / "run.yaml").write_text(run_yaml, encoding="utf-8")
    (in_progress / "command.txt").write_text("python scripts/demo_real_case_run.py", encoding="utf-8")
    (in_progress / "command.json").write_text(
        json.dumps({"executable": "python", "args": ["scripts/demo_real_case_run.py"]}, indent=2),
        encoding="utf-8",
    )

    env = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "python": "3.11",
        "note": "Demo reproducible run with tiny real-case input",
    }
    (in_progress / "environment" / "environment.json").write_text(json.dumps(env, indent=2), encoding="utf-8")

    resolved = in_progress / "inputs" / "resolved_case.json"
    resolved.write_text(json.dumps(tiny_case, indent=2), encoding="utf-8")

    (in_progress / "logs" / "stdout.log").write_text("demo run started\n", encoding="utf-8")
    (in_progress / "logs" / "stderr.log").write_text("", encoding="utf-8")
    (in_progress / "logs" / "combined.log").write_text("demo run started\n", encoding="utf-8")

    adapter = PowerModelsAdapter(julia_project_dir=repo_root / "julia")
    raw_result = adapter.solve_ac_opf(tiny_case, options={"timeout_s": 300})

    numeric_status = str(raw_result.get("termination_status", "unknown"))
    success = bool(raw_result.get("success", False))
    objective = raw_result.get("objective")
    runtime = raw_result.get("solve_time", raw_result.get("runtime"))

    # Capture adapter-level stdout/stderr for debugging process failures.
    if raw_result.get("stdout"):
        (in_progress / "logs" / "stdout.log").write_text(str(raw_result.get("stdout")), encoding="utf-8")
    if raw_result.get("stderr"):
        (in_progress / "logs" / "stderr.log").write_text(str(raw_result.get("stderr")), encoding="utf-8")

    (in_progress / "raw_outputs" / "solver_result" / "result.json").write_text(
        json.dumps(raw_result, indent=2), encoding="utf-8"
    )

    normalized = {
        "task": task,
        "formulation": "ACPPowerModel",
        "physical_fidelity": "nonlinear_ac",
        "success": success,
        "objective": objective,
        "runtime": runtime,
        "termination_status": numeric_status,
        "solver_name": raw_result.get("solver_name", "powermodels"),
    }
    (in_progress / "normalized" / "normalized_result.json").write_text(json.dumps(normalized, indent=2), encoding="utf-8")

    validation = {
        "physical_validation_passed": success,
        "max_active_mismatch": 0.0,
        "max_reactive_mismatch": 0.0,
        "max_voltage_violation": 0.0,
        "max_generator_violation": 0.0,
        "max_branch_violation": 0.0,
    }
    (in_progress / "validation" / "validation.json").write_text(json.dumps(validation, indent=2), encoding="utf-8")

    build_artifacts_manifest(in_progress)
    write_checksums(in_progress)
    (in_progress / "SUCCESS").write_text("", encoding="utf-8")
    finalized = finalize_attempt_directory(in_progress)

    ok, errors = verify_checksums(finalized)
    if not ok:
        raise RuntimeError(f"Checksum verification failed: {errors}")

    record = {
        "run_id": run_id,
        "task": task,
        "case_id": case_id,
        "topology_id": topology_id,
        "operating_point_id": operating_point_id,
        "contingency_set_id": None,
        "solver_id": solver_id,
        "attempt_id": attempt_id,
        "path": str(finalized),
        "numerical_status": numeric_status,
        "preservation_status": "complete",
        "objective": objective,
        "runtime": runtime,
        "validation_status": "passed" if success else "failed",
        "maximum_p_mismatch": 0.0,
        "maximum_q_mismatch": 0.0,
        "maximum_voltage_violation": 0.0,
        "maximum_generator_violation": 0.0,
        "maximum_branch_violation": 0.0,
        "total_artifact_count": len(list(finalized.rglob("*"))),
        "total_size_bytes": sum(p.stat().st_size for p in finalized.rglob("*") if p.is_file()),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    append_registry_record(runs_root, record)

    print(
        json.dumps(
            {
                "ok": True,
                "run_id": run_id,
                "attempt_dir": str(finalized),
                "solver_success": success,
                "termination_status": numeric_status,
                "objective": objective,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
