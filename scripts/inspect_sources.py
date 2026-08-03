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


def _destination(spec: dict) -> str:
    return str(spec.get("destination") or spec.get("target_dir"))


def _source_url(spec: dict) -> str | None:
    value = spec.get("repository") or spec.get("url") or spec.get("repo")
    return str(value) if value else None


def _source_type(spec: dict) -> str:
    if spec.get("type"):
        return str(spec.get("type"))
    if spec.get("repository") or spec.get("url") or spec.get("repo"):
        return "git"
    if spec.get("downloads"):
        return "archive_collection"
    return "manual_catalog"


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
        source_type = _source_type(spec)
        target_dir = (repo_root / _destination(spec)).resolve()
        exists = target_dir.exists()

        entry: dict[str, object] = {
            "source_type": source_type,
            "target_dir": str(target_dir),
            "exists": exists,
            "url": _source_url(spec),
            "oedi_record": spec.get("oedi_record"),
            "catalog_url": spec.get("catalog_url"),
            "status": "present" if exists else "missing",
        }

        if source_type == "git":
            head = _git_head(target_dir) if exists else None
            pinned = spec.get("git_commit")
            entry["git_head"] = head
            entry["pinned_commit"] = pinned
            entry["pinned_match"] = (head == pinned) if (head and pinned) else None

        downloads = spec.get("downloads") or []
        if downloads:
            dl_status = []
            for item in downloads:
                url = str(item.get("url", "")).strip()
                if not url:
                    continue
                filename = str(item.get("filename") or Path(url).name)
                raw_path = target_dir / "raw" / filename
                alt_path = target_dir / filename
                file_path = raw_path if raw_path.exists() else alt_path
                dl_status.append(
                    {
                        "url": url,
                        "filename": filename,
                        "exists": file_path.exists(),
                        "size_bytes": file_path.stat().st_size if file_path.exists() else None,
                        "path": str(file_path),
                    }
                )
            entry["downloads"] = dl_status

        cases = spec.get("cases") or []
        if cases:
            case_status = []
            for case in cases:
                case_id = str(case.get("case_id"))
                case_dir = target_dir / case_id
                raw_dir = case_dir / "raw"
                files = sorted([p.name for p in raw_dir.iterdir() if p.is_file()]) if raw_dir.exists() else []
                source_file = case.get("source_file")
                source_file_path = (target_dir / str(source_file)) if source_file else None
                case_status.append(
                    {
                        "case_id": case_id,
                        "grid_family": case.get("grid_family"),
                        "acquisition_mode": case.get("acquisition_mode"),
                        "source_file": source_file,
                        "source_file_exists": bool(source_file_path and source_file_path.exists()),
                        "required": bool(case.get("required", False)),
                        "pglib_equivalent": case.get("pglib_equivalent"),
                        "case_page_url": case.get("case_page_url"),
                        "archive_url": case.get("archive_url"),
                        "expected_sha256": case.get("expected_sha256"),
                        "has_raw_files": len(files) > 0,
                        "raw_files": files,
                    }
                )
            entry["cases"] = case_status

        report["sources"][source_id] = entry

    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
