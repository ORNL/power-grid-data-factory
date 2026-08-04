"""Canonical case registry derived from configs/sources.yaml.

Maps a canonical ``case_id`` to its on-disk MATPOWER file, grid family, and
dataset, so downstream tooling never has to guess filenames from case ids.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml


@dataclass(frozen=True)
class CaseInfo:
    case_id: str
    grid_family: str
    dataset: str
    source_key: str
    source_origin: str
    destination: str
    source_file: str | None
    bus_count: int | None
    acquisition_mode: str


def _load_raw_cases(repo_root: Path) -> dict[str, dict[str, object]]:
    text = (repo_root / "configs" / "sources.yaml").read_text(encoding="utf-8")
    payload = yaml.safe_load(text) or {}
    out: dict[str, dict[str, object]] = {}
    for source_key, spec in (payload.get("sources") or {}).items():
        if not isinstance(spec, dict):
            continue
        dest = spec.get("destination")
        for case in spec.get("cases") or []:
            if not isinstance(case, dict):
                continue
            cid = str(case.get("case_id"))
            merged = dict(case)
            merged["source_key"] = source_key
            merged["destination"] = dest
            out[cid] = merged
    return out



@lru_cache(maxsize=8)
def load_case_registry(repo_root: str) -> dict[str, CaseInfo]:
    root = Path(repo_root)
    raw = _load_raw_cases(root)
    registry: dict[str, CaseInfo] = {}
    for cid, rec in raw.items():
        grid_family = str(rec.get("grid_family") or "unknown")
        bus_count = rec.get("bus_count")
        registry[cid] = CaseInfo(
            case_id=cid,
            grid_family=grid_family,
            dataset=grid_family,
            source_key=str(rec.get("source_key") or "unknown"),
            source_origin=str(rec.get("source_origin") or "unknown"),
            destination=str(rec.get("destination") or "external/pglib-opf"),
            source_file=(str(rec["source_file"]) if rec.get("source_file") else None),
            bus_count=(int(bus_count) if isinstance(bus_count, int) else None),
            acquisition_mode=str(rec.get("acquisition_mode") or "unknown"),
        )
    return registry


def get_case_info(repo_root: Path, case_id: str) -> CaseInfo | None:
    return load_case_registry(str(repo_root)).get(case_id)


def grid_family_for(repo_root: Path, case_id: str) -> str:
    info = get_case_info(repo_root, case_id)
    return info.grid_family if info else "unknown"


def dataset_for(repo_root: Path, case_id: str) -> str:
    info = get_case_info(repo_root, case_id)
    return info.dataset if info else "unknown"


def bus_count_for(repo_root: Path, case_id: str) -> int | None:
    info = get_case_info(repo_root, case_id)
    return info.bus_count if info else None


def _find_manual_matpower_case(case_root: Path) -> Path | None:
    extracted = case_root / "extracted"
    if not extracted.exists():
        return None
    preferred = sorted(extracted.glob("case_*.m"))
    if preferred:
        return preferred[0]
    for p in sorted(extracted.glob("*.m")):
        n = p.name.lower()
        if n.startswith("contab") or n.startswith("scenarios"):
            continue
        return p
    return None


def resolve_case_file(repo_root: Path, case_id: str) -> Path:
    """Resolve the on-disk MATPOWER file for a canonical case id.

    Falls back to the legacy ``external/pglib-opf/<case_id>.m`` layout only when
    the case is not present in the registry.
    """
    info = get_case_info(repo_root, case_id)
    if info is None:
        return (repo_root / "external" / "pglib-opf" / f"{case_id}.m").resolve()

    if info.source_file:
        return (repo_root / info.destination / info.source_file).resolve()

    found = _find_manual_matpower_case((repo_root / info.destination / case_id).resolve())
    if found:
        return found
    return (repo_root / info.destination / case_id / "extracted" / f"{case_id}.m").resolve()
