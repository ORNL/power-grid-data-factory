#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def aggregate(progress_dir: Path, shard_count: int) -> dict[str, Any]:
    totals: Counter[str] = Counter()
    counts_by_case: Counter[str] = Counter()
    converged_by_case: Counter[str] = Counter()
    termination_statuses: Counter[str] = Counter()
    session_converged_by_case: Counter[str] = Counter()
    session_termination_statuses: Counter[str] = Counter()
    latest_update: str | None = None
    for shard_index in range(shard_count):
        path = progress_dir / f"shard_{shard_index:05d}.json"
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        totals["started_shards"] += 1
        totals["complete_shards"] += item.get("phase") == "complete"
        totals["active_calculations"] += item.get("phase") == "running" and bool(item.get("current_candidate_id"))
        for key in (
            "total_candidates",
            "attempted",
            "converged",
            "unsuccessful",
            "resumed_attempts",
            "session_attempted",
            "session_converged",
            "session_unsuccessful",
            "errors",
            "skipped",
        ):
            totals[key] += int(item.get(key, 0))
        counts_by_case.update(item.get("counts_by_case", {}))
        converged_by_case.update(item.get("converged_by_case", {}))
        termination_statuses.update(item.get("termination_statuses", {}))
        session_converged_by_case.update(item.get("session_converged_by_case", {}))
        session_termination_statuses.update(item.get("session_termination_statuses", {}))
        updated_at = item.get("updated_at")
        if updated_at and (latest_update is None or updated_at > latest_update):
            latest_update = updated_at
    return {
        "configured_shards": shard_count,
        **totals,
        "counts_by_case": dict(sorted(counts_by_case.items())),
        "converged_by_case": dict(sorted(converged_by_case.items())),
        "termination_statuses": dict(sorted(termination_statuses.items())),
        "session_converged_by_case": dict(sorted(session_converged_by_case.items())),
        "session_termination_statuses": dict(sorted(session_termination_statuses.items())),
        "latest_update": latest_update,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Aggregate small per-shard solver progress snapshots.")
    parser.add_argument("--progress-dir", required=True)
    parser.add_argument("--shard-count", type=int, required=True)
    args = parser.parse_args()
    print(json.dumps(aggregate(Path(args.progress_dir), args.shard_count), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()