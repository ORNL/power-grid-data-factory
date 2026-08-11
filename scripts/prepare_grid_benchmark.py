#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import time

try:
    from grid_data_factory.campaigns.benchmark_selection import load_completed_candidate_ids, select_unfinished_candidates
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from grid_data_factory.campaigns.benchmark_selection import load_completed_candidate_ids, select_unfinished_candidates


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Select deterministic unfinished candidates for per-grid solver benchmarks.")
    parser.add_argument("--candidates-jsonl", required=True, help="Campaign selected-candidate JSONL.")
    parser.add_argument("--partial-runs-tree", required=True, help="Partial map/reduce tree containing samples.jsonl files.")
    parser.add_argument("--out-dir", required=True, help="Directory for one benchmark candidate shard per grid.")
    parser.add_argument("--per-case", type=int, default=20, help="Maximum unfinished benchmark candidates selected per grid.")
    parser.add_argument("--seed", type=int, default=20260810, help="Seed for deterministic hash-priority sampling.")
    parser.add_argument("--force", action="store_true", help="Replace an existing benchmark selection in out-dir.")
    return parser.parse_args()


def _case_slug(case_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", case_id).strip("._") or "case"


def main() -> None:
    args = parse_args()
    if args.per_case <= 0:
        raise SystemExit("--per-case must be > 0")

    candidates_path = Path(args.candidates_jsonl).resolve()
    runs_tree = Path(args.partial_runs_tree).resolve()
    out_dir = Path(args.out_dir).resolve()
    manifest_path = out_dir / "benchmark_selection_manifest.json"
    if manifest_path.exists() and not args.force:
        raise SystemExit(f"Selection already exists: {manifest_path}; pass --force to replace it")
    if not candidates_path.is_file():
        raise SystemExit(f"Candidate stream not found: {candidates_path}")
    if not runs_tree.is_dir():
        raise SystemExit(f"Partial runs tree not found: {runs_tree}")

    out_dir.mkdir(parents=True, exist_ok=True)
    if args.force:
        for path in out_dir.glob("case_*.jsonl"):
            path.unlink()
        for name in ("benchmark_shards.txt", "benchmark_selection_manifest.json"):
            path = out_dir / name
            if path.exists():
                path.unlink()

    started = time.monotonic()

    def report_progress(stats: dict[str, int]) -> None:
        elapsed = max(time.monotonic() - started, 1e-9)
        print(
            f"completion scan files={stats['files_scanned']}/{stats['total_files']} "
            f"records={stats['records_seen']} read_gib={stats['bytes_read'] / 2**30:.1f} "
            f"rate_mib_s={stats['bytes_read'] / 2**20 / elapsed:.1f}",
            file=sys.stderr,
            flush=True,
        )

    completed_ids, completion_stats = load_completed_candidate_ids(runs_tree, progress=report_progress)
    selected, selection_stats = select_unfinished_candidates(candidates_path, completed_ids, args.per_case, args.seed)
    if not selected:
        raise SystemExit("No unfinished candidates were selected")

    shards = []
    queue_lines = []
    for index, (case_id, lines) in enumerate(sorted(selected.items())):
        shard_path = out_dir / f"case_{index:03d}_{_case_slug(case_id)}.jsonl"
        shard_path.write_text("".join(lines), encoding="utf-8")
        queue_lines.append(str(shard_path) + "\n")
        shards.append({"index": index, "case_id": case_id, "path": str(shard_path), "candidate_count": len(lines)})

    queue_path = out_dir / "benchmark_shards.txt"
    queue_path.write_text("".join(queue_lines), encoding="utf-8")
    manifest = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "candidates_jsonl": str(candidates_path),
        "partial_runs_tree": str(runs_tree),
        "per_case": args.per_case,
        "seed": args.seed,
        "completed_candidate_ids": len(completed_ids),
        "completion_scan": completion_stats,
        "selection_scan": selection_stats,
        "shards": shards,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "manifest": str(manifest_path), "cases": len(shards), "candidates": sum(len(lines) for lines in selected.values())}, indent=2))


if __name__ == "__main__":
    main()