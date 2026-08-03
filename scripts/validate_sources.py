#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import tarfile
import zipfile
from pathlib import Path

import yaml


STATUSES = {
    "MISSING",
    "DOWNLOADED_UNREGISTERED",
    "REGISTERED",
    "EXTRACTED",
    "VALIDATED",
    "CHECKSUM_MISMATCH",
    "UNSUPPORTED_FORMAT",
    "INCOMPLETE_FOR_PF",
    "INCOMPLETE_FOR_OPF",
}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _destination(spec: dict) -> str:
    return str(spec.get("destination") or spec.get("target_dir"))


def _source_type(spec: dict) -> str:
    if spec.get("type"):
        return str(spec.get("type"))
    if spec.get("url") or spec.get("repo"):
        return "git"
    if spec.get("downloads"):
        return "archive_collection"
    return "manual_catalog"


def _is_archive_supported(path: Path) -> bool:
    return zipfile.is_zipfile(path) or tarfile.is_tarfile(path)


def _has_pf_material(base: Path) -> bool:
    exts = {".m", ".raw", ".json"}
    for p in base.rglob("*"):
        if p.is_file() and p.suffix.lower() in exts:
            return True
    return False


def _has_opf_material(base: Path) -> bool:
    # OPF requires cost/function data; infer minimally from common naming.
    patterns = ["gencost", "cost", "opf", "generator_cost"]
    for p in base.rglob("*"):
        if not p.is_file():
            continue
        name = p.name.lower()
        if any(k in name for k in patterns):
            return True
        if p.suffix.lower() == ".m":
            try:
                text = p.read_text(encoding="utf-8", errors="ignore").lower()
            except Exception:  # noqa: BLE001
                continue
            if "gencost" in text:
                return True
    return False


