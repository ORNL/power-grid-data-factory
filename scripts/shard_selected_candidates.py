#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


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


def _safe_float(x: Any) -> float:
    try:
        return float(x)
    except Exception:
        return 0.0


def _derive_dataset(row: dict[str, Any]) -> str:
    for key in ("dataset", "source_dataset", "grid_family", "source", "source_name"):
        value = str(row.get(key, "")).strip()
        if value:
            return value

    case_id = str(row.get("case_id", "")).strip().lower()
    if case_id.startswith("pglib"):
        return "pglib"
    if case_id.startswith("activsg") or case_id.startswith("memphis"):
        return "tamu"
    if "rts" in case_id:
        return "rts_gmlc"
    return "unknown"


def _bucket_value(row: dict[str, Any], key: str) -> str:
    key = key.strip()
    if key == "dataset":
        return _derive_dataset(row)
    if key == "topology_id":
        val = str(row.get("topology_id", "")).strip()
        if val:
            return val
        return str(row.get("contingency_class", "none")).strip() or "none"
    return str(row.get(key, "")).strip() or "unknown"


def _coverage_counts(rows: list[dict[str, Any]], keys: list[str]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {k: defaultdict(int) for k in keys}
    for row in rows:
        for key in keys:
            bucket = _bucket_value(row, key)
            counts[key][bucket] += 1
    return counts


def _coverage_universe(selected: list[dict[str, Any]], pool: list[dict[str, Any]], keys: list[str]) -> dict[str, set[str]]:
    source = pool if pool else selected
    out: dict[str, set[str]] = {k: set() for k in keys}
    for row in source:
        for key in keys:
            out[key].add(_bucket_value(row, key))
    return out


def _missing_buckets(
    selected: list[dict[str, Any]],
    universe: dict[str, set[str]],
    keys: list[str],
    min_per_bucket: int,
) -> dict[str, list[str]]:
    counts = _coverage_counts(selected, keys)
    missing: dict[str, list[str]] = {}
    for key in keys:
        need = [bucket for bucket in sorted(universe[key]) if counts[key].get(bucket, 0) < min_per_bucket]
        if need:
            missing[key] = need
    return missing


def _score_sort(rows: list[dict[str, Any]], score_key: str) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda r: (-_safe_float(r.get(score_key, 0.0)), str(r.get("candidate_id", ""))))


def _backfill_coverage(
    selected: list[dict[str, Any]],
    pool: list[dict[str, Any]],
    keys: list[str],
    min_per_bucket: int,
    max_additions: int,
    score_key: str,
) -> tuple[list[dict[str, Any]], list[str], dict[str, list[str]], dict[str, list[str]]]:
    out = list(selected)
    universe = _coverage_universe(selected, pool, keys)
    missing_before = _missing_buckets(out, universe, keys, min_per_bucket)
    if not missing_before:
        return out, [], missing_before, missing_before

    selected_ids = {str(x.get("candidate_id", "")) for x in out}
    pool_ranked = _score_sort(pool, score_key)
    added_ids: list[str] = []
    additions = 0

    while True:
        missing_now = _missing_buckets(out, universe, keys, min_per_bucket)
        if not missing_now:
            break
        if max_additions > 0 and additions >= max_additions:
            break

        best: dict[str, Any] | None = None
        best_gain = -1
        best_score = -1.0

        for cand in pool_ranked:
            cid = str(cand.get("candidate_id", ""))
            if cid in selected_ids:
                continue

            gain = 0
            for key, buckets in missing_now.items():
                b = _bucket_value(cand, key)
                if b in buckets:
                    gain += 1

            if gain <= 0:
                continue

            score = _safe_float(cand.get(score_key, 0.0))
            if gain > best_gain or (gain == best_gain and score > best_score):
                best = cand
                best_gain = gain
                best_score = score

        if best is None:
            break

        cid = str(best.get("candidate_id", ""))
        out.append(best)
        selected_ids.add(cid)
        added_ids.append(cid)
        additions += 1

    missing_after = _missing_buckets(out, universe, keys, min_per_bucket)
    return out, added_ids, missing_before, missing_after


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
            rows, added_ids, missing_before, missing_after = _backfill_coverage(
                selected=rows,
                pool=pool_rows,
                keys=coverage_keys,
                min_per_bucket=args.min_per_bucket,
                max_additions=args.max_backfill_additions,
                score_key=args.score_key,
            )
        else:
            universe = _coverage_universe(rows, pool_rows, coverage_keys)
            missing_before = _missing_buckets(rows, universe, coverage_keys, args.min_per_bucket)
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
    rows = sorted(rows, key=lambda r: str(r.get("candidate_id", "")))

    shards: list[list[dict[str, Any]]] = [[] for _ in range(args.num_shards)]
    for idx, row in enumerate(rows):
        shards[idx % args.num_shards].append(row)

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
