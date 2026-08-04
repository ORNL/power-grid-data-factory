from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from grid_data_factory.runtime_metadata import collect_execution_context


def _parse_pp_exception(exc: Exception) -> str:
    msg = str(exc).strip()
    if "did not converge" in msg.lower():
        return "nonconverged"
    return f"process_error:{type(exc).__name__}"


def run_pandapower_case(case_file: Path) -> dict[str, Any]:
    start_t = time.perf_counter()
    exec_ctx = collect_execution_context()
    try:
        import pandapower as pp
        from pandapower.converter.matpower import from_mpc
    except ModuleNotFoundError as exc:
        elapsed = round(time.perf_counter() - start_t, 6)
        return {
            "success": False,
            "termination_status": "missing_deps",
            "solver_name": "pandapower",
            "error": f"{type(exc).__name__}: {exc}",
            "runtime": elapsed,
            "solve_time": elapsed,
            "runtime_metadata": {
                "wallclock_seconds": elapsed,
                "execution_context": exec_ctx,
            },
        }

    try:
        net = from_mpc(str(case_file))
        pp.runopp(net, numba=False)
        converged = bool(net.get("OPF_converged", False))
        objective = float(net.res_cost)
        status = "converged" if converged else "nonconverged"
        elapsed = round(time.perf_counter() - start_t, 6)
        return {
            "success": converged,
            "termination_status": status,
            "solver_name": "pandapower",
            "objective": objective,
            "runtime": elapsed,
            "solve_time": elapsed,
            "runtime_metadata": {
                "wallclock_seconds": elapsed,
                "execution_context": exec_ctx,
            },
        }
    except Exception as exc:  # noqa: BLE001
        elapsed = round(time.perf_counter() - start_t, 6)
        return {
            "success": False,
            "termination_status": _parse_pp_exception(exc),
            "solver_name": "pandapower",
            "error": f"{type(exc).__name__}: {exc}",
            "runtime": elapsed,
            "solve_time": elapsed,
            "runtime_metadata": {
                "wallclock_seconds": elapsed,
                "execution_context": exec_ctx,
            },
        }
