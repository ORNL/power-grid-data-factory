from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .checksums import sha256_file


def build_artifacts_manifest(attempt_dir: Path) -> Path:
    artifacts = []
    for p in sorted(attempt_dir.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(attempt_dir).as_posix()
        if rel == "manifests/artifacts_manifest.json":
            continue
        st = p.stat()
        artifacts.append(
            {
                "path": rel,
                "role": _infer_role(rel),
                "source": "unknown",
                "size_bytes": st.st_size,
                "sha256": sha256_file(p),
                "compression": "none",
                "required_for_reproduction": True,
                "required_for_training": rel.startswith("normalized/"),
                "required_for_validation": rel.startswith("validation/") or rel.startswith("raw_outputs/"),
                "created_at": datetime.fromtimestamp(st.st_ctime, tz=timezone.utc).isoformat(),
                "modified_at": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
            }
        )

    out = attempt_dir / "manifests" / "artifacts_manifest.json"
    payload = {"schema_version": "1.0", "artifacts": artifacts}
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out


def _infer_role(path: str) -> str:
    if path.startswith("raw_outputs/"):
        return "solver_native_output"
    if path.startswith("normalized/"):
        return "normalized_output"
    if path.startswith("validation/"):
        return "validation_output"
    if path.startswith("logs/"):
        return "execution_log"
    if path.startswith("inputs/"):
        return "resolved_input"
    if path.startswith("environment/"):
        return "environment_metadata"
    if path.startswith("intermediate/"):
        return "intermediate_artifact"
    return "other"
