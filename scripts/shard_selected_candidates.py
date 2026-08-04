#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    from grid_data_factory.campaigns.sharding import backfill_coverage, coverage_universe, missing_buckets, shard_rows
except ModuleNotFoundError:
    _repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(_repo_root / "src"))
    from grid_data_factory.campaigns.sharding import backfill_coverage, coverage_universe, missing_buckets, shard_rows


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Deterministically shard selected-candidate JSONL for parallel map execution.")
    p.add_argument("--input", required=True, help="Selected candidates JSONL path.")
    p.add_argument("--num-shards", type=int, required=True, help="Number of output shards.")
    p.add_argument("--out-dir", required=True, help="Directory where shard JSONL files are written.")
    p.add_argument("--prefix", default="shard", help="Shard filename prefix.")
    p.add_argument("--pool-jsonl", default="", help="Optional candidate pool JSONL used for coverage backfill.")
    p.add_argument(
        "--coverage-keys",
        default="dataset,topology_id,operating_regime,contingency_class",
        help="Comma-separated bucket keys to enforce coverage for (e.g. dataset,topology_id,operating_regime,contingency_class).",
    )
    p.add_argument("--min-per-bucket", type=int, default=1, help="Minimum selected count required per bucket.")
    p.add_argument("--enforce-coverage", action="store_true", help="Fail if coverage requirements are not met.")
    p.add_argument("--backfill-from-pool", action="store_true", help="Auto-backfill missing coverage buckets from pool JSONL.")
    p.add_argument(
        "--max-backfill-additions",
        type=int,
        default=0,
        help="Optional cap on number of candidates added during backfill (0 means unlimited).",
    )
    p.add_argument(
        "--score-key",
        default="novelty_score",
        help="Score key used to prioritize backfill candidates (descending).",
    )
    return p.parse_args()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=True) + "\n")


def main() -> None:
    args = parse_args()
    if args.num_shards <= 0:
        raise SystemExit("--num-shards must be > 0")
    if args.min_per_bucket <= 0:
        raise SystemExit("--min-per-bucket must be > 0")

    in_path = Path(args.input).resolve()
    out_dir = Path(args.out_dir).resolve()
    rows = _read_jsonl(in_path)

    pool_rows: list[dict[str, Any]] = []
    if args.pool_jsonl:
        pool_rows = _read_jsonl(Path(args.pool_jsonl).resolve())

    coverage_keys = [x.strip() for x in args.coverage_keys.split(",") if x.strip()]
    if coverage_keys:
        if args.backfill_from_pool and not pool_rows:
            raise SystemExit("--backfill-from-pool requires --pool-jsonl")

        if args.backfill_from_pool:
            rows, added_ids, missing_before, missing_after = backfill_coverage(
                selected=rows,
                pool=pool_rows,
                keys=coverage_keys,
                min_per_bucket=args.min_per_bucket,
                max_additions=args.max_backfill_additions,
                score_key=args.score_key,
            )
        else:
            universe = coverage_universe(rows, pool_rows, coverage_keys)
            missing_before = missing_buckets(rows, universe, coverage_keys, args.min_per_bucket)
            missing_after = dict(missing_before)
            added_ids = []

        if args.enforce_coverage and missing_after:
            report_path = out_dir / f"{args.prefix}_coverage_report.json"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(
                    {
                        "ok": False,
                        "input": str(in_path),
                        "coverage_keys": coverage_keys,
                        "min_per_bucket": args.min_per_bucket,
                        "missing_before": missing_before,
                        "missing_after": missing_after,
                        "added_candidate_ids": added_ids,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            raise SystemExit(f"Coverage requirements not met. See {report_path}")
    else:
        missing_before = {}
        missing_after = {}
        added_ids = []

    # Deterministic ordering by candidate_id ensures stable shard assignment.
    shards = shard_rows(rows, args.num_shards)

    counts: list[int] = []
    files: list[str] = []
    for i, shard_rows in enumerate(shards):
        shard_path = out_dir / f"{args.prefix}_{i:05d}.jsonl"
        _write_jsonl(shard_path, shard_rows)
        files.append(str(shard_path))
        counts.append(len(shard_rows))

    manifest = {
        "ok": True,
        "input": str(in_path),
        "num_input": len(_read_jsonl(in_path)),
        "num_output": len(rows),
        "num_shards": args.num_shards,
        "prefix": args.prefix,
        "shard_counts": counts,
        "shard_files": files,
        "coverage": {
            "keys": coverage_keys,
            "min_per_bucket": args.min_per_bucket,
            "missing_before": missing_before,
            "missing_after": missing_after,
            "backfill_added_count": len(added_ids),
            "backfill_added_candidate_ids": added_ids,
        },
    }
    manifest_path = out_dir / f"{args.prefix}_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "manifest": str(manifest_path)}, indent=2))


if __name__ == "__main__":
    main()
