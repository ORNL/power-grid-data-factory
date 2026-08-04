"""Reference load-snapshot operating points.

Some source bundles (e.g. the TAMU EPIGRIDS New England 250 case) ship multiple
curated MATPOWER files that share one topology but encode real, non-uniform
operating states (seasonal load shapes at different voltage-limit / difficulty
regimes). These are genuine operating points that the parametric operating-point
generator cannot reproduce, so we register them as full per-bus load snapshots
and apply them verbatim at solve time.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any


def _to_number(token: str) -> float:
    return float(token)


def _parse_bus_matrix(content: str) -> list[list[float]]:
    pattern = re.compile(r"mpc\.bus\s*=\s*\[(.*?)\];", re.DOTALL)
    match = pattern.search(content)
    if not match:
        return []

    cleaned: list[str] = []
    for raw_line in match.group(1).splitlines():
        line = raw_line.split("%", 1)[0].strip()
        if line:
            cleaned.append(line)

    merged = " ".join(cleaned)
    rows: list[list[float]] = []
    if ";" in merged:
        for chunk in merged.split(";"):
            line = chunk.strip()
            if line:
                rows.append([_to_number(tok) for tok in line.split()])
        return rows

    acc: list[str] = []
    for line in cleaned:
        acc.extend(line.split())
        if len(acc) >= 13:
            rows.append([_to_number(tok) for tok in acc])
            acc = []
    if acc:
        rows.append([_to_number(tok) for tok in acc])
    return rows


def read_bus_loads(case_file: Path) -> dict[str, list[float]]:
    """Return ``{bus_id: [pd, qd]}`` for buses carrying nonzero load."""
    text = case_file.read_text(encoding="utf-8", errors="ignore")
    loads: dict[str, list[float]] = {}
    for row in _parse_bus_matrix(text):
        if len(row) < 4:
            continue
        bus_id = str(int(row[0]))
        pd = float(row[2])
        qd = float(row[3])
        if abs(pd) > 0.0 or abs(qd) > 0.0:
            loads[bus_id] = [pd, qd]
    return loads


_SEASONS = ("winter", "spring", "summer", "fall")


def classify_snapshot_name(name: str) -> dict[str, str]:
    """Infer (season, voltage_regime, difficulty) tags from a snapshot filename."""
    stem = Path(name).stem
    lowered = stem.lower()

    season = "unknown"
    for s in _SEASONS:
        if s in lowered:
            season = s
            break

    if "generous" in lowered:
        voltage_regime = "generous"
    elif "tight" in lowered:
        voltage_regime = "tight"
    else:
        voltage_regime = "unknown"

    # Difficulty is encoded by the containing subfolder for the New England set.
    parts = [p.lower() for p in Path(name).parts]
    difficulty = "unknown"
    for level in ("easy", "medium", "hard"):
        if level in parts:
            difficulty = level
            break

    return {"season": season, "voltage_regime": voltage_regime, "difficulty": difficulty}


def _snapshot_id(stem: str, index: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", stem.lower()).strip("_")
    return f"snapshot_{index:03d}_{slug}"


def build_snapshot_registry(
    case_id: str,
    snapshot_files: list[tuple[str, Path]],
) -> dict[str, Any]:
    """Build a snapshot registry payload.

    ``snapshot_files`` is a list of ``(relative_label, path)`` pairs. The
    relative label preserves the source sub-path so difficulty subfolders can be
    classified.
    """
    snapshots: dict[str, Any] = {}
    for index, (label, path) in enumerate(sorted(snapshot_files, key=lambda kv: kv[0].lower())):
        bus_load = read_bus_loads(path)
        if not bus_load:
            continue
        tags = classify_snapshot_name(label)
        stem = Path(label).stem
        total_pd = round(sum(v[0] for v in bus_load.values()), 3)
        total_qd = round(sum(v[1] for v in bus_load.values()), 3)
        sid = _snapshot_id(stem, index)
        snapshots[sid] = {
            "snapshot_id": sid,
            "label": stem,
            "source_relpath": label,
            "season": tags["season"],
            "voltage_regime": tags["voltage_regime"],
            "difficulty": tags["difficulty"],
            "load_bus_count": len(bus_load),
            "total_pd": total_pd,
            "total_qd": total_qd,
            "bus_load": bus_load,
        }
    return {"case_id": case_id, "snapshots": snapshots}


def snapshot_registry_path(repo_root: Path, case_id: str) -> Path:
    return repo_root / "data" / "operating_point_registry" / case_id / "load_snapshots.json"


def write_snapshot_registry(repo_root: Path, case_id: str, registry: dict[str, Any]) -> Path:
    path = snapshot_registry_path(repo_root, case_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(registry, ensure_ascii=True, indent=2), encoding="utf-8")
    return path


@lru_cache(maxsize=None)
def _load_registry_cached(path_str: str, mtime: float) -> dict[str, Any]:
    return json.loads(Path(path_str).read_text(encoding="utf-8"))


def load_snapshot_registry(repo_root: Path, case_id: str) -> dict[str, Any] | None:
    path = snapshot_registry_path(repo_root, case_id)
    if not path.exists():
        return None
    return _load_registry_cached(str(path), path.stat().st_mtime)


def get_snapshot_bus_loads(repo_root: Path, case_id: str, snapshot_id: str) -> dict[str, list[float]] | None:
    registry = load_snapshot_registry(repo_root, case_id)
    if not registry:
        return None
    snap = (registry.get("snapshots") or {}).get(snapshot_id)
    if not snap:
        return None
    return snap.get("bus_load")
