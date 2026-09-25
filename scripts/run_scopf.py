#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

_scripts_dir = Path(__file__).resolve().parent
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))

import run_ac_opf as ac_workflow

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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run coupled AC SCOPF via PowerModelsSecurityConstrained and Ipopt.")
    parser.add_argument("--source", default="pglib", choices=["pglib", "tamu"])
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--case-file", action="append", default=[], help="Optional explicit MATPOWER case file path(s).")
    parser.add_argument("--all-config-cases", action="store_true", help="Run all configured cases for the selected source.")
    parser.add_argument(
        "--contingency-set",
        required=True,
        help="JSON/JSONL file containing N-1 contingencies or candidate rows with nested contingency objects.",
    )
    parser.add_argument("--contingency-set-id", default="", help="Override the set ID inferred from the input file.")
    parser.add_argument("--runs-root", default="data/outputs/runs")
    parser.add_argument("--solver-id", default="powermodels_scopf_pmsc_ipopt")
    parser.add_argument("--topology-id", default="topology_000000_baseline")
    parser.add_argument("--operating-point-id", default="op_000000_baseline")
    parser.add_argument("--timeout-s", type=float, default=1800.0)
    parser.add_argument("--tol", type=float, default=1e-8)
    parser.add_argument("--max-iter", type=int)
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--out", default="", help="Optional JSON report path.")
    return parser.parse_args()


