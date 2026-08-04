#!/usr/bin/env python3
"""Remove generated data so the workspace can return to a clean slate.

Deletion is scoped by the lifecycle tiers defined in
``src/grid_data_factory/storage/paths.py``:

- ``data/outputs/`` and ``data/scratch/`` are always safe to remove (disposable
  run products and ephemeral logs/temp files).
- ``data/derived/`` is rebuildable but only cleared with ``--include-derived``.
- ``data/inputs/`` and ``data/reports/`` are protected and never touched.

Each tier root and its ``.gitkeep`` marker are preserved so the tracked layout
survives. Use ``--dry-run`` to preview what would be removed.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

try:
    from grid_data_factory.storage import paths
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from grid_data_factory.storage import paths


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    p.add_argument("--include-derived", action="store_true", help="Also clear the rebuildable data/derived/ tier.")
    p.add_argument("--dry-run", action="store_true", help="List what would be removed without deleting.")
    return p.parse_args()


def _clean_tier(repo_root: Path, tier_rel: str, dry_run: bool) -> int:
    root = repo_root / tier_rel
    if not root.exists():
        return 0
    removed = 0
    for child in sorted(root.iterdir()):
        if child.name == ".gitkeep":
            continue
        removed += 1
        print(f"[{'dry-run' if dry_run else 'remove'}] {child.relative_to(repo_root)}")
        if dry_run:
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    return removed


def main() -> None:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()

    tiers = list(paths.CLEANABLE)
    if args.include_derived:
        tiers += list(paths.REBUILDABLE)

    for protected in paths.PROTECTED:
        if protected in tiers:
            raise SystemExit(f"refusing to clean protected tier: {protected}")

    total = 0
    for tier_rel in tiers:
        total += _clean_tier(repo_root, tier_rel, args.dry_run)

    verb = "would remove" if args.dry_run else "removed"
    print(f"[clean] {verb} {total} entr{'y' if total == 1 else 'ies'} across: {', '.join(tiers)}")


if __name__ == "__main__":
    main()
