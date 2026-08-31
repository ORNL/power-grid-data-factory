#!/usr/bin/env python3
"""Extract candidate inputs from process_error samples so they can be re-run.

Scans every samples.jsonl under RUNS_ROOT, extracts ``inputs["candidate"]``
for records whose ``feasibility_label`` is ``"error"`` (i.e. process_error /
solver-crash), and writes them to an output JSONL file ready for consumption
by ``run_campaign_exago_ac_opf_round.py``.

Run this on a compute node, not the login node -- Lustre random-read I/O is
much faster there.

Usage
-----
    python3.11 scripts/extract_process_error_candidates.py \
        --runs-root data/outputs/runs/exago_frontier_large_grids_par \
        --groups g03,g04 \
        --out data/scratch/repair/process_error_candidates.jsonl

Then shard and submit:
    python3.11 scripts/shard_selected_candidates.py \
        --input  data/scratch/repair/process_error_candidates.jsonl \
        --output data/scratch/repair/shards \
        --shards 64
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import sys
from pathlib import Path


def _scan_file(path_str: str) -> list[dict]:
    """Return candidate dicts from process_error records in one samples.jsonl."""
    candidates: list[dict] = []
    try:
        with open(path_str, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("feasibility_label") != "error":
                    continue
                candidate = (rec.get("inputs") or {}).get("candidate")
                if candidate and candidate.get("case_id"):
                    candidates.append(candidate)
    except OSError:
        pass
    return candidates


def main() -> None:
    p = argparse.ArgumentParser(description="Extract process_error candidates for repair re-run.")
    p.add_argument("--runs-root", required=True,
                   help="Root directory containing gNN subdirectories (abs or relative to repo root).")
    p.add_argument("--groups", default="all",
                   help="Comma-separated group names to scan, e.g. 'g03,g04', or 'all'.")
    p.add_argument("--out", required=True,
                   help="Output JSONL path for extracted candidates.")
    p.add_argument("--workers", type=int, default=min(64, os.cpu_count() or 8),
                   help="Parallel worker processes for scanning.")
    args = p.parse_args()

    runs_root = Path(args.runs_root)
    if not runs_root.is_absolute():
        runs_root = (Path(__file__).resolve().parent.parent / runs_root).resolve()

    if args.groups.strip().lower() == "all":
        group_dirs = sorted(runs_root.iterdir()) if runs_root.exists() else []
    else:
        group_dirs = [runs_root / g.strip() for g in args.groups.split(",")]

    # Collect all samples.jsonl paths
    print(f"Collecting samples.jsonl paths under {runs_root} ...", flush=True)
    all_files: list[str] = []
    for gdir in group_dirs:
        if not gdir.is_dir():
            print(f"  WARNING: {gdir} not found, skipping", flush=True)
            continue
        for jl in gdir.rglob("ac_opf/samples.jsonl"):
            all_files.append(str(jl))
    print(f"Found {len(all_files)} samples.jsonl files to scan.", flush=True)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    total = 0
    by_case: dict[str, int] = {}
    seen_ids: set[str] = set()

    with out_path.open("w", encoding="utf-8") as out_fh:
        with mp.Pool(args.workers) as pool:
            for i, batch in enumerate(pool.imap_unordered(_scan_file, all_files, chunksize=16)):
                for cand in batch:
                    cid = str(cand.get("candidate_id", ""))
                    if cid in seen_ids:
                        continue  # deduplicate across rounds
                    seen_ids.add(cid)
                    out_fh.write(json.dumps(cand) + "\n")
                    total += 1
                    case_id = str(cand.get("case_id", "unknown"))
                    by_case[case_id] = by_case.get(case_id, 0) + 1
                if (i + 1) % 500 == 0:
                    print(f"  Scanned {i+1}/{len(all_files)} files, {total} unique candidates so far...", flush=True)

    print(f"\nDone. Wrote {total} unique process_error candidates to {out_path}")
    print("Breakdown by case_id:")
    for case_id, count in sorted(by_case.items(), key=lambda x: -x[1]):
        print(f"  {case_id}: {count}")


if __name__ == "__main__":
    main()
