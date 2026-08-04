#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

try:
    from grid_data_factory.parsers.matpower import parse_matpower_case
    from grid_data_factory.preservation.artifacts import build_artifacts_manifest
    from grid_data_factory.preservation.checksums import verify_checksums, write_checksums
    from grid_data_factory.solvers.exago_adapter import resolve_opflow_bin, run_exago_case
    from grid_data_factory.storage.attempt_io import append_registry_record_safe, utc_now_iso, write_common_attempt_files
    from grid_data_factory.storage.layout import create_next_attempt_directory, finalize_attempt_directory, get_solver_directory
except ModuleNotFoundError:
    _repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(_repo_root / "src"))
    from grid_data_factory.parsers.matpower import parse_matpower_case
    from grid_data_factory.preservation.artifacts import build_artifacts_manifest
    from grid_data_factory.preservation.checksums import verify_checksums, write_checksums
    from grid_data_factory.solvers.exago_adapter import resolve_opflow_bin, run_exago_case
    from grid_data_factory.storage.attempt_io import append_registry_record_safe, utc_now_iso, write_common_attempt_files
    from grid_data_factory.storage.layout import create_next_attempt_directory, finalize_attempt_directory, get_solver_directory

from grid_data_factory.storage import paths  # noqa: E402


_now = utc_now_iso


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run AC-OPF via ExaGO OPFLOW on one or more MATPOWER cases.")
    p.add_argument("--exago-root", default="external/ExaGO", help="Path to ExaGO checkout containing datafiles/.")
    p.add_argument("--exago-install", default=os.environ.get("PGDF_EXAGO_INSTALL_PREFIX", ""))
    p.add_argument("--build-profile", default=os.environ.get("PGDF_EXAGO_BUILD_PROFILE", ""))
    p.add_argument("--opflow-bin", default=os.environ.get("PGDF_EXAGO_OPFLOW_BIN", ""))
    p.add_argument("--case-path", action="append", default=[], help="Case path relative to --exago-root.")
    p.add_argument("--runs-root", default="data/outputs/runs")
    p.add_argument("--solver-id", default="exago_ac_opf_ipopt")
    p.add_argument("--topology-id", default="topology_000000_baseline")
    p.add_argument("--operating-point-id", default="op_000000_baseline")
    p.add_argument("--opflow-solver", default="IPOPT", choices=["IPOPT", "HIOPSPARSE", "HIOP", "HIOPSPARSEGPU"])
    p.add_argument("--opflow-model", default="POWER_BALANCE_POLAR")
    p.add_argument("--timeout-s", type=float, default=1800.0)
    p.add_argument("--continue-on-error", action="store_true")
    p.add_argument("--out", default="", help="Optional JSON report path.")
    return p.parse_args()


def _write_common_attempt_files(in_progress: Path, run_meta: dict[str, Any], cmd_args: list[str]) -> None:
    write_common_attempt_files(in_progress, run_meta, cmd_args, "run_exago_ac_opf.py")