def _validate_case(case_spec: dict, case_root: Path) -> tuple[str, dict]:
    raw_dir = case_root / "raw"
    manifest_path = case_root / "source_manifest.yaml"
    extracted_dir = case_root / "extracted"

    details: dict[str, object] = {
        "case_id": case_spec.get("case_id"),
        "path": str(case_root),
        "required": bool(case_spec.get("required", False)),
    }

    if not raw_dir.exists() or not any(p.is_file() for p in raw_dir.iterdir()):
        return "MISSING", details

    if not manifest_path.exists():
        details["raw_files"] = sorted([p.name for p in raw_dir.iterdir() if p.is_file()])
        return "DOWNLOADED_UNREGISTERED", details

    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    archive = manifest.get("archive") or {}

    archive_rel = archive.get("relative_path")
    if archive_rel:
        archive_path = case_root / str(archive_rel)
    else:
        files = [p for p in raw_dir.iterdir() if p.is_file()]
        archive_path = files[0] if files else None

    if archive_path is None or not archive_path.exists():
        return "MISSING", details

    details["archive"] = str(archive_path)

    if not _is_archive_supported(archive_path):
        return "UNSUPPORTED_FORMAT", details

    expected = case_spec.get("expected_sha256") or archive.get("expected_sha256")
    actual_sha = _sha256(archive_path)
    details["sha256"] = actual_sha
    details["expected_sha256"] = expected

    if expected and actual_sha != str(expected):
        return "CHECKSUM_MISMATCH", details

    if not extracted_dir.exists() or not any(p.exists() for p in extracted_dir.rglob("*")):
        return "REGISTERED", details

    if not _has_pf_material(extracted_dir):
        return "INCOMPLETE_FOR_PF", details

    if not _has_opf_material(extracted_dir):
        return "INCOMPLETE_FOR_OPF", details

    return "VALIDATED", details


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Validate source acquisition completeness and integrity.")
    p.add_argument("--config", default="configs/sources.yaml")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    cfg_path = (repo_root / args.config).resolve()

    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    sources = cfg.get("sources", {})

    result: dict[str, object] = {
        "ok": True,
        "config": str(cfg_path),
        "sources": {},
        "status_set": sorted(STATUSES),
    }

    for source_id, spec in sources.items():
        source_type = _source_type(spec)
        source_root = (repo_root / _destination(spec)).resolve()
        source_entry: dict[str, object] = {
            "source_type": source_type,
            "path": str(source_root),
            "status": "VALIDATED",
            "details": [],
        }

        if source_type == "git":
            if not (source_root / ".git").exists():
                source_entry["status"] = "MISSING"
                source_entry["details"].append({"message": "git repository not present"})
                result["ok"] = False
            result["sources"][source_id] = source_entry
            continue

        if source_type == "archive_collection":
            raw_dir = source_root / "raw"
            has_raw = raw_dir.exists()
            if not has_raw:
                legacy_files = []
                for item in spec.get("downloads") or []:
                    url = str(item.get("url", "")).strip()
                    if not url:
                        continue
                    filename = str(item.get("filename") or Path(url).name)
                    if (source_root / filename).exists():
                        legacy_files.append(filename)

                if legacy_files:
                    source_entry["details"].append(
                        {
                            "message": "using legacy archive layout at source root",
                            "legacy_files": legacy_files,
                        }
                    )

            if not has_raw and not any((source_root / str(item.get("filename") or Path(str(item.get("url", "")).strip()).name)).exists() for item in (spec.get("downloads") or []) if str(item.get("url", "")).strip()):
                source_entry["status"] = "MISSING"
                source_entry["details"].append({"message": "raw directory missing"})
                result["ok"] = False
            else:
                bad_checksum = False
                unsupported = False
                for item in spec.get("downloads") or []:
                    url = str(item.get("url", "")).strip()
                    if not url:
                        continue
                    filename = str(item.get("filename") or Path(url).name)
                    archive = raw_dir / filename
                    if not archive.exists():
                        archive = source_root / filename
                    if not archive.exists():
                        source_entry["status"] = "MISSING"
                        source_entry["details"].append({"filename": filename, "status": "MISSING"})
                        result["ok"] = False
                        continue

                    if not _is_archive_supported(archive):
                        unsupported = True
                        source_entry["details"].append({"filename": filename, "status": "UNSUPPORTED_FORMAT"})
                        continue

                    expected = item.get("expected_sha256")
                    actual = _sha256(archive)
                    if expected and actual != str(expected):
                        bad_checksum = True
                        source_entry["details"].append(
                            {
                                "filename": filename,
                                "status": "CHECKSUM_MISMATCH",
                                "expected_sha256": expected,
                                "sha256": actual,
                            }
                        )
                    else:
                        source_entry["details"].append(
                            {
                                "filename": filename,
                                "status": "VALIDATED",
                                "sha256": actual,
                            }
                        )

                if bad_checksum:
                    source_entry["status"] = "CHECKSUM_MISMATCH"
                    result["ok"] = False
                elif unsupported:
                    source_entry["status"] = "UNSUPPORTED_FORMAT"
                    result["ok"] = False
            result["sources"][source_id] = source_entry
            continue

        if source_type == "manual_catalog":
            cases = spec.get("cases") or []
            overall = "VALIDATED"
            for case in cases:
                case_id = str(case.get("case_id"))
                case_root = source_root / case_id
                status, details = _validate_case(case, case_root)
                details["status"] = status
                source_entry["details"].append(details)

                if status != "VALIDATED":
                    if overall == "VALIDATED":
                        overall = status
                    if bool(case.get("required", False)):
                        result["ok"] = False
            source_entry["status"] = overall
            result["sources"][source_id] = source_entry
            continue

        source_entry["status"] = "UNSUPPORTED_FORMAT"
        source_entry["details"].append({"message": f"unsupported source type: {source_type}"})
        result["ok"] = False
        result["sources"][source_id] = source_entry

    print(json.dumps(result, indent=2))
    if not result["ok"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
