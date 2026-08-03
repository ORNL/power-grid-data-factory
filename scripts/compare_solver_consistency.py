#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any


def _resolve_opflow_bin(repo_root: Path, exago_root: Path, args: argparse.Namespace) -> Path:
    # Precedence: explicit binary path, explicit install prefix, profile path, legacy default.
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


def _run_exago_opflow(exago_root: Path, opflow_bin: Path, case_path: str, solver: str, model: str) -> dict[str, Any]:
    full_case = (exago_root / case_path).resolve()
    cmd = [
        str(opflow_bin),
        "-netfile",
        str(full_case),
        "-opflow_solver",
        solver,
        "-opflow_model",
        model,
        "-print_output",
        "1",
    ]

    if solver == "HIOP":
        cmd.extend(["-hiop_compute_mode", "CPU"])

    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=exago_root)
    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")

    convergence = None
    objective = None

    m_conv = re.search(r"Convergence status\s+(\S+)", combined)
    if m_conv:
        convergence = m_conv.group(1)

    m_obj = re.search(r"Objective value\s+([0-9.+-Ee]+)", combined)
    if m_obj:
        objective = float(m_obj.group(1))

    return {
        "exit_code": proc.returncode,
        "convergence": convergence,
        "objective": objective,
        "stderr_tail": "\n".join((proc.stderr or "").splitlines()[-5:]),
    }


def _run_pandapower_opf(exago_root: Path, case_path: str) -> dict[str, Any]:
    import pandapower as pp
    from pandapower.converter.matpower import from_mpc

    full_case = exago_root / case_path
    net = from_mpc(str(full_case))
    pp.runopp(net, numba=False)

    return {
        "convergence": "CONVERGED" if bool(net.get("OPF_converged", False)) else "FAILED",
        "objective": float(net.res_cost),
    }


def _powermodels_status(repo_root: Path) -> dict[str, Any]:
    from grid_data_factory.solvers.powermodels_adapter import PowerModelsAdapter

    adapter = PowerModelsAdapter(repo_root=repo_root)
    tiny_case = {
        "case_id": "tiny_demo",
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
    return adapter.solve_ac_opf(tiny_case, options={"timeout_s": 300})


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare ExaGO and pandapower AC-OPF outputs on MATPOWER cases.")
    parser.add_argument(
        "--exago-root",
        default="external/ExaGO",
        help="Path to ExaGO checkout containing datafiles/.",
    )
    parser.add_argument(
        "--exago-install",
        default=os.environ.get("PGDF_EXAGO_INSTALL_PREFIX", ""),
        help="Optional install prefix containing bin/opflow. Relative paths resolve from repo root.",
    )
    parser.add_argument(
        "--build-profile",
        default=os.environ.get("PGDF_EXAGO_BUILD_PROFILE", ""),
        help="Optional machine profile; resolves opflow under <exago-root>/builds/<profile>/install/bin/opflow.",
    )
    parser.add_argument(
        "--opflow-bin",
        default=os.environ.get("PGDF_EXAGO_OPFLOW_BIN", ""),
        help="Optional explicit opflow binary path. Relative paths resolve from repo root.",
    )
    parser.add_argument(
        "--cases",
        nargs="+",
        default=["datafiles/case9/case9mod.m", "datafiles/case39.m"],
        help="Case paths relative to --exago-root.",
    )
    parser.add_argument(
        "--out",
        default="",
        help="Optional JSON output path. If omitted, prints JSON to stdout.",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    exago_root = (repo_root / args.exago_root).resolve()
    opflow_bin = _resolve_opflow_bin(repo_root, exago_root, args)
    if not opflow_bin.exists():
        raise SystemExit(
            json.dumps(
                {
                    "ok": False,
                    "stage": "resolve_opflow_bin",
                    "message": "opflow binary not found",
                    "opflow_bin": str(opflow_bin),
                },
                indent=2,
            )
        )
    if not os.access(opflow_bin, os.X_OK):
        raise SystemExit(
            json.dumps(
                {
                    "ok": False,
                    "stage": "resolve_opflow_bin",
                    "message": "opflow binary exists but is not executable",
                    "opflow_bin": str(opflow_bin),
                },
                indent=2,
            )
        )

    report: dict[str, Any] = {
        "cases": {},
        "powermodels_status": None,
        "exago_opflow_bin": str(opflow_bin),
        "notes": [
            "ExaGO comparison runs IPOPT, HIOPSPARSE, and HIOP(CPU).",
            "Pandapower objective may differ slightly due to formulation and conversion differences.",
        ],
    }

    for case in args.cases:
        ex_ipopt = _run_exago_opflow(exago_root, opflow_bin, case, "IPOPT", "POWER_BALANCE_POLAR")
        ex_hiopsparse = _run_exago_opflow(exago_root, opflow_bin, case, "HIOPSPARSE", "POWER_BALANCE_POLAR")
        ex_hiop = _run_exago_opflow(exago_root, opflow_bin, case, "HIOP", "POWER_BALANCE_HIOP")

        pp_res = _run_pandapower_opf(exago_root, case)

        ref = ex_ipopt.get("objective")
        pp_obj = pp_res.get("objective")
        abs_diff = abs(pp_obj - ref) if (isinstance(pp_obj, float) and isinstance(ref, float)) else None
        rel_diff = (abs_diff / abs(ref)) if (isinstance(abs_diff, float) and ref) else None

        report["cases"][case] = {
            "exago": {
                "IPOPT": ex_ipopt,
                "HIOPSPARSE": ex_hiopsparse,
                "HIOP": ex_hiop,
            },
            "pandapower": pp_res,
            "pandapower_vs_exago_ipopt": {
                "abs_diff": abs_diff,
                "rel_diff": rel_diff,
            },
        }

    report["powermodels_status"] = _powermodels_status(repo_root)

    payload = json.dumps(report, indent=2)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload, encoding="utf-8")
        print(json.dumps({"ok": True, "report": str(out.resolve())}, indent=2))
    else:
        print(payload)


if __name__ == "__main__":
    main()