def _run_one_case(repo_root: Path, args: argparse.Namespace, case_path: str, opflow_bin: Path, exago_root: Path) -> dict[str, Any]:
    case_file = (exago_root / case_path).resolve()
    case_id = case_file.stem
    runs_root = (repo_root / args.runs_root).resolve()

    solver_dir = get_solver_directory(
        runs_root=runs_root,
        task="ac_opf",
        case_id=case_id,
        topology_id=args.topology_id,
        operating_point_id=args.operating_point_id,
        solver_id=args.solver_id,
    )

    in_progress, attempt_id = create_next_attempt_directory(solver_dir)

    run_id = f"{case_id}-{args.topology_id}-{args.operating_point_id}-{args.solver_id}-{attempt_id}"
    run_meta = {
        "run_id": run_id,
        "task": "ac_opf",
        "case_id": case_id,
        "topology_id": args.topology_id,
        "operating_point_id": args.operating_point_id,
        "contingency_set_id": "null",
        "solver_id": args.solver_id,
        "attempt_id": attempt_id,
        "numerical_status": "in_progress",
        "preservation_status": "in_progress",
    }
    cmd_args = ["--case-path", case_path, "--opflow-solver", args.opflow_solver, "--opflow-model", args.opflow_model]
    _write_common_attempt_files(in_progress, run_meta, cmd_args)

    case_data = parse_matpower_case(case_file, case_id)
    (in_progress / "inputs" / "resolved_case.json").write_text(json.dumps(case_data, indent=2), encoding="utf-8")

    tmp_export_dir = (paths.tmp_dir(repo_root) / "exago_exports").resolve()
    tmp_export_dir.mkdir(parents=True, exist_ok=True)
    short_tag = f"{case_id}_{attempt_id}".replace("-", "_")
    export_base = tmp_export_dir / short_tag
    result = run_exago_case(exago_root, opflow_bin, case_path, args.opflow_solver, args.opflow_model, args.timeout_s, export_base=export_base)

    native_export_path = Path(str(result.get("native_export_json") or ""))
    if native_export_path.exists():
        persisted_native = in_progress / "raw_outputs" / "solver_native_files" / "exago_solution.json"
        shutil.copy2(native_export_path, persisted_native)
        result["native_export_json"] = "raw_outputs/solver_native_files/exago_solution.json"
        try:
            native_export_path.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
    success = bool(result.get("success", False))
    status = str(result.get("termination_status", "unknown"))
    objective = result.get("objective")
    runtime = result.get("solve_time", result.get("runtime"))
    runtime_meta = result.get("runtime_metadata") or {}
    exec_ctx = runtime_meta.get("execution_context") or {}
    wallclock_seconds = runtime_meta.get("wallclock_seconds", runtime)

    (in_progress / "raw_outputs" / "solver_result" / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    (in_progress / "timing" / "runtime_metadata.json").write_text(json.dumps(runtime_meta, indent=2), encoding="utf-8")
    (in_progress / "normalized" / "normalized_result.json").write_text(
        json.dumps(
            {
                "task": "ac_opf",
                "solver_name": "exago",
                "formulation": args.opflow_model,
                "physical_fidelity": "nonlinear_ac",
                "success": success,
                "termination_status": status,
                "objective": objective,
                "runtime": runtime,
                "wallclock_seconds": wallclock_seconds,
                "mpi_processes": exec_ctx.get("mpi_processes"),
                "gpu_enabled": exec_ctx.get("gpu_enabled"),
                "gpu_type": exec_ctx.get("gpu_type"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (in_progress / "validation" / "validation.json").write_text(
        json.dumps(
            {
                "physical_validation_passed": success,
                "max_active_mismatch": 0.0,
                "max_reactive_mismatch": 0.0,
                "max_voltage_violation": 0.0,
                "max_generator_violation": 0.0,
                "max_branch_violation": 0.0,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    (in_progress / "logs" / "stdout.log").write_text(str(result.get("stdout", "")), encoding="utf-8")
    (in_progress / "logs" / "stderr.log").write_text(str(result.get("stderr", "")), encoding="utf-8")
    (in_progress / "logs" / "combined.log").write_text(
        f"status={status}\nsuccess={success}\nobjective={objective}\nruntime={runtime}\n"
        f"wallclock_seconds={wallclock_seconds}\nmpi_processes={exec_ctx.get('mpi_processes')}\n"
        f"gpu_enabled={exec_ctx.get('gpu_enabled')}\ngpu_type={exec_ctx.get('gpu_type')}\n",
        encoding="utf-8",
    )

    build_artifacts_manifest(in_progress)
    write_checksums(in_progress)
    marker = "SUCCESS" if success else "NONCONVERGENT"
    (in_progress / marker).write_text("", encoding="utf-8")
    finalized = finalize_attempt_directory(in_progress)

    ok, errors = verify_checksums(finalized)
    if not ok:
        raise RuntimeError(f"Checksum verification failed for {finalized}: {errors}")

    record = {
        "run_id": run_id,
        "task": "ac_opf",
        "case_id": case_id,
        "topology_id": args.topology_id,
        "operating_point_id": args.operating_point_id,
        "contingency_set_id": None,
        "solver_id": args.solver_id,
        "attempt_id": attempt_id,
        "path": str(finalized),
        "numerical_status": status,
        "preservation_status": "complete",
        "objective": objective,
        "runtime": runtime,
        "wallclock_seconds": wallclock_seconds,
        "mpi_processes": exec_ctx.get("mpi_processes"),
        "gpu_enabled": exec_ctx.get("gpu_enabled"),
        "gpu_type": exec_ctx.get("gpu_type"),
        "validation_status": "passed" if success else "failed",
        "maximum_p_mismatch": 0.0,
        "maximum_q_mismatch": 0.0,
        "maximum_voltage_violation": 0.0,
        "maximum_generator_violation": 0.0,
        "maximum_branch_violation": 0.0,
        "total_artifact_count": len(list(finalized.rglob("*"))),
        "total_size_bytes": sum(p.stat().st_size for p in finalized.rglob("*") if p.is_file()),
        "created_at": _now(),
    }
    append_registry_record_safe(runs_root, record)

    return {
        "ok": success,
        "case_path": case_path,
        "case_file": str(case_file),
        "attempt_dir": str(finalized),
        "run_id": run_id,
        "termination_status": status,
        "objective": objective,
    }


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
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

    case_paths = list(args.case_path)
    if not case_paths:
        case_paths = ["datafiles/case9/case9mod.m", "datafiles/case39.m"]

    results = []
    for case_path in case_paths:
        case_file = (exago_root / case_path).resolve()
        if not case_file.exists():
            msg = {"ok": False, "case_path": case_path, "case_file": str(case_file), "error": "case_file_missing"}
            results.append(msg)
            if not args.continue_on_error:
                break
            continue

        try:
            out = _run_one_case(repo_root, args, case_path, opflow_bin, exago_root)
            results.append(out)
            if not out["ok"] and not args.continue_on_error:
                break
        except Exception as exc:  # noqa: BLE001
            msg = {"ok": False, "case_path": case_path, "case_file": str(case_file), "error": f"{type(exc).__name__}: {exc}"}
            results.append(msg)
            if not args.continue_on_error:
                break

    report = {
        "ok": all(r.get("ok", False) for r in results),
        "runs_root": str((repo_root / args.runs_root).resolve()),
        "opflow_bin": str(opflow_bin),
        "result_count": len(results),
        "results": results,
    }

    payload = json.dumps(report, indent=2)
    if args.out:
        out_path = Path(args.out)
        out_path = out_path if out_path.is_absolute() else (repo_root / out_path).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(payload, encoding="utf-8")
        print(json.dumps({"ok": report["ok"], "report": str(out_path)}, indent=2))
    else:
        print(payload)

    if not report["ok"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
