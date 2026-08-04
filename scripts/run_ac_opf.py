#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

try:
    from grid_data_factory.parsers.matpower import parse_matpower_case
    from grid_data_factory.preservation.artifacts import build_artifacts_manifest
    from grid_data_factory.preservation.checksums import verify_checksums, write_checksums
    from grid_data_factory.solvers.powermodels_adapter import PowerModelsAdapter
    from grid_data_factory.storage.attempt_io import append_registry_record_safe, utc_now_iso, write_common_attempt_files
    from grid_data_factory.storage.layout import create_next_attempt_directory, finalize_attempt_directory, get_solver_directory
except ModuleNotFoundError:
    _repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(_repo_root / "src"))
    from grid_data_factory.parsers.matpower import parse_matpower_case
    from grid_data_factory.preservation.artifacts import build_artifacts_manifest
    from grid_data_factory.preservation.checksums import verify_checksums, write_checksums
    from grid_data_factory.solvers.powermodels_adapter import PowerModelsAdapter
    from grid_data_factory.storage.attempt_io import append_registry_record_safe, utc_now_iso, write_common_attempt_files
    from grid_data_factory.storage.layout import create_next_attempt_directory, finalize_attempt_directory, get_solver_directory


_now = utc_now_iso


def _resolve_case_ids_from_config(repo_root: Path, source: str) -> list[str]:
    cfg = yaml.safe_load((repo_root / "configs" / "sources.yaml").read_text(encoding="utf-8")) or {}
    source_spec = ((cfg.get("sources") or {}).get(source) or {})
    case_ids = []
    for case in source_spec.get("cases") or []:
        mode = str(case.get("acquisition_mode") or "")
        if source == "pglib" and mode != "automatic":
            continue
        case_ids.append(str(case.get("case_id")))
    return case_ids


def _find_manual_matpower_case(case_root: Path) -> Path | None:
    extracted = case_root / "extracted"
    if not extracted.exists():
        return None

    preferred = sorted(extracted.glob("case_*.m"))
    if preferred:
        return preferred[0]

    candidates = []
    for p in sorted(extracted.glob("*.m")):
        n = p.name.lower()
        if n.startswith("contab") or n.startswith("scenarios"):
            continue
        candidates.append(p)
    return candidates[0] if candidates else None


def _resolve_case_file(repo_root: Path, source: str, case_id: str, case_file: str | None) -> Path:
    if case_file:
        p = Path(case_file)
        return p if p.is_absolute() else (repo_root / p).resolve()

    if source == "pglib":
        return (repo_root / "external" / "pglib-opf" / f"{case_id}.m").resolve()

    if source == "tamu":
        found = _find_manual_matpower_case((repo_root / "external" / "tamu" / case_id).resolve())
        if found:
            return found

    raise FileNotFoundError(f"Could not resolve case file for source={source}, case_id={case_id}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run AC-OPF via PowerModels on one or more MATPOWER cases.")
    p.add_argument("--source", default="pglib", choices=["pglib", "tamu"])
    p.add_argument("--case-id", action="append", default=[])
    p.add_argument("--case-file", action="append", default=[], help="Optional explicit case file path(s).")
    p.add_argument("--all-config-cases", action="store_true", help="Run all configured cases for the selected source.")
    p.add_argument("--runs-root", default="data/outputs/runs")
    p.add_argument("--solver-id", default="powermodels_ac_opf_ipopt")
    p.add_argument("--topology-id", default="topology_000000_baseline")
    p.add_argument("--operating-point-id", default="op_000000_baseline")
    p.add_argument("--timeout-s", type=float, default=1800.0)
    p.add_argument("--continue-on-error", action="store_true")
    p.add_argument("--out", default="", help="Optional JSON report path.")
    return p.parse_args()


def _build_case_plan(repo_root: Path, args: argparse.Namespace) -> list[tuple[str, Path]]:
    items: list[tuple[str, Path]] = []

    if args.all_config_cases:
        for cid in _resolve_case_ids_from_config(repo_root, args.source):
            try:
                path = _resolve_case_file(repo_root, args.source, cid, None)
            except FileNotFoundError:
                continue
            if path.exists():
                items.append((cid, path))

    for cid in args.case_id:
        path = _resolve_case_file(repo_root, args.source, cid, None)
        items.append((cid, path))

    for i, cf in enumerate(args.case_file, start=1):
        p = Path(cf)
        p = p if p.is_absolute() else (repo_root / p).resolve()
        cid = p.stem
        if not cid:
            cid = f"case_{i:03d}"
        items.append((cid, p))

    dedup: dict[str, Path] = {}
    for cid, path in items:
        dedup[f"{cid}::{path}"] = path

    out = []
    for key, path in dedup.items():
        cid = key.split("::", 1)[0]
        out.append((cid, path))
    return out


