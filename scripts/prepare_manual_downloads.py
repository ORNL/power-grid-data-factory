#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import yaml


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_sources(config_path: Path) -> dict:
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return cfg.get("sources", {})


def _destination(spec: dict) -> str:
    return str(spec.get("destination") or spec.get("target_dir"))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Prepare manual source download checklists and manifests.")
    p.add_argument("--config", default="configs/sources.yaml")
    p.add_argument("--source", required=True)
    return p.parse_args()


def _acquisition_status(case_dir: Path) -> str:
    raw_dir = case_dir / "raw"
    manifest = case_dir / "source_manifest.yaml"
    extracted = case_dir / "extracted"

    if not raw_dir.exists() or not any(p.is_file() for p in raw_dir.iterdir()):
        return "missing"
    if not manifest.exists():
        return "downloaded_unregistered"
    if not extracted.exists() or not any(p.exists() for p in extracted.rglob("*")):
        return "registered"
    return "extracted"


def _write_manual_markdown(target_dir: Path, spec: dict, cases: list[dict]) -> None:
    lines = [
        "# TAMU Manual Downloads",
        "",
        "Use official case-specific pages and complete all required forms/acknowledgments.",
        "Do not bypass controlled interfaces.",
        "",
    ]

    catalog = spec.get("catalog_url")
    if catalog:
        lines.append(f"Catalog: {catalog}")
        lines.append("")

    for case in cases:
        case_id = str(case.get("case_id"))
        case_dir = target_dir / case_id
        status = _acquisition_status(case_dir)

        lines.extend(
            [
                f"## {case_id}",
                "",
                f"- Case ID: `{case_id}`",
                "- Acquisition method: manual",
                f"- Destination: `external/tamu/{case_id}/raw/`",
                "- Preferred representation: MATPOWER",
                "- Acceptable archive names: original downloaded archive name (no renaming required)",
                "- MATPOWER required: yes",
                "- Dynamic/time-series files: optional",
                f"- Required for initial campaign: {'yes' if case.get('required', False) else 'no'}",
                f"- Checksum status: {'pending' if status == 'missing' else 'recorded after registration'}",
                f"- Acquisition status: {status}",
                f"- Verified case page: {case.get('case_page_url') or 'not set'}",
                "",
                "Open the verified case-specific page, complete any required form or",
                "acknowledgment, and save the original downloaded archive without renaming it.",
                "",
            ]
        )

    (target_dir / "MANUAL_DOWNLOADS.md").write_text("\n".join(lines), encoding="utf-8")


def _write_manual_manifest(target_dir: Path, source_id: str, spec: dict, cases: list[dict]) -> None:
    payload = {
        "source": source_id,
        "source_type": str(spec.get("type")),
        "generated_at": _now(),
        "catalog_url": spec.get("catalog_url"),
        "cases": [],
    }

    for case in cases:
        case_id = str(case.get("case_id"))
        case_dir = target_dir / case_id
        raw_dir = case_dir / "raw"
        raw_files = sorted([p.name for p in raw_dir.iterdir() if p.is_file()]) if raw_dir.exists() else []
        payload["cases"].append(
            {
                "case_id": case_id,
                "required": bool(case.get("required", False)),
                "case_page_url": case.get("case_page_url"),
                "archive_url": case.get("archive_url"),
                "expected_sha256": case.get("expected_sha256"),
                "preferred_file_format": "MATPOWER",
                "requires_matpower": True,
                "optional_dynamic_or_time_series": True,
                "acquisition_status": _acquisition_status(case_dir),
                "raw_files": raw_files,
            }
        )

    (target_dir / "manual_download_manifest.yaml").write_text(
        yaml.safe_dump(payload, sort_keys=False), encoding="utf-8"
    )


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    config_path = (repo_root / args.config).resolve()
    sources = _load_sources(config_path)

    if args.source not in sources:
        raise SystemExit(f"unknown source: {args.source}")

    spec = sources[args.source]
    if str(spec.get("type")) != "manual_catalog":
        raise SystemExit(f"source {args.source} is not type manual_catalog")

    target_dir = (repo_root / _destination(spec)).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    cases = spec.get("cases") or []
    for case in cases:
        case_id = str(case.get("case_id"))
        (target_dir / case_id / "raw").mkdir(parents=True, exist_ok=True)

    _write_manual_markdown(target_dir, spec, cases)
    _write_manual_manifest(target_dir, args.source, spec, cases)

    print(
        yaml.safe_dump(
            {
                "ok": True,
                "source": args.source,
                "manual_downloads": str(target_dir / "MANUAL_DOWNLOADS.md"),
                "manifest": str(target_dir / "manual_download_manifest.yaml"),
            },
            sort_keys=False,
        )
    )


if __name__ == "__main__":
    main()
