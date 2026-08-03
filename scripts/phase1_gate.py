#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Phase 1 gate: solver consistency + preservation audit")
    p.add_argument("--config", default="configs/phase1_calibration.yaml")
    p.add_argument("--exago-root", default="external/ExaGO")
    p.add_argument("--exago-install", default="")
    p.add_argument("--build-profile", default="")
    p.add_argument("--opflow-bin", default="")
    p.add_argument("--runs-root", default="data/runs")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    import yaml

    repo_root = Path(__file__).resolve().parents[1]

    cfg = yaml.safe_load((repo_root / args.config).read_text(encoding="utf-8"))
    phase1 = cfg["phase1"]

    cases = phase1.get("cases", [])
    report_path = phase1.get("report_path", "data/analysis/solver_consistency_report.json")
    report_abs = (repo_root / report_path).resolve()

    compare_cmd = [
        sys.executable,
        str(repo_root / "scripts" / "compare_solver_consistency.py"),
        "--exago-root",
        args.exago_root,
        "--out",
        str(report_abs),
    ]
    if args.exago_install:
        compare_cmd.extend(["--exago-install", args.exago_install])
    if args.build_profile:
        compare_cmd.extend(["--build-profile", args.build_profile])
    if args.opflow_bin:
        compare_cmd.extend(["--opflow-bin", args.opflow_bin])
    if cases:
        compare_cmd.extend(["--cases", *cases])

    compare_proc = _run(compare_cmd, repo_root)
    if compare_proc.returncode != 0:
        print(
            json.dumps(
                {
                    "ok": False,
                    "stage": "compare_solver_consistency",
                    "returncode": compare_proc.returncode,
                    "stdout": compare_proc.stdout,
                    "stderr": compare_proc.stderr,
                },
                indent=2,
            )
        )
        raise SystemExit(2)

    compare_report = json.loads(report_abs.read_text(encoding="utf-8"))

    audit_cmd = [
        sys.executable,
        str(repo_root / "scripts" / "audit_preservation.py"),
        "--runs-root",
        args.runs_root,
    ]
    audit_proc = _run(audit_cmd, repo_root)
    if audit_proc.returncode != 0:
        print(
            json.dumps(
                {
                    "ok": False,
                    "stage": "audit_preservation",
                    "returncode": audit_proc.returncode,
                    "stdout": audit_proc.stdout,
                    "stderr": audit_proc.stderr,
                },
                indent=2,
            )
        )
        raise SystemExit(2)

    audit_report = json.loads(audit_proc.stdout)

    checks: list[dict] = []

    require_exago = bool(phase1.get("require_exago_converged", True))
    require_pp = bool(phase1.get("require_pandapower_converged", True))
    require_pm = bool(phase1.get("require_powermodels_success", True))
    max_rel = float(phase1.get("max_pandapower_vs_exago_ipopt_relative_diff", 0.01))
    min_attempts = int(phase1.get("min_audited_attempts", 1))
    accepted_pm = set(phase1.get("accepted_powermodels_termination_status", []))

    for case_name, case_payload in compare_report.get("cases", {}).items():
        exago = case_payload.get("exago", {})
        if require_exago:
            for solver in ("IPOPT", "HIOPSPARSE", "HIOP"):
                s = exago.get(solver, {})
                ok = s.get("exit_code") == 0 and s.get("convergence") == "CONVERGED"
                checks.append(
                    {
                        "check": f"{case_name} exago {solver} converged",
                        "ok": ok,
                        "detail": s,
                    }
                )

        if require_pp:
            pp = case_payload.get("pandapower", {})
            ok = pp.get("convergence") == "CONVERGED"
            checks.append(
                {
                    "check": f"{case_name} pandapower converged",
                    "ok": ok,
                    "detail": pp,
                }
            )

        rel_diff = case_payload.get("pandapower_vs_exago_ipopt", {}).get("rel_diff")
        ok = isinstance(rel_diff, float) and rel_diff <= max_rel
        checks.append(
            {
                "check": f"{case_name} pandapower vs exago rel diff <= {max_rel}",
                "ok": ok,
                "detail": {"rel_diff": rel_diff},
            }
        )

    if require_pm:
        pm = compare_report.get("powermodels_status", {})
        term = pm.get("termination_status")
        ok = bool(pm.get("success", False)) and (not accepted_pm or term in accepted_pm)
        checks.append(
            {
                "check": "powermodels success",
                "ok": ok,
                "detail": pm,
            }
        )

    attempts = audit_report.get("attempts", [])
    checks.append(
        {
            "check": f"preservation audit attempts >= {min_attempts}",
            "ok": len(attempts) >= min_attempts,
            "detail": {"attempt_count": len(attempts)},
        }
    )
    checks.append(
        {
            "check": "preservation audit ok",
            "ok": bool(audit_report.get("ok", False)),
            "detail": {"ok": audit_report.get("ok", False)},
        }
    )

    ok = all(c["ok"] for c in checks)
    gate_report = {
        "ok": ok,
        "phase": "phase1",
        "compare_report": str(report_abs),
        "checks": checks,
    }
    print(json.dumps(gate_report, indent=2))
    if not ok:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
