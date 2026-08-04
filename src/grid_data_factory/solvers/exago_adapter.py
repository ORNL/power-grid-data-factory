from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from grid_data_factory.parsers.matpower import parse_matpower_case
from grid_data_factory.runtime_metadata import collect_execution_context


def resolve_opflow_bin(
    repo_root: Path,
    exago_root: Path,
    *,
    opflow_bin: str = "",
    exago_install: str = "",
    build_profile: str = "",
) -> Path:
    # Precedence: explicit binary path, explicit install prefix, profile path, legacy default.
    if opflow_bin:
        p = Path(opflow_bin)
        return p if p.is_absolute() else (repo_root / p).resolve()

    if exago_install:
        install_prefix = Path(exago_install)
        install_prefix = install_prefix if install_prefix.is_absolute() else (repo_root / install_prefix).resolve()
        return install_prefix / "bin" / "opflow"

    profile = (build_profile or "").strip()
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


def run_exago_case(
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
