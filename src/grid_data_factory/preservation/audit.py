from __future__ import annotations

from pathlib import Path

TERMINAL_MARKERS = {
    "SUCCESS",
    "FAILED",
    "TIMEOUT",
    "CANCELED",
    "NODE_FAILURE",
    "INFEASIBLE",
    "NONCONVERGENT",
    "INVALID_INPUT",
    "ISLANDED",
    "SOLVER_SUCCESS_PRESERVATION_FAILED",
}


def find_attempt_dirs(runs_root: Path) -> list[Path]:
    return sorted(p for p in runs_root.rglob("attempt_*") if p.is_dir())


def audit_attempt(attempt_dir: Path) -> list[str]:
    issues = []
    required = [
        "run.yaml",
        "command.txt",
        "command.json",
        "environment/environment.json",
        "logs/stdout.log",
        "logs/stderr.log",
        "manifests/artifacts_manifest.json",
        "manifests/checksums.sha256",
    ]
    for rel in required:
        if not (attempt_dir / rel).exists():
            issues.append(f"missing {rel}")

    markers = [m for m in TERMINAL_MARKERS if (attempt_dir / m).exists()]
    if len(markers) != 1:
        issues.append(f"expected exactly one terminal marker, found {len(markers)}")

    return issues


def audit_runs_root(runs_root: Path) -> dict:
    report = {"attempts": []}
    for attempt in find_attempt_dirs(runs_root):
        issues = audit_attempt(attempt)
        report["attempts"].append(
            {
                "attempt": str(attempt),
                "ok": len(issues) == 0,
                "issues": issues,
            }
        )
    report["ok"] = all(a["ok"] for a in report["attempts"]) if report["attempts"] else True
    return report
