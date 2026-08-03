#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import yaml

REQUIRED_CASE_FIELDS = [
    "case_id",
    "grid_family",
    "source_repository",
    "source_origin",
    "source_file",
    "source_case_name",
    "bus_count",
    "exact_source_topology",
    "related_case_ids",
    "equivalent_case_ids",
    "non_equivalent_similar_size_cases",
]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _destination(spec: dict) -> str:
    return str(spec.get("destination") or spec.get("target_dir"))


def _load_config(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _has_pf_material(base: Path) -> bool:
    if not base.exists():
        return False
    exts = {".m", ".raw", ".json"}
    for p in base.rglob("*"):
        if p.is_file() and p.suffix.lower() in exts:
            return True
    return False


def _has_opf_material(base: Path) -> bool:
    if not base.exists():
        return False

    for p in base.rglob("*"):
        if not p.is_file():
            continue
        name = p.name.lower()
        if "gencost" in name or "opf" in name or "cost" in name:
            return True
        if p.suffix.lower() == ".m":
            text = p.read_text(encoding="utf-8", errors="ignore").lower()
            if "gencost" in text:
                return True
    return False


def _scan_manual_case(case_root: Path) -> tuple[bool, str | None, bool, bool, bool]:
    raw_dir = case_root / "raw"
    extracted_dir = case_root / "extracted"

    candidates = []
    if raw_dir.exists():
        candidates.extend(sorted([p for p in raw_dir.iterdir() if p.is_file()]))

    local_available = len(candidates) > 0
    checksum = _sha256(candidates[0]) if candidates else None

    if extracted_dir.exists() and any(p.is_file() for p in extracted_dir.rglob("*")):
        pf_ready = _has_pf_material(extracted_dir)
        opf_ready = _has_opf_material(extracted_dir)
    else:
        pf_ready = False
        opf_ready = False

    return local_available, checksum, pf_ready, opf_ready, opf_ready


def _scan_file_case(source_file_path: Path) -> tuple[bool, str | None, bool, bool, bool]:
    if not source_file_path.exists():
        return False, None, False, False, False

    checksum = _sha256(source_file_path)
    pf_ready = _has_pf_material(source_file_path.parent)
    opf_ready = _has_opf_material(source_file_path.parent)
    return True, checksum, pf_ready, opf_ready, opf_ready


def _audit_case(repo_root: Path, source_id: str, spec: dict, case: dict) -> dict:
    target_dir = (repo_root / _destination(spec)).resolve()
    case_id = str(case.get("case_id"))
    acquisition_mode = str(case.get("acquisition_mode") or "manual")

    missing_fields = [key for key in REQUIRED_CASE_FIELDS if key not in case]

    source_file = case.get("source_file")
    source_file_path = (target_dir / str(source_file)) if source_file else None

    if source_file_path is not None:
        local_available, checksum, pf_ready, dc_ready, ac_ready = _scan_file_case(source_file_path)
        expected_source_file = str(source_file)
    else:
        case_root = target_dir / case_id
        local_available, checksum, pf_ready, dc_ready, ac_ready = _scan_manual_case(case_root)
        expected_source_file = "manual archive in external/tamu/<case_id>/raw/"

    return {
        "case_id": case_id,
        "grid_family": case.get("grid_family"),
        "acquisition_mode": acquisition_mode,
        "source_repository": case.get("source_repository") or source_id,
        "source_origin": case.get("source_origin"),
        "expected_source_file": expected_source_file,
        "local_availability": local_available,
        "checksum": checksum,
        "exact_tamu_correspondence": case.get("exact_tamu_topology"),
        "pglib_equivalent": case.get("pglib_equivalent"),
        "similar_size_non_equivalent_cases": case.get("non_equivalent_similar_size_cases") or [],
        "pf_readiness": pf_ready,
        "dc_opf_readiness": dc_ready,
        "ac_opf_readiness": ac_ready,
        "metadata_complete": len(missing_fields) == 0,
        "missing_metadata_fields": missing_fields,
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Audit case-source correspondence and local availability.")
    p.add_argument("--config", default="configs/sources.yaml")
    p.add_argument("--format", choices=["text", "json"], default="text")
    return p.parse_args()


def _print_text(rows: list[dict]) -> None:
    for row in rows:
        print(f"{row['case_id']}")
        print(f"  grid family: {row['grid_family']}")
        print(f"  acquisition mode: {row['acquisition_mode']}")
        print(f"  source repository: {row['source_repository']}")
        print(f"  expected source file: {row['expected_source_file']}")
        print(f"  local availability: {'yes' if row['local_availability'] else 'no'}")
        print(f"  checksum: {row['checksum'] or 'n/a'}")
        print(f"  exact TAMU correspondence: {'yes' if row['exact_tamu_correspondence'] else 'no'}")
        print(f"  PGLib equivalent: {row['pglib_equivalent'] if row['pglib_equivalent'] is not None else 'none'}")
        similar = row['similar_size_non_equivalent_cases']
        print(f"  similar-size but non-equivalent cases: {', '.join(similar) if similar else 'none'}")
        print(f"  PF readiness: {'ready' if row['pf_readiness'] else 'not-ready'}")
        print(f"  DC-OPF readiness: {'ready' if row['dc_opf_readiness'] else 'not-ready'}")
        print(f"  AC-OPF readiness: {'ready' if row['ac_opf_readiness'] else 'not-ready'}")
        if not row["metadata_complete"]:
            print(f"  metadata missing: {', '.join(row['missing_metadata_fields'])}")
        print()


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    config_path = (repo_root / args.config).resolve()
    cfg = _load_config(config_path)

    rows: list[dict] = []
    for source_id, spec in (cfg.get("sources") or {}).items():
        for case in spec.get("cases") or []:
            rows.append(_audit_case(repo_root, source_id, spec, case))

    rows.sort(key=lambda x: x["case_id"])

    report = {
        "ok": all(r["metadata_complete"] for r in rows),
        "config": str(config_path),
        "case_count": len(rows),
        "cases": rows,
    }

    if args.format == "json":
        print(json.dumps(report, indent=2))
    else:
        _print_text(rows)


if __name__ == "__main__":
    main()
