#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
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
    p = argparse.ArgumentParser(description="Inspect configured external source availability.")
    p.add_argument("--config", default="configs/sources.yaml")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    cfg_path = (repo_root / args.config).resolve()

    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    sources = cfg.get("sources", {})

    report: dict[str, object] = {
        "ok": True,
        "config": str(cfg_path),
        "sources": {},
    }

    for source_id, spec in sources.items():
        target_dir = (repo_root / str(spec["target_dir"]))
        exists = target_dir.exists()
        repo_url = spec.get("repo")
        pinned = spec.get("git_commit")
        head = _git_head(target_dir) if exists else None

        entry = {
            "target_dir": str(target_dir),
            "exists": exists,
            "repo": repo_url,
            "oedi_record": spec.get("oedi_record"),
            "git_head": head,
            "pinned_commit": pinned,
            "pinned_match": (head == pinned) if (head and pinned) else None,
            "status": "present" if exists else "missing",
        }
        report["sources"][source_id] = entry

    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
