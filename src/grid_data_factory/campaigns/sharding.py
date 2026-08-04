from __future__ import annotations

from collections import defaultdict
from typing import Any


def safe_float(x: Any) -> float:
    try:
        return float(x)
    except Exception:
        return 0.0


def derive_dataset(row: dict[str, Any]) -> str:
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


def bucket_value(row: dict[str, Any], key: str) -> str:
    key = key.strip()
    if key == "dataset":
        return derive_dataset(row)
    if key == "topology_id":
        val = str(row.get("topology_id", "")).strip()
        if val:
            return val
        return str(row.get("contingency_class", "none")).strip() or "none"
    return str(row.get(key, "")).strip() or "unknown"


def coverage_counts(rows: list[dict[str, Any]], keys: list[str]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {k: defaultdict(int) for k in keys}
    for row in rows:
        for key in keys:
            bucket = bucket_value(row, key)
            counts[key][bucket] += 1
    return counts


def coverage_universe(selected: list[dict[str, Any]], pool: list[dict[str, Any]], keys: list[str]) -> dict[str, set[str]]:
    source = pool if pool else selected
    out: dict[str, set[str]] = {k: set() for k in keys}
    for row in source:
        for key in keys:
            out[key].add(bucket_value(row, key))
    return out


def missing_buckets(
    selected: list[dict[str, Any]],
    universe: dict[str, set[str]],
    keys: list[str],
    min_per_bucket: int,
) -> dict[str, list[str]]:
    counts = coverage_counts(selected, keys)
    missing: dict[str, list[str]] = {}
    for key in keys:
        need = [bucket for bucket in sorted(universe[key]) if counts[key].get(bucket, 0) < min_per_bucket]
        if need:
            missing[key] = need
    return missing


def score_sort(rows: list[dict[str, Any]], score_key: str) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda r: (-safe_float(r.get(score_key, 0.0)), str(r.get("candidate_id", ""))))


def backfill_coverage(
    selected: list[dict[str, Any]],
    pool: list[dict[str, Any]],
    keys: list[str],
    min_per_bucket: int,
    max_additions: int,
    score_key: str,
) -> tuple[list[dict[str, Any]], list[str], dict[str, list[str]], dict[str, list[str]]]:
    out = list(selected)
    universe = coverage_universe(selected, pool, keys)
    missing_before = missing_buckets(out, universe, keys, min_per_bucket)
    if not missing_before:
        return out, [], missing_before, missing_before

    selected_ids = {str(x.get("candidate_id", "")) for x in out}
    pool_ranked = score_sort(pool, score_key)
    added_ids: list[str] = []
    additions = 0

    while True:
        missing_now = missing_buckets(out, universe, keys, min_per_bucket)
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
                b = bucket_value(cand, key)
                if b in buckets:
                    gain += 1

            if gain <= 0:
                continue

            score = safe_float(cand.get(score_key, 0.0))
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

    missing_after = missing_buckets(out, universe, keys, min_per_bucket)
    return out, added_ids, missing_before, missing_after


def shard_rows(rows: list[dict[str, Any]], num_shards: int) -> list[list[dict[str, Any]]]:
    # Deterministic ordering by candidate_id ensures stable shard assignment.
    ordered = sorted(rows, key=lambda r: str(r.get("candidate_id", "")))
    shards: list[list[dict[str, Any]]] = [[] for _ in range(num_shards)]
    for idx, row in enumerate(ordered):
        shards[idx % num_shards].append(row)
    return shards
