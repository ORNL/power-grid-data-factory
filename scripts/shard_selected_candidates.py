#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import resource
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

try:
    from grid_data_factory.campaigns.sharding import backfill_coverage, bucket_value, coverage_universe, missing_buckets, shard_rows
except ModuleNotFoundError:
    _repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(_repo_root / "src"))
    from grid_data_factory.campaigns.sharding import backfill_coverage, bucket_value, coverage_universe, missing_buckets, shard_rows


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
    p.add_argument(
        "--stream",
        action="store_true",
        help="Stream the split in O(1) memory instead of loading the whole input. "
        "Required at 10M+ candidate scale to avoid loading tens of GB into RAM.",
    )
    return p.parse_args()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _iter_jsonl_lines(path: Path):
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if stripped:
                yield stripped


def _raise_fd_limit(want: int) -> None:
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        if soft < want:
            resource.setrlimit(resource.RLIMIT_NOFILE, (min(want, hard), hard))
    except (ValueError, OSError, AttributeError):
        pass


def _missing(universe: dict[str, set[str]], counts: dict[str, dict[str, int]], keys: list[str], min_per_bucket: int) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for key in keys:
        need = [b for b in sorted(universe[key]) if counts[key].get(b, 0) < min_per_bucket]
        if need:
            out[key] = need
    return out


def run_stream(args: argparse.Namespace, coverage_keys: list[str]) -> None:
    """Shard by streaming: never holds the full candidate set in memory.

    Each input line is round-robined to shard ``idx % num_shards`` via an open
    append handle per shard, so peak memory is O(distinct coverage buckets)
    plus the shard write buffers rather than O(candidates). At 15M candidates
    the in-memory path needed ~180 GB per file; this stays near flat.
    """
    in_path = Path(args.input).resolve()
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    num_shards = args.num_shards
    _raise_fd_limit(num_shards + 128)

    pool_path = Path(args.pool_jsonl).resolve() if args.pool_jsonl else None
    need_ids = bool(args.backfill_from_pool and pool_path)

    files = [str(out_dir / f"{args.prefix}_{i:05d}.jsonl") for i in range(num_shards)]
    counts = [0] * num_shards
    sel_counts: dict[str, dict[str, int]] = {k: defaultdict(int) for k in coverage_keys}
    selected_ids: set[str] = set()
    num_input = 0
    idx = 0

    handles = [open(f, "w", encoding="utf-8") for f in files]
    try:
        for line in _iter_jsonl_lines(in_path):
            shard = idx % num_shards
            handles[shard].write(line)
            handles[shard].write("\n")
            counts[shard] += 1
            idx += 1
            num_input += 1
            if coverage_keys or need_ids:
                row = json.loads(line)
                for key in coverage_keys:
                    sel_counts[key][bucket_value(row, key)] += 1
                if need_ids:
                    selected_ids.add(str(row.get("candidate_id", "")))
    finally:
        for h in handles:
            h.close()

    if pool_path and coverage_keys:
        universe: dict[str, set[str]] = {k: set() for k in coverage_keys}
        for line in _iter_jsonl_lines(pool_path):
            row = json.loads(line)
            for key in coverage_keys:
                universe[key].add(bucket_value(row, key))
    else:
        universe = {k: set(sel_counts[k].keys()) for k in coverage_keys}

    missing_before = _missing(universe, sel_counts, coverage_keys, args.min_per_bucket)

    added_ids: list[str] = []
    if missing_before and args.backfill_from_pool and pool_path:
        needs: dict[tuple[str, str], int] = {}
        for key, buckets in missing_before.items():
            for bucket in buckets:
                needs[(key, bucket)] = args.min_per_bucket - sel_counts[key].get(bucket, 0)
        _raise_fd_limit(num_shards + 128)
        handles = [open(f, "a", encoding="utf-8") for f in files]
        try:
            for line in _iter_jsonl_lines(pool_path):
                if not needs:
                    break
                if args.max_backfill_additions > 0 and len(added_ids) >= args.max_backfill_additions:
                    break
                row = json.loads(line)
                cid = str(row.get("candidate_id", ""))
                if cid in selected_ids:
                    continue
                gain = [(key, bucket_value(row, key)) for key in coverage_keys]
                gain = [kb for kb in gain if kb in needs]
                if not gain:
                    continue
                shard = idx % num_shards
                handles[shard].write(line)
                handles[shard].write("\n")
                counts[shard] += 1
                idx += 1
                selected_ids.add(cid)
                added_ids.append(cid)
                for key in coverage_keys:
                    sel_counts[key][bucket_value(row, key)] += 1
                for kb in gain:
                    needs[kb] -= 1
                    if needs[kb] <= 0:
                        del needs[kb]
        finally:
            for h in handles:
                h.close()

    missing_after = _missing(universe, sel_counts, coverage_keys, args.min_per_bucket)

    if args.enforce_coverage and missing_after:
        report_path = out_dir / f"{args.prefix}_coverage_report.json"
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

    manifest = {
        "ok": True,
        "mode": "stream",
        "input": str(in_path),
        "num_input": num_input,
        "num_output": num_input + len(added_ids),
        "num_shards": num_shards,
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

    coverage_keys = [x.strip() for x in args.coverage_keys.split(",") if x.strip()]
    if args.stream:
        run_stream(args, coverage_keys)
        return

    in_path = Path(args.input).resolve()
    out_dir = Path(args.out_dir).resolve()
    rows = _read_jsonl(in_path)
    num_input = len(rows)

    pool_rows: list[dict[str, Any]] = []
    if args.pool_jsonl:
        pool_rows = _read_jsonl(Path(args.pool_jsonl).resolve())

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
    for i, shard in enumerate(shards):
        shard_path = out_dir / f"{args.prefix}_{i:05d}.jsonl"
        _write_jsonl(shard_path, shard)
        files.append(str(shard_path))
        counts.append(len(shard))

    manifest = {
        "ok": True,
        "input": str(in_path),
        "num_input": num_input,
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
