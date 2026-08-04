#!/usr/bin/env python3
"""Register reference load-snapshot operating points for a case.

Discovers the seasonal / regime MATPOWER files that ship alongside a case (e.g.
the TAMU New England 250 bundle) and records each as a full per-bus load
snapshot in ``data/operating_point_registry/<case_id>/load_snapshots.json``.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from grid_data_factory.scenarios.load_snapshots import build_snapshot_registry, write_snapshot_registry
    from grid_data_factory.sources.registry import resolve_case_file
except ModuleNotFoundError:
    _repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(_repo_root / "src"))
    from grid_data_factory.scenarios.load_snapshots import build_snapshot_registry, write_snapshot_registry
    from grid_data_factory.sources.registry import resolve_case_file


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Register reference load-snapshot operating points for a case.")
    p.add_argument("--case-id", required=True)
    p.add_argument(
        "--source-dir",
        default=None,
        help="Directory to scan for snapshot .m files (default: the case's extracted/ tree).",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]

    base_file = resolve_case_file(repo_root, args.case_id)
    base_resolved = base_file.resolve()

    if args.source_dir:
        source_dir = Path(args.source_dir)
        source_dir = source_dir if source_dir.is_absolute() else (repo_root / source_dir).resolve()
    else:
        source_dir = base_resolved.parent

    if not source_dir.exists():
        raise SystemExit(f"source directory not found: {source_dir}")

    snapshot_files: list[tuple[str, Path]] = []
    for path in sorted(source_dir.rglob("*.m")):
        if path.resolve() == base_resolved:
            continue
        name_lower = path.name.lower()
        if name_lower.startswith("basecase") or name_lower.startswith("mastercase"):
            continue
        label = str(path.relative_to(source_dir))
        snapshot_files.append((label, path))

    registry = build_snapshot_registry(args.case_id, snapshot_files)
    out_path = write_snapshot_registry(repo_root, args.case_id, registry)

    snaps = registry.get("snapshots") or {}
    print(
        json.dumps(
            {
                "ok": True,
                "case_id": args.case_id,
                "source_dir": str(source_dir),
                "registry": str(out_path),
                "snapshot_count": len(snaps),
                "snapshots": [
                    {
                        "snapshot_id": s["snapshot_id"],
                        "season": s["season"],
                        "voltage_regime": s["voltage_regime"],
                        "difficulty": s["difficulty"],
                        "total_pd": s["total_pd"],
                    }
                    for s in snaps.values()
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
