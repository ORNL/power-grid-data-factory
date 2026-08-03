#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import yaml


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def _git_head(repo_dir: Path) -> str | None:
    if not (repo_dir / ".git").exists():
        return None
    proc = _run(["git", "rev-parse", "HEAD"], repo_dir)
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Download and initialize configured external sources.")
    p.add_argument("--config", default="configs/sources.yaml")
    p.add_argument("--clone-missing", action="store_true")
    p.add_argument("--checkout-pinned", action="store_true")
    p.add_argument("--create-manual-dirs", action="store_true")
    p.add_argument("--out", default="data/imported/source_provenance.json")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]

    cfg_path = (repo_root / args.config).resolve()
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    sources = cfg.get("sources", {})

    report: dict[str, object] = {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": str(cfg_path),
        "sources": {},
    }

    for source_id, spec in sources.items():
        target_dir = (repo_root / str(spec["target_dir"]))
        source_report: dict[str, object] = {
            "target_dir": str(target_dir),
            "exists": target_dir.exists(),
            "repo": spec.get("repo"),
            "oedi_record": spec.get("oedi_record"),
            "actions": [],
            "errors": [],
        }

        repo_url = spec.get("repo")
        if repo_url and not target_dir.exists() and args.clone_missing:
            target_dir.parent.mkdir(parents=True, exist_ok=True)
            proc = _run(["git", "clone", str(repo_url), str(target_dir)], repo_root)
            if proc.returncode == 0:
                source_report["actions"].append("cloned")
                source_report["exists"] = True
            else:
                source_report["errors"].append("clone_failed")
                source_report["clone_stderr"] = proc.stderr
                report["ok"] = False

        if not repo_url and not target_dir.exists() and args.create_manual_dirs:
            target_dir.mkdir(parents=True, exist_ok=True)
            note = target_dir / "README.manual_source.txt"
            if not note.exists():
                lines = [
                    f"Source: {source_id}",
                    f"Created: {datetime.now(timezone.utc).isoformat()}",
                    "This source requires manual download/import.",
                ]
                if spec.get("oedi_record"):
                    lines.append(f"Reference: {spec['oedi_record']}")
                note.write_text("\n".join(lines) + "\n", encoding="utf-8")
            source_report["actions"].append("created_manual_target_dir")
            source_report["exists"] = True

        pinned = spec.get("git_commit")
        if repo_url and target_dir.exists() and pinned and args.checkout_pinned:
            proc = _run(["git", "checkout", str(pinned)], target_dir)
            if proc.returncode == 0:
                source_report["actions"].append("checked_out_pinned_commit")
            else:
                source_report["errors"].append("checkout_failed")
                source_report["checkout_stderr"] = proc.stderr
                report["ok"] = False

        source_report["git_head"] = _git_head(target_dir) if target_dir.exists() else None
        if pinned:
            source_report["pinned_commit"] = str(pinned)
            source_report["pinned_match"] = source_report["git_head"] == str(pinned)

        report["sources"][source_id] = source_report

    out_path = (repo_root / args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(json.dumps(report, indent=2))
    if not report["ok"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
