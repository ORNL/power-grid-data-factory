#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tarfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import yaml


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _destination(spec: dict) -> str:
    return str(spec.get("destination") or spec.get("target_dir"))


def _source_url(spec: dict) -> str | None:
    value = spec.get("repository") or spec.get("url") or spec.get("repo")
    return str(value) if value else None


def _git_head(repo_dir: Path) -> str | None:
    if not (repo_dir / ".git").exists():
        return None
    proc = _run(["git", "rev-parse", "HEAD"], repo_dir)
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _download_with_curl(url: str, out_file: Path, repo_root: Path) -> tuple[bool, str]:
    out_file.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "curl",
        "-fL",
        "--retry",
        "5",
        "--retry-delay",
        "5",
        "--continue-at",
        "-",
        url,
        "-o",
        str(out_file),
    ]
    proc = _run(cmd, repo_root)
    ok = proc.returncode == 0
    stderr = (proc.stderr or "").strip()
    return ok, stderr


def _normalize_legacy_archive_location(target_dir: Path, filename: str, raw_dir: Path) -> Path:
    raw_path = raw_dir / filename
    if raw_path.exists():
        return raw_path

    legacy_path = target_dir / filename
    if legacy_path.exists():
        raw_dir.mkdir(parents=True, exist_ok=True)
        legacy_path.rename(raw_path)
        return raw_path

    return raw_path


def _extract_archive(archive_path: Path, extract_root: Path) -> tuple[bool, str | None, str | None]:
    name = archive_path.name
    stem = archive_path.stem
    if name.endswith(".tar.gz") or name.endswith(".tgz"):
        if name.endswith(".tar.gz"):
            stem = name[: -len(".tar.gz")]
        else:
            stem = name[: -len(".tgz")]
    dest = extract_root / stem
    dest.mkdir(parents=True, exist_ok=True)

    try:
        if zipfile.is_zipfile(archive_path):
            with zipfile.ZipFile(archive_path, "r") as zf:
                zf.extractall(dest)
            return True, str(dest), "zip"
        if tarfile.is_tarfile(archive_path):
            with tarfile.open(archive_path, "r:*") as tf:
                tf.extractall(dest)
            return True, str(dest), "tar"
    except Exception as exc:  # noqa: BLE001
        return False, None, f"extract_error:{type(exc).__name__}:{exc}"

    return False, None, "unsupported_archive"


