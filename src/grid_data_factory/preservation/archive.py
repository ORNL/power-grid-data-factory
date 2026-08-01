from __future__ import annotations

import tarfile
from pathlib import Path

from .checksums import sha256_file


def create_archive(attempt_dir: Path, fmt: str = "tar.gz") -> Path:
    if fmt != "tar.gz":
        raise ValueError("Only tar.gz is currently supported")
    out = attempt_dir.parent / f"{attempt_dir.name}.tar.gz"
    with tarfile.open(out, "w:gz") as tf:
        tf.add(attempt_dir, arcname=attempt_dir.name)
    return out


def verify_archive(archive_path: Path) -> dict:
    if not archive_path.exists():
        return {"ok": False, "error": "archive missing"}
    try:
        with tarfile.open(archive_path, "r:gz") as tf:
            members = tf.getmembers()
    except tarfile.ReadError as exc:
        return {"ok": False, "error": f"invalid archive: {exc}"}
    return {
        "ok": True,
        "path": str(archive_path),
        "size_bytes": archive_path.stat().st_size,
        "sha256": sha256_file(archive_path),
        "member_count": len(members),
    }
