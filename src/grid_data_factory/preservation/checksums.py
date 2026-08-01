from __future__ import annotations

import hashlib
from pathlib import Path


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def write_checksums(attempt_dir: Path) -> Path:
    out = attempt_dir / "manifests" / "checksums.sha256"
    lines = []
    for p in sorted(attempt_dir.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(attempt_dir)
        if rel.as_posix() == "manifests/checksums.sha256":
            continue
        lines.append(f"{sha256_file(p)}  {rel.as_posix()}")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def verify_checksums(attempt_dir: Path) -> tuple[bool, list[str]]:
    path = attempt_dir / "manifests" / "checksums.sha256"
    if not path.exists():
        return False, ["missing manifests/checksums.sha256"]
    errors: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, rel = line.split("  ", 1)
        f = attempt_dir / rel
        if not f.exists():
            errors.append(f"missing file: {rel}")
            continue
        actual = sha256_file(f)
        if actual != digest:
            errors.append(f"checksum mismatch: {rel}")
    return len(errors) == 0, errors
