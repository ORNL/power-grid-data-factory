#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import yaml


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _copy_text(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(src.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Export tracked reproducibility bundle for manual source cases.")
    p.add_argument("--config", default="configs/sources.yaml")
    p.add_argument("--manual-root", default="external/tamu")
    p.add_argument("--out-root", default="data/inputs/manual_sources")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]

    config_path = (repo_root / args.config).resolve()
    manual_root = (repo_root / args.manual_root).resolve()
    out_root = (repo_root / args.out_root).resolve()
    out_cases = out_root / "cases"
    out_cases.mkdir(parents=True, exist_ok=True)

    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    tamu = ((cfg.get("sources") or {}).get("tamu") or {})
    cases = [c for c in tamu.get("cases") or [] if str(c.get("acquisition_mode") or "manual") == "manual"]

    for name in ["MANUAL_DOWNLOADS.md", "manual_download_manifest.yaml"]:
        src = manual_root / name
        if src.exists():
            _copy_text(src, out_root / name)

    summary = {
        "generated_from": str(manual_root.relative_to(repo_root)),
        "generated_at": None,
        "policy_note": "Manual source archives remain under external/tamu (gitignored). This bundle stores reproducibility metadata, manifests, checksums, and inventories.",
        "cases": [],
    }

    for case in cases:
        case_id = str(case.get("case_id"))
        case_root = manual_root / case_id
        dst_case = out_cases / case_id
        dst_case.mkdir(parents=True, exist_ok=True)

        copied = {}
        for fname in ["source_manifest.yaml", "checksums.sha256", "inventory.txt"]:
            src = case_root / fname
            if src.exists():
                dst = dst_case / fname
                _copy_text(src, dst)
                copied[fname] = str(dst.relative_to(repo_root))

        archives = []
        raw_dir = case_root / "raw"
        if raw_dir.exists():
            for p in sorted(raw_dir.iterdir()):
                if not p.is_file():
                    continue
                archives.append(
                    {
                        "filename": p.name,
                        "size_bytes": p.stat().st_size,
                        "sha256": _sha256(p),
                        "relative_external_path": str(p.relative_to(repo_root)),
                    }
                )

        extracted_count = 0
        extracted_dir = case_root / "extracted"
        if extracted_dir.exists():
            extracted_count = sum(1 for p in extracted_dir.rglob("*") if p.is_file())

        summary["cases"].append(
            {
                "case_id": case_id,
                "required": bool(case.get("required", False)),
                "acquisition_mode": case.get("acquisition_mode"),
                "grid_family": case.get("grid_family"),
                "metadata": {
                    "source_repository": case.get("source_repository"),
                    "source_origin": case.get("source_origin"),
                    "source_case_name": case.get("source_case_name"),
                    "bus_count": case.get("bus_count"),
                    "exact_source_topology": case.get("exact_source_topology"),
                    "exact_tamu_topology": case.get("exact_tamu_topology"),
                    "non_equivalent_similar_size_cases": case.get("non_equivalent_similar_size_cases") or [],
                },
                "copied_files": copied,
                "archives": archives,
                "archive_count": len(archives),
                "extracted_file_count": extracted_count,
                "present": case_root.exists(),
            }
        )

    summary["generated_at"] = datetime.now(timezone.utc).isoformat()
    (out_root / "repro_bundle.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(json.dumps({"ok": True, "case_count": len(summary["cases"]), "out_root": str(out_root)}, indent=2))


if __name__ == "__main__":
    main()