def _resolve_path(repo_root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (repo_root / path).resolve()


def _read_contingency_document(path: Path) -> tuple[str | None, list[dict[str, Any]]]:
    text = path.read_text(encoding="utf-8")
    try:
        document = json.loads(text)
    except json.JSONDecodeError:
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
        return None, rows

    if isinstance(document, list):
        return None, document
    if not isinstance(document, dict):
        raise ValueError("Contingency input must be a JSON object, JSON array, or JSONL rows")
    if "contingencies" in document:
        rows = document["contingencies"]
        if not isinstance(rows, list):
            raise ValueError("contingencies must be a JSON array")
        set_id = document.get("contingency_set_id") or document.get("set_id")
        return str(set_id) if set_id else None, rows
    return None, [document]


def _validate_n1_contingency(contingency: dict[str, Any], position: int) -> dict[str, Any]:
    event_type = str(contingency.get("event_type", "simultaneous"))
    components = contingency.get("components") or []
    if event_type != "simultaneous" or len(components) != 1:
        raise ValueError(
            f"Contingency {position} is not a static N-1 event; PMSC requires event_type=simultaneous "
            "with exactly one component"
        )
    component = components[0]
    component_type = str(component.get("type", ""))
    component_id = str(component.get("id", ""))
    if component_type not in {"branch", "generator"} or not component_id:
        raise ValueError(f"Contingency {position} must identify one branch or generator")

    resolved = dict(contingency)
    resolved["event_type"] = "simultaneous"
    resolved["order"] = 1
    resolved["components"] = [{"type": component_type, "id": component_id}]
    resolved.setdefault("contingency_id", f"ctg_{position:06d}")
    return resolved


def load_contingency_set(path: Path, case_id: str, override_id: str = "") -> tuple[str, list[dict[str, Any]]]:
    document_id, rows = _read_contingency_document(path)
    contingencies: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Every contingency row must be a JSON object")
        row_case_id = row.get("case_id")
        if row_case_id is not None and str(row_case_id) != case_id:
            continue
        contingency = row.get("contingency", row)
        if not isinstance(contingency, dict):
            raise ValueError("Nested contingency must be a JSON object")
        contingencies.append(_validate_n1_contingency(contingency, len(contingencies) + 1))

    if not contingencies:
        raise ValueError(f"No N-1 contingencies found for case_id={case_id}")
    contingency_set_id = override_id or document_id or path.stem
    contingency_set_id = re.sub(r"[^A-Za-z0-9._-]+", "_", contingency_set_id).strip("._-")
    if not contingency_set_id:
        raise ValueError("contingency_set_id is empty after sanitization")
    return contingency_set_id, contingencies


def _write_result_artifacts(
    in_progress: Path,
    args: argparse.Namespace,
    case_id: str,
    contingency_set_id: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    success = bool(result.get("success", False))
    status = str(result.get("termination_status", "unknown"))
    objective = result.get("objective")
    runtime = result.get("solve_time", result.get("runtime"))
    runtime_meta = result.get("runtime_metadata") or {}
    exec_ctx = runtime_meta.get("execution_context") or {}
    wallclock_seconds = runtime_meta.get("wallclock_seconds", runtime)
    solver_provenance = {
        "task": "scopf",
        "solver_id": args.solver_id,
        "solver_name": result.get("solver_name", "powermodels_security_constrained"),
        "solver_backend": "julia_powermodels_security_constrained",
        "optimizer": "Ipopt.Optimizer",
        "formulation": "PowerModelsSecurityConstrained.build_c1_scopf",
        "case_id": case_id,
        "contingency_set_id": contingency_set_id,
        "contingency_count": result.get("contingency_count"),
    }
    raw_result = dict(result)
    for key, value in solver_provenance.items():
        raw_result.setdefault(key, value)

    solver_name = re.sub(r"[^A-Za-z0-9._-]+", "_", args.solver_id).strip("._-") or "unknown_solver"
    result_root = in_progress / "raw_outputs" / "solver_result"
    solver_root = result_root / solver_name
    solver_root.mkdir(parents=True, exist_ok=True)
    for root in (result_root, solver_root):
        (root / "result.json").write_text(json.dumps(raw_result, indent=2), encoding="utf-8")
        (root / "solver_provenance.json").write_text(json.dumps(solver_provenance, indent=2), encoding="utf-8")

    (in_progress / "timing" / "runtime_metadata.json").write_text(json.dumps(runtime_meta, indent=2), encoding="utf-8")
    normalized = {
        "task": "scopf",
        "solver_name": solver_provenance["solver_name"],
        "formulation": solver_provenance["formulation"],
        "physical_fidelity": "coupled_nonlinear_ac_security_constrained",
        "success": success,
        "termination_status": status,
        "objective": objective,
        "runtime": runtime,
        "wallclock_seconds": wallclock_seconds,
        "contingency_set_id": contingency_set_id,
        "contingency_count": result.get("contingency_count"),
        "mpi_processes": exec_ctx.get("mpi_processes"),
        "gpu_enabled": exec_ctx.get("gpu_enabled"),
        "gpu_type": exec_ctx.get("gpu_type"),
    }
    (in_progress / "normalized" / "normalized_result.json").write_text(json.dumps(normalized, indent=2), encoding="utf-8")
    (in_progress / "validation" / "validation.json").write_text(
        json.dumps({"physical_validation_passed": success, "solver_termination_status": status}, indent=2),
        encoding="utf-8",
    )
    (in_progress / "logs" / "stdout.log").write_text(str(result.get("stdout", "")), encoding="utf-8")
    (in_progress / "logs" / "stderr.log").write_text(str(result.get("stderr", "")), encoding="utf-8")
    (in_progress / "logs" / "combined.log").write_text(
        f"status={status}\nsuccess={success}\nobjective={objective}\nruntime={runtime}\n"
        f"wallclock_seconds={wallclock_seconds}\ncontingency_count={result.get('contingency_count')}\n",
        encoding="utf-8",
    )
    return {
        "success": success,
        "status": status,
        "objective": objective,
        "runtime": runtime,
        "runtime_meta": runtime_meta,
        "execution_context": exec_ctx,
        "wallclock_seconds": wallclock_seconds,
    }


def _run_one_case(
    repo_root: Path,
    args: argparse.Namespace,
    case_id: str,
    case_file: Path,
    contingency_set_id: str,
    contingencies: list[dict[str, Any]],
    adapter: PowerModelsAdapter,
) -> dict[str, Any]:
    runs_root = (repo_root / args.runs_root).resolve()
    solver_dir = get_solver_directory(
        runs_root=runs_root,
        task="scopf",
        case_id=case_id,
        topology_id=args.topology_id,
        operating_point_id=args.operating_point_id,
        solver_id=args.solver_id,
        contingency_set_id=contingency_set_id,
    )
    in_progress, attempt_id = create_next_attempt_directory(solver_dir)
    run_id = (
        f"{case_id}-{args.topology_id}-{args.operating_point_id}-"
        f"{contingency_set_id}-{args.solver_id}-{attempt_id}"
    )
    run_meta = {
        "run_id": run_id,
        "task": "scopf",
        "case_id": case_id,
        "topology_id": args.topology_id,
        "operating_point_id": args.operating_point_id,
        "contingency_set_id": contingency_set_id,
        "solver_id": args.solver_id,
        "attempt_id": attempt_id,
        "numerical_status": "in_progress",
        "preservation_status": "in_progress",
    }
    cmd_args = [
        "--source", args.source,
        "--case-id", case_id,
        "--contingency-set", args.contingency_set,
        "--contingency-set-id", contingency_set_id,
    ]
    write_common_attempt_files(in_progress, run_meta, cmd_args, "run_scopf.py")

    case_data = parse_matpower_case(case_file, case_id)
    (in_progress / "inputs" / "resolved_case.json").write_text(json.dumps(case_data, indent=2), encoding="utf-8")
    resolved_set = {
        "contingency_set_id": contingency_set_id,
        "case_id": case_id,
        "contingency_count": len(contingencies),
        "contingencies": contingencies,
    }
    (in_progress / "inputs" / "resolved_contingency_set.json").write_text(
        json.dumps(resolved_set, indent=2), encoding="utf-8"
    )

    options: dict[str, Any] = {"timeout_s": args.timeout_s, "tol": args.tol}
    if args.max_iter is not None:
        options["max_iter"] = args.max_iter
    result = adapter.solve_scopf(case_data, contingencies, options=options)
    summary = _write_result_artifacts(in_progress, args, case_id, contingency_set_id, result)

    build_artifacts_manifest(in_progress)
    write_checksums(in_progress)
    marker = "SUCCESS" if summary["success"] else "NONCONVERGENT"
    (in_progress / marker).write_text("", encoding="utf-8")
    finalized = finalize_attempt_directory(in_progress)
    checksums_ok, checksum_errors = verify_checksums(finalized)
    if not checksums_ok:
        raise RuntimeError(f"Checksum verification failed for {finalized}: {checksum_errors}")

    exec_ctx = summary["execution_context"]
    record = {
        "run_id": run_id,
        "task": "scopf",
        "case_id": case_id,
        "topology_id": args.topology_id,
        "operating_point_id": args.operating_point_id,
        "contingency_set_id": contingency_set_id,
        "contingency_count": len(contingencies),
        "solver_id": args.solver_id,
        "attempt_id": attempt_id,
        "path": str(finalized),
        "numerical_status": summary["status"],
        "preservation_status": "complete",
        "objective": summary["objective"],
        "runtime": summary["runtime"],
        "wallclock_seconds": summary["wallclock_seconds"],
        "mpi_processes": exec_ctx.get("mpi_processes"),
        "gpu_enabled": exec_ctx.get("gpu_enabled"),
        "gpu_type": exec_ctx.get("gpu_type"),
        "validation_status": "passed" if summary["success"] else "failed",
        "total_artifact_count": len(list(finalized.rglob("*"))),
        "total_size_bytes": sum(path.stat().st_size for path in finalized.rglob("*") if path.is_file()),
        "created_at": utc_now_iso(),
    }
    append_registry_record_safe(runs_root, record)
    return {
        "ok": summary["success"],
        "case_id": case_id,
        "case_file": str(case_file),
        "contingency_set_id": contingency_set_id,
        "contingency_count": len(contingencies),
        "attempt_dir": str(finalized),
        "run_id": run_id,
        "termination_status": summary["status"],
        "objective": summary["objective"],
    }


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    plan = ac_workflow._build_case_plan(repo_root, args)
    if not plan:
        raise SystemExit("No cases selected. Use --case-id, --case-file, or --all-config-cases.")

    contingency_path = _resolve_path(repo_root, args.contingency_set)
    if not contingency_path.is_file():
        raise SystemExit(f"Contingency set file does not exist: {contingency_path}")
    adapter = PowerModelsAdapter(repo_root=repo_root)
    results: list[dict[str, Any]] = []
    for case_id, case_file in plan:
        if not case_file.exists():
            result = {"ok": False, "case_id": case_id, "case_file": str(case_file), "error": "case_file_missing"}
            results.append(result)
            if not args.continue_on_error:
                break
            continue
        try:
            set_id, contingencies = load_contingency_set(contingency_path, case_id, args.contingency_set_id)
            result = _run_one_case(repo_root, args, case_id, case_file, set_id, contingencies, adapter)
            results.append(result)
            if not result["ok"] and not args.continue_on_error:
                break
        except Exception as exc:  # noqa: BLE001
            results.append(
                {"ok": False, "case_id": case_id, "case_file": str(case_file), "error": f"{type(exc).__name__}: {exc}"}
            )
            if not args.continue_on_error:
                break

    report = {
        "ok": bool(results) and all(result.get("ok", False) for result in results),
        "source": args.source,
        "contingency_set_file": str(contingency_path),
        "runs_root": str((repo_root / args.runs_root).resolve()),
        "result_count": len(results),
        "results": results,
    }
    payload = json.dumps(report, indent=2)
    if args.out:
        out_path = _resolve_path(repo_root, args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(payload, encoding="utf-8")
        print(json.dumps({"ok": report["ok"], "report": str(out_path)}, indent=2))
    else:
        print(payload)
    if not report["ok"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