def _write_yaml(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _load_existing_manifest(path: Path) -> dict:
    if not path.exists():
        return {"source": {}, "archives": []}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {"source": {}, "archives": []}


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


def _prepare_manual_catalog(spec: dict, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    note = target_dir / "README.manual_source.txt"
    if note.exists():
        return
    lines = [
        "This source uses manual acquisition through case-specific pages.",
        f"Catalog: {spec.get('catalog_url')}",
        "",
        "Do not bypass forms, acknowledgments, or selection interfaces.",
    ]
    note.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _source_type(spec: dict) -> str:
    if spec.get("type"):
        return str(spec.get("type"))
    if spec.get("repository") or spec.get("repo") or spec.get("url"):
        return "git"
    if spec.get("downloads"):
        return "archive_collection"
    return "manual_catalog"


def _process_git_source(repo_root: Path, spec: dict, target_dir: Path, args: argparse.Namespace, source_report: dict, report: dict) -> None:
    repo_url = _source_url(spec)
    if not repo_url:
        source_report["errors"].append("missing_repository_url")
        report["ok"] = False
        return
    recursive = bool(spec.get("recursive", False))

    if not target_dir.exists() and args.clone_missing:
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        cmd = ["git", "clone"]
        if recursive:
            cmd.append("--recurse-submodules")
        cmd.extend([repo_url, str(target_dir)])
        proc = _run(cmd, repo_root)
        if proc.returncode == 0:
            source_report["actions"].append("cloned")
        else:
            source_report["errors"].append("clone_failed")
            source_report["clone_stderr"] = proc.stderr
            report["ok"] = False

    pinned = spec.get("git_commit")
    if target_dir.exists() and pinned and args.checkout_pinned:
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


def _iter_download_specs(spec: dict) -> list[dict]:
    entries: list[dict] = []
    for item in spec.get("downloads") or []:
        entry = dict(item)
        entry.setdefault("acquisition_mode", "direct")
        entries.append(entry)

    for case in spec.get("cases") or []:
        mode = str(case.get("acquisition_mode") or "manual")
        if case.get("archive_url") and mode == "direct":
            entry = {
                "url": str(case["archive_url"]),
                "filename": case.get("filename"),
                "expected_sha256": case.get("expected_sha256"),
                "case_id": case.get("case_id"),
                "case_page_url": case.get("case_page_url"),
                "acquisition_mode": "direct",
            }
            entries.append(entry)
    return entries


def _process_archive_source(
    repo_root: Path,
    source_id: str,
    spec: dict,
    target_dir: Path,
    args: argparse.Namespace,
    source_report: dict,
    report: dict,
) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    source_report["exists"] = True

    manifest_path = target_dir / "source_manifest.yaml"
    existing = _load_existing_manifest(manifest_path)
    existing_by_file = {a.get("filename"): a for a in existing.get("archives", []) if isinstance(a, dict)}

    downloads = _iter_download_specs(spec)
    archives_meta: list[dict] = []

    raw_dir = target_dir / "raw"
    extract_dir = target_dir / "extracted"
    raw_dir.mkdir(parents=True, exist_ok=True)
    extract_dir.mkdir(parents=True, exist_ok=True)

    for item in downloads:
        url = str(item.get("url", "")).strip()
        if not url:
            continue
        filename = str(item.get("filename") or Path(url).name)
        out_file = _normalize_legacy_archive_location(target_dir, filename, raw_dir)
        expected_sha = item.get("expected_sha256")
        source_page = item.get("source_page_url") or spec.get("oedi_record") or spec.get("catalog_url")

        file_meta: dict[str, object] = {
            "filename": filename,
            "path": str(out_file),
            "url": url,
            "source_page": source_page,
            "case_id": item.get("case_id"),
            "acquisition_mode": item.get("acquisition_mode", "direct"),
            "exists": out_file.exists(),
        }

        already_verified = False
        if out_file.exists():
            sha = _sha256(out_file)
            file_meta["sha256"] = sha
            file_meta["size_bytes"] = out_file.stat().st_size
            if expected_sha:
                file_meta["expected_sha256"] = str(expected_sha)
                file_meta["sha256_match"] = sha == str(expected_sha)
                already_verified = bool(file_meta["sha256_match"])
            elif filename in existing_by_file and existing_by_file[filename].get("sha256") == sha:
                already_verified = True

        if args.download_files and not already_verified:
            if not out_file.exists() or (expected_sha and not file_meta.get("sha256_match", False)):
                ok, err = _download_with_curl(url, out_file, repo_root)
                if ok:
                    source_report["actions"].append(f"downloaded:{filename}")
                else:
                    source_report["errors"].append(f"download_failed:{filename}")
                    file_meta["download_error"] = err
                    report["ok"] = False

            if out_file.exists():
                sha = _sha256(out_file)
                file_meta["sha256"] = sha
                file_meta["size_bytes"] = out_file.stat().st_size
                file_meta["downloaded_at"] = _now()
                if expected_sha:
                    file_meta["expected_sha256"] = str(expected_sha)
                    file_meta["sha256_match"] = sha == str(expected_sha)
                    if not file_meta["sha256_match"]:
                        source_report["errors"].append(f"checksum_mismatch:{filename}")
                        report["ok"] = False
        elif already_verified:
            source_report["actions"].append(f"skipped_verified:{filename}")

        if out_file.exists() and args.extract_archives:
            ok, extracted_path, mode = _extract_archive(out_file, extract_dir)
            if ok:
                file_meta["extracted_to"] = extracted_path
                file_meta["archive_type"] = mode
                source_report["actions"].append(f"extracted:{filename}")
            else:
                file_meta["extract_error"] = mode
                if mode != "unsupported_archive":
                    source_report["errors"].append(f"extract_failed:{filename}")
                    report["ok"] = False

        archives_meta.append(file_meta)

    source_manifest = {
        "source": {
            "source_id": source_id,
            "source_type": _source_type(spec),
            "destination": str(target_dir),
            "generated_at": _now(),
            "oedi_record": spec.get("oedi_record"),
            "catalog_url": spec.get("catalog_url"),
        },
        "archives": archives_meta,
    }
    _write_yaml(manifest_path, source_manifest)
    source_report["source_manifest"] = str(manifest_path)
    source_report["downloads"] = archives_meta


def _check_required_cases(spec: dict, require_cases: set[str], source_report: dict, report: dict) -> None:
    if not require_cases:
        return

    cases = spec.get("cases") or []
    for case in cases:
        case_id = str(case.get("case_id"))
        if case_id not in require_cases:
            continue
        mode = str(case.get("acquisition_mode") or "manual")
        has_archive = bool(case.get("archive_url"))
        if mode != "direct" or not has_archive:
            source_report["errors"].append(f"required_case_unavailable:{case_id}")
            report["ok"] = False


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    cfg_path = (repo_root / args.config).resolve()

    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    sources = cfg.get("sources", {})

    report: dict[str, object] = {
        "ok": True,
        "generated_at": _now(),
        "config": str(cfg_path),
        "sources": {},
    }

    require_cases = {c.strip() for c in args.require_case if c.strip()}

    for source_id, spec in sources.items():
        source_type = _source_type(spec)
        dest_rel = _destination(spec)
        target_dir = (repo_root / dest_rel).resolve()

        source_report: dict[str, object] = {
            "source_type": source_type,
            "target_dir": str(target_dir),
            "exists": target_dir.exists(),
            "url": _source_url(spec),
            "oedi_record": spec.get("oedi_record"),
            "catalog_url": spec.get("catalog_url"),
            "actions": [],
            "errors": [],
        }

        if source_type == "git":
            _process_git_source(repo_root, spec, target_dir, args, source_report, report)
        elif source_type in {"archive_collection", "manual_catalog"}:
            if source_type == "manual_catalog" and args.create_manual_dirs and not target_dir.exists():
                _prepare_manual_catalog(spec, target_dir)
                source_report["actions"].append("created_manual_target_dir")
                source_report["exists"] = True

            _check_required_cases(spec, require_cases, source_report, report)
            _process_archive_source(repo_root, source_id, spec, target_dir, args, source_report, report)
        else:
            source_report["errors"].append(f"unsupported_source_type:{source_type}")
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
