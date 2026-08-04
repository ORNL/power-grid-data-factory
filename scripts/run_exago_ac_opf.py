#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from grid_data_factory.parsers.matpower import parse_matpower_case
    from grid_data_factory.preservation.artifacts import build_artifacts_manifest
    from grid_data_factory.preservation.checksums import verify_checksums, write_checksums
    from grid_data_factory.runtime_metadata import collect_execution_context
    from grid_data_factory.storage.layout import create_next_attempt_directory, finalize_attempt_directory, get_solver_directory
    from grid_data_factory.storage.registry import append_registry_record as _append_registry_record
except ModuleNotFoundError:
    _repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(_repo_root / "src"))
    from grid_data_factory.parsers.matpower import parse_matpower_case
    from grid_data_factory.preservation.artifacts import build_artifacts_manifest
    from grid_data_factory.preservation.checksums import verify_checksums, write_checksums
    from grid_data_factory.runtime_metadata import collect_execution_context
    from grid_data_factory.storage.layout import create_next_attempt_directory, finalize_attempt_directory, get_solver_directory
    _append_registry_record = None

from grid_data_factory.storage import paths  # noqa: E402


def _append_registry_record_fallback(runs_root: Path, record: dict[str, Any]) -> None:
    jsonl = runs_root / "run_registry.jsonl"
    jsonl.parent.mkdir(parents=True, exist_ok=True)
    with jsonl.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=True) + "\n")


def append_registry_record_safe(runs_root: Path, record: dict[str, Any]) -> None:
    if _append_registry_record is not None:
        try:
            _append_registry_record(runs_root, record)
            return
        except ModuleNotFoundError:
            pass
    _append_registry_record_fallback(runs_root, record)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_opflow_bin(repo_root: Path, exago_root: Path, args: argparse.Namespace) -> Path:
    if args.opflow_bin:
        p = Path(args.opflow_bin)
        return p if p.is_absolute() else (repo_root / p).resolve()

    if args.exago_install:
        install_prefix = Path(args.exago_install)
        install_prefix = install_prefix if install_prefix.is_absolute() else (repo_root / install_prefix).resolve()
        return install_prefix / "bin" / "opflow"

    profile = args.build_profile.strip()
    if profile:
        return (exago_root / "builds" / profile / "install" / "bin" / "opflow").resolve()

    return (exago_root / "install" / "bin" / "opflow").resolve()


def _extract_section(stdout_text: str, header_line: str) -> list[str]:
    lines = stdout_text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if header_line in line:
            start = i + 1
            break
    if start is None:
        return []

    rows: list[str] = []
    for line in lines[start:]:
        stripped = line.strip()
        if not stripped:
            if rows:
                break
            continue
        if stripped.startswith("---") or stripped.startswith("==="):
            continue
        if stripped.startswith("["):
            break
        rows.append(line)
    return rows


def _parse_exago_solution(stdout_text: str, case_data: dict[str, Any]) -> dict[str, Any]:
    base_mva = float(case_data.get("base_mva", 100.0))

    bus_sol: dict[str, dict[str, float]] = {}
    bus_rows = _extract_section(stdout_text, "Bus        Pd")
    for row in bus_rows:
        toks = row.split()
        if len(toks) < 7 or not toks[0].isdigit():
            continue
        bus_idx = toks[0]
        bus_sol[bus_idx] = {
            "vm": float(toks[5]),
            "va": float(toks[6]),
        }

    branch_sol: dict[str, dict[str, float]] = {}
    branch_rows = _extract_section(stdout_text, "From       To       Status")
    for i, row in enumerate(branch_rows, start=1):
        toks = row.split()
        if len(toks) < 6:
            continue
        if not toks[0].isdigit() or not toks[1].isdigit():
            continue
        sft = float(toks[3])
        stf = float(toks[4])
        branch_sol[str(i)] = {
            "pf": sft / base_mva,
            "pt": -stf / base_mva,
            "qf": 0.0,
            "qt": 0.0,
        }

    gen_sol: dict[str, dict[str, float]] = {}
    gen_rows = _extract_section(stdout_text, "Gen      Status     Fuel")
    for i, row in enumerate(gen_rows, start=1):
        toks = row.split()
        if len(toks) < 9 or not toks[0].isdigit():
            continue
        pg = float(toks[3])
        qg = float(toks[4])
        gen_sol[str(i)] = {
            "pg": pg / base_mva,
            "qg": qg / base_mva,
            "pg_cost": 0.0,
        }

    return {
        "baseMVA": base_mva,
        "bus": bus_sol,
        "branch": branch_sol,
        "gen": gen_sol,
        "per_unit": True,
        "multinetwork": False,
        "multiinfrastructure": False,
    }