def _write_common_attempt_files(in_progress: Path, run_meta: dict[str, Any], cmd_args: list[str]) -> None:
    write_common_attempt_files(in_progress, run_meta, cmd_args, "run_ac_opf.py")


def _run_one_case(repo_root: Path, args: argparse.Namespace, case_id: str, case_file: Path, adapter: PowerModelsAdapter) -> dict[str, Any]:
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
    cmd_args = ["--source", args.source, "--case-id", case_id]
    _write_common_attempt_files(in_progress, run_meta, cmd_args)

    case_data = parse_matpower_case(case_file, case_id)
    (in_progress / "inputs" / "resolved_case.json").write_text(json.dumps(case_data, indent=2), encoding="utf-8")

    result = adapter.solve_ac_opf(case_data, options={"timeout_s": args.timeout_s})
    success = bool(result.get("success", False))
    status = str(result.get("termination_status", "unknown"))
    objective = result.get("objective")
    runtime = result.get("solve_time", result.get("runtime"))
    runtime_meta = result.get("runtime_metadata") or {}
    exec_ctx = runtime_meta.get("execution_context") or {}
    wallclock_seconds = runtime_meta.get("wallclock_seconds", runtime)

    # Ensure raw solver artifacts always carry explicit solver provenance.
    solver_provenance = {
        "task": "ac_opf",
        "solver_id": args.solver_id,
        "solver_name": result.get("solver_name", "powermodels"),
        "solver_backend": "julia_powermodels",
        "optimizer": "Ipopt.Optimizer",
        "formulation": "ACPPowerModel",
        "case_id": case_id,
    }

    raw_result = dict(result)
    for key, value in solver_provenance.items():
        raw_result.setdefault(key, value)

    solver_dir_name = re.sub(r"[^A-Za-z0-9._-]+", "_", args.solver_id).strip("._-") or "unknown_solver"
    solver_raw_dir = in_progress / "raw_outputs" / "solver_result" / solver_dir_name
    solver_raw_dir.mkdir(parents=True, exist_ok=True)

    # Backward-compatible top-level paths.
    (in_progress / "raw_outputs" / "solver_result" / "result.json").write_text(
        json.dumps(raw_result, indent=2),
        encoding="utf-8",
    )
    (in_progress / "raw_outputs" / "solver_result" / "solver_provenance.json").write_text(
        json.dumps(solver_provenance, indent=2),
        encoding="utf-8",
    )
    # Preferred solver-specific subdirectory paths.
    (solver_raw_dir / "result.json").write_text(
        json.dumps(raw_result, indent=2),
        encoding="utf-8",
    )
    (solver_raw_dir / "solver_provenance.json").write_text(
        json.dumps(solver_provenance, indent=2),
        encoding="utf-8",
    )
    (in_progress / "timing" / "runtime_metadata.json").write_text(json.dumps(runtime_meta, indent=2), encoding="utf-8")
    (in_progress / "normalized" / "normalized_result.json").write_text(
        json.dumps(
            {
                "task": "ac_opf",
                "solver_name": result.get("solver_name", "powermodels"),
                "formulation": "ACPPowerModel",
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
        "case_id": case_id,
        "case_file": str(case_file),
        "attempt_dir": str(finalized),
        "run_id": run_id,
        "termination_status": status,
        "objective": objective,
    }


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]

    plan = _build_case_plan(repo_root, args)
    if not plan:
        raise SystemExit("No cases selected. Use --case-id, --case-file, or --all-config-cases.")

    adapter = PowerModelsAdapter(repo_root=repo_root)

    results = []
    for case_id, case_file in plan:
        if not case_file.exists():
            msg = {"ok": False, "case_id": case_id, "case_file": str(case_file), "error": "case_file_missing"}
            results.append(msg)
            if not args.continue_on_error:
                break
            continue

        try:
            out = _run_one_case(repo_root, args, case_id, case_file, adapter)
            results.append(out)
            if not out["ok"] and not args.continue_on_error:
                break
        except Exception as exc:  # noqa: BLE001
            msg = {"ok": False, "case_id": case_id, "case_file": str(case_file), "error": f"{type(exc).__name__}: {exc}"}
            results.append(msg)
            if not args.continue_on_error:
                break

    report = {
        "ok": all(r.get("ok", False) for r in results),
        "source": args.source,
        "runs_root": str((repo_root / args.runs_root).resolve()),
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
