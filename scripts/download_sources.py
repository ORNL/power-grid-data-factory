#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

try:
    from grid_data_factory.sources.download import (
        check_required_cases,
        destination_dir,
        prepare_manual_catalog,
        process_archive_source,
        process_git_source,
        source_type,
        source_url,
    )
    from grid_data_factory.storage.attempt_io import utc_now_iso
except ModuleNotFoundError:
    _repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(_repo_root / "src"))
    from grid_data_factory.sources.download import (
        check_required_cases,
        destination_dir,
        prepare_manual_catalog,
        process_archive_source,
        process_git_source,
        source_type,
        source_url,
    )
    from grid_data_factory.storage.attempt_io import utc_now_iso


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Download and initialize configured external sources.")
    p.add_argument("--config", default="configs/sources.yaml")
    p.add_argument("--clone-missing", action="store_true")
    p.add_argument("--checkout-pinned", action="store_true")
    p.add_argument("--create-manual-dirs", action="store_true")
    p.add_argument("--download-files", action="store_true")
    p.add_argument("--extract-archives", action="store_true")
    p.add_argument("--out", default="data/inputs/source_provenance.json")
    p.add_argument("--require-case", action="append", default=[])
    return p.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    cfg_path = (repo_root / args.config).resolve()

    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    sources = cfg.get("sources", {})

    report: dict[str, object] = {
        "ok": True,
        "generated_at": utc_now_iso(),
        "config": str(cfg_path),
        "sources": {},
    }

    require_cases = {c.strip() for c in args.require_case if c.strip()}

    for source_id, spec in sources.items():
        stype = source_type(spec)
        dest_rel = destination_dir(spec)
        target_dir = (repo_root / dest_rel).resolve()

        source_report: dict[str, object] = {
            "source_type": stype,
            "target_dir": str(target_dir),
            "exists": target_dir.exists(),
            "url": source_url(spec),
            "oedi_record": spec.get("oedi_record"),
            "catalog_url": spec.get("catalog_url"),
            "actions": [],
            "errors": [],
        }

        if stype == "git":
            process_git_source(repo_root, spec, target_dir, args, source_report, report)
        elif stype in {"archive_collection", "manual_catalog"}:
            if stype == "manual_catalog" and args.create_manual_dirs and not target_dir.exists():
                prepare_manual_catalog(spec, target_dir)
                source_report["actions"].append("created_manual_target_dir")
                source_report["exists"] = True

            check_required_cases(spec, require_cases, source_report, report)
            process_archive_source(repo_root, source_id, spec, target_dir, args, source_report, report)
        else:
            source_report["errors"].append(f"unsupported_source_type:{stype}")
            report["ok"] = False

        source_report["exists"] = target_dir.exists()
        report["sources"][source_id] = source_report

    out_path = (repo_root / args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(json.dumps(report, indent=2))
    if not report["ok"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