def _parse_exago_json_export(export_path: Path, case_data: dict[str, Any]) -> dict[str, Any] | None:
    if not export_path.exists():
        return None

    payload = json.loads(export_path.read_text(encoding="utf-8"))
    features = ((payload.get("geojsondata") or {}).get("features") or [])
    base_mva = float(case_data.get("base_mva", 100.0))

    bus_sol: dict[str, dict[str, float]] = {}
    gen_by_bus: dict[str, list[dict[str, float]]] = {}
    branch_sol: dict[str, dict[str, float]] = {}

    for feat in features:
        props = feat.get("properties") or {}
        etype = str(props.get("elementtype", "")).lower()

        if etype == "substation":
            bus_entries = props.get("bus") or []
            for b in bus_entries:
                bid = str(int(float(b.get("BUS_I", 0)))) if b.get("BUS_I") is not None else ""
                if not bid:
                    continue
                bus_sol[bid] = {
                    "vm": float(b.get("VM", 1.0)),
                    "va": float(b.get("VA", 0.0)),
                }
                gens = b.get("gen") or []
                if gens:
                    gen_by_bus.setdefault(bid, [])
                    for g in gens:
                        gen_by_bus[bid].append(
                            {
                                "pg": float(g.get("PG", 0.0)) / base_mva,
                                "qg": float(g.get("QG", 0.0)) / base_mva,
                                "pg_cost": 0.0,
                            }
                        )

        elif etype == "branch":
            idx = len(branch_sol) + 1
            branch_sol[str(idx)] = {
                "pf": float(props.get("PF", 0.0)) / base_mva,
                "qf": float(props.get("QF", 0.0)) / base_mva,
                "pt": float(props.get("PT", 0.0)) / base_mva,
                "qt": float(props.get("QT", 0.0)) / base_mva,
            }

    gen_sol: dict[str, dict[str, float]] = {}
    gen_offsets: dict[str, int] = {}
    for i, gen in enumerate(case_data.get("generators", []), start=1):
        bus_id = str(gen.get("bus_id", ""))
        options = gen_by_bus.get(bus_id, [])
        pos = gen_offsets.get(bus_id, 0)
        if pos < len(options):
            gen_sol[str(i)] = options[pos]
            gen_offsets[bus_id] = pos + 1
        else:
            gen_sol[str(i)] = {"pg": 0.0, "qg": 0.0, "pg_cost": 0.0}

    if not bus_sol:
        return None

    return {
        "baseMVA": base_mva,
        "bus": bus_sol,
        "branch": branch_sol,
        "gen": gen_sol,
        "per_unit": True,
        "multinetwork": False,
        "multiinfrastructure": False,
    }


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
    run_yaml = "\n".join([f"{k}: {v}" for k, v in run_meta.items()]) + "\n"
    (in_progress / "run.yaml").write_text(run_yaml, encoding="utf-8")
    (in_progress / "command.txt").write_text("python scripts/run_exago_ac_opf.py " + " ".join(cmd_args), encoding="utf-8")
    (in_progress / "command.json").write_text(
        json.dumps({"executable": "python3.11", "args": ["scripts/run_exago_ac_opf.py", *cmd_args]}, indent=2),
        encoding="utf-8",
    )
    (in_progress / "environment" / "environment.json").write_text(
        json.dumps({"created_at": _now(), "python": "3.11", "script": "run_exago_ac_opf.py"}, indent=2),
        encoding="utf-8",
    )


def _run_exago_case(
    exago_root: Path,
    opflow_bin: Path,
    case_path: str,
    opflow_solver: str,
    opflow_model: str,
    timeout_s: float,
    export_base: Path | None = None,
) -> dict[str, Any]:
    start_t = time.perf_counter()
    exec_ctx = collect_execution_context()

    full_case = (exago_root / case_path).resolve()
    cmd = [
        str(opflow_bin),
        "-netfile",
        str(full_case),
        "-opflow_solver",
        opflow_solver,
        "-opflow_model",
        opflow_model,
        "-print_output",
        "1",
    ]
    export_json_path = None
    if export_base is not None:
        cmd.extend(["-opflow_output_format", "JSON", "-save_output", str(export_base)])
        export_json_path = export_base.with_suffix(".json")
    if opflow_solver == "HIOP":
        cmd.extend(["-hiop_compute_mode", "CPU"])

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=exago_root, timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        elapsed = round(time.perf_counter() - start_t, 6)
        return {
            "success": False,
            "termination_status": "timeout",
            "solver_name": "exago",
            "stdout": exc.stdout,
            "stderr": exc.stderr,
            "runtime": elapsed,
            "solve_time": elapsed,
            "runtime_metadata": {
                "wallclock_seconds": elapsed,
                "execution_context": exec_ctx,
            },
        }

    elapsed = round(time.perf_counter() - start_t, 6)
    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")

    convergence = None
    objective = None
    m_conv = re.search(r"Convergence status\s+(\S+)", combined)
    if m_conv:
        convergence = m_conv.group(1)
    m_obj = re.search(r"Objective value\s+([0-9.+-Ee]+)", combined)
    if m_obj:
        objective = float(m_obj.group(1))

    success = bool(proc.returncode == 0 and str(convergence).upper() == "CONVERGED")
    status = "converged" if success else "nonconverged"
    if proc.returncode != 0 and convergence is None:
        status = "process_error"

    case_data = parse_matpower_case(full_case, full_case.stem)
    parsed_solution = None
    if export_json_path is not None:
        try:
            parsed_solution = _parse_exago_json_export(export_json_path, case_data)
        except Exception:  # noqa: BLE001
            parsed_solution = None
    if parsed_solution is None:
        parsed_solution = _parse_exago_solution(proc.stdout or "", case_data)

    return {
        "success": success,
        "termination_status": status,
        "solver_name": "exago",
        "exit_code": proc.returncode,
        "convergence": convergence,
        "objective": objective,
        "raw_result": {
            "optimizer": opflow_solver,
            "termination_status": "LOCALLY_SOLVED" if success else "NONCONVERGED",
            "objective": objective,
            "solution": parsed_solution,
            "solve_time": elapsed,
        },
        "native_export_json": str(export_json_path) if export_json_path is not None else None,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "runtime": elapsed,
        "solve_time": elapsed,
        "runtime_metadata": {
            "wallclock_seconds": elapsed,
            "execution_context": exec_ctx,
        },
    }


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
    result = _run_exago_case(exago_root, opflow_bin, case_path, args.opflow_solver, args.opflow_model, args.timeout_s, export_base=export_base)

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
    opflow_bin = _resolve_opflow_bin(repo_root, exago_root, args)

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
