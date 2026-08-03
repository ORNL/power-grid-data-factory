#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import tarfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import yaml


SUPPORTED_EXTENSIONS = (
    ".zip",
    ".tar",
    ".tar.gz",
    ".tgz",
    ".gz",
    ".bz2",
    ".xz",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_supported_archive(name: str) -> bool:
    lowered = name.lower()
    return any(lowered.endswith(ext) for ext in SUPPORTED_EXTENSIONS)


def _extract_archive(archive: Path, extract_dir: Path) -> tuple[bool, str]:
    extract_dir.mkdir(parents=True, exist_ok=True)
    try:
        if zipfile.is_zipfile(archive):
            with zipfile.ZipFile(archive, "r") as zf:
                zf.extractall(extract_dir)
            return True, "zip"
        if tarfile.is_tarfile(archive):
            with tarfile.open(archive, "r:*") as tf:
                tf.extractall(extract_dir)
            return True, "tar"
    except Exception as exc:  # noqa: BLE001
        return False, f"extract_error:{type(exc).__name__}:{exc}"
    return False, "unsupported_format"


def _load_sources(config_path: Path) -> dict:
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return cfg.get("sources", {})


def _destination(spec: dict) -> str:
    return str(spec.get("destination") or spec.get("target_dir"))


def _find_case_spec(spec: dict, case_id: str) -> dict:
    for case in spec.get("cases") or []:
        if str(case.get("case_id")) == case_id:
            return case
    raise KeyError(case_id)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Register a manually downloaded source archive.")
    p.add_argument("--config", default="configs/sources.yaml")
    p.add_argument("--source", required=True)
    p.add_argument("--case-id", required=True)
    p.add_argument("--file", required=True)
    p.add_argument("--extract", action="store_true")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]

    config_path = (repo_root / args.config).resolve()
    sources = _load_sources(config_path)
    if args.source not in sources:
        raise SystemExit(f"unknown source: {args.source}")

    source_spec = sources[args.source]
    if str(source_spec.get("type")) != "manual_catalog":
        raise SystemExit(f"source {args.source} is not type manual_catalog")

    try:
        case_spec = _find_case_spec(source_spec, args.case_id)
    except KeyError:
        raise SystemExit(f"unknown case-id: {args.case_id}")

    file_path = Path(args.file).resolve()
    if not file_path.exists() or not file_path.is_file():
        raise SystemExit(f"file not found: {file_path}")

    case_root = (repo_root / _destination(source_spec) / args.case_id).resolve()
    raw_dir = case_root / "raw"
    extracted_dir = case_root / "extracted"
    raw_dir.mkdir(parents=True, exist_ok=True)

    target_file = raw_dir / file_path.name
    if file_path != target_file:
        target_file.write_bytes(file_path.read_bytes())

    if not _is_supported_archive(target_file.name):
        raise SystemExit(f"unsupported archive extension: {target_file.name}")

    file_sha = _sha256(target_file)
    file_size = target_file.stat().st_size

    expected_sha = case_spec.get("expected_sha256")
    sha_match = None
    if expected_sha:
        sha_match = file_sha == str(expected_sha)

    extract_status = "not_requested"
    archive_type = "unknown"
    if args.extract:
        ok, mode = _extract_archive(target_file, extracted_dir)
        extract_status = "ok" if ok else mode
        archive_type = mode if ok else "unknown"

    inventory_file = case_root / "inventory.txt"
    file_list = sorted(
        [str(p.relative_to(case_root)) for p in case_root.rglob("*") if p.is_file() and p.name != "inventory.txt"]
    )
    inventory_file.write_text("\n".join(file_list) + "\n", encoding="utf-8")

    checksum_file = case_root / "checksums.sha256"
    checksum_file.write_text(f"{file_sha}  raw/{target_file.name}\n", encoding="utf-8")

    manifest_payload = {
        "source": args.source,
        "source_type": "manual_catalog",
        "case_id": args.case_id,
        "registered_at": _now(),
        "catalog_url": source_spec.get("catalog_url"),
        "case_page_url": case_spec.get("case_page_url"),
        "archive": {
            "original_filename": target_file.name,
            "relative_path": f"raw/{target_file.name}",
            "size_bytes": file_size,
            "sha256": file_sha,
            "expected_sha256": expected_sha,
            "sha256_match": sha_match,
            "archive_type": archive_type,
        },
        "extraction": {
            "requested": bool(args.extract),
            "status": extract_status,
            "destination": str(extracted_dir),
        },
    }

    manifest_path = case_root / "source_manifest.yaml"
    manifest_path.write_text(yaml.safe_dump(manifest_payload, sort_keys=False), encoding="utf-8")

    print(
        yaml.safe_dump(
            {
                "ok": True,
                "source": args.source,
                "case_id": args.case_id,
                "manifest": str(manifest_path),
                "inventory": str(inventory_file),
                "checksums": str(checksum_file),
                "sha256": file_sha,
                "expected_sha256": expected_sha,
                "sha256_match": sha_match,
                "extract_status": extract_status,
            },
            sort_keys=False,
        )
    )


if __name__ == "__main__":
    main()
