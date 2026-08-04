#!/usr/bin/env python3
"""Print a one-glance progress dashboard for a map/reduce campaign round.

Reads only cheap signals (bootstrap intermediate files, the ≤N per-shard
execution reports, and queue/done markers) so it stays fast even when the runs
tree holds millions of attempt directories. Use ``--watch`` to refresh.
"""
from __future__ import annotations

import argparse
import glob
import json
import subprocess
import time
from pathlib import Path

try:
    from grid_data_factory.storage import paths
except ModuleNotFoundError:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from grid_data_factory.storage import paths


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--campaign-id", required=True)
    p.add_argument("--round-index", type=int, default=0)
    p.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    p.add_argument("--job-name", default="pgdf_mr_r", help="Slurm job-name prefix used to look up state.")
    p.add_argument("--watch", type=int, default=0, help="Refresh every N seconds (0 = print once).")
    return p.parse_args()


def _count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    with path.open("rb") as fh:
        while chunk := fh.read(1024 * 1024):
            total += chunk.count(b"\n")
    return total


def _squeue_state(job_name: str) -> str:
    try:
        out = subprocess.run(
            ["squeue", "-n", job_name, "-h", "-o", "%i %T %M %L"],
            capture_output=True, text=True, check=False, timeout=15,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "(squeue unavailable)"
    return out or "(not in queue)"


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def collect(repo_root: Path, campaign_id: str, round_index: int) -> dict:
    pad = f"{round_index:03d}"
    croot = paths.campaign_root(repo_root, campaign_id)
    summaries = croot / "round_summaries"
    shard_dir = summaries / f"round_{pad}_shards"
    done_dir = shard_dir / "queue" / "done"
    reduce_marker = summaries / f"round_{pad}_mapreduce_reduce_report.json"

    op = croot / "seed_operating_candidates.jsonl"
    ctg = croot / "seed_contingency_candidates.jsonl"
    screened = croot / f"round{round_index}_screened_candidates.jsonl"
    selected = summaries / f"round_{pad}_selected_candidates.jsonl"

    shard_files = glob.glob(str(shard_dir / "selected_*.jsonl"))
    done_markers = glob.glob(str(done_dir / "*"))

    solved = failed = skipped = 0
    reports = glob.glob(str(paths.campaigns_root(repo_root) / f"{campaign_id}__r{pad}__s*" / "round_summaries" / f"round_{pad}_ac_execution_report.json"))
    for r in reports:
        d = _read_json(Path(r))
        solved += int(d.get("solved_count", 0))
        failed += int(d.get("failed_count", 0))
        skipped += int(d.get("skipped_count", 0))

    # Determine the coarse pipeline stage.
    if reduce_marker.exists():
        stage = "DONE (reduce complete)" if _read_json(reduce_marker).get("ok") else "reduce FINISHED (not ok)"
    elif shard_files:
        stage = "MAP (solving shards)"
    elif selected.exists():
        stage = "sharding (selection done)"
    elif screened.exists():
        stage = "bootstrap: selection"
    elif ctg.exists():
        stage = "bootstrap: screening"
    elif op.exists():
        stage = "bootstrap: contingency enumeration"
    else:
        stage = "starting"

    return {
        "stage": stage,
        "operating_candidates": _count_lines(op),
        "contingency_candidates": _count_lines(ctg),
        "screened_candidates": _count_lines(screened),
        "selected_candidates": _count_lines(selected),
        "shards_total": len(shard_files),
        "shards_done": len(done_markers),
        "shard_reports": len(reports),
        "solved": solved,
        "failed": failed,
        "skipped": skipped,
        "reduce_ok": _read_json(reduce_marker).get("ok") if reduce_marker.exists() else None,
    }


def render(campaign_id: str, round_index: int, job_name: str, s: dict) -> str:
    processed = s["solved"] + s["failed"] + s["skipped"]
    lines = [
        f"campaign={campaign_id} round={round_index}",
        f"job: {_squeue_state(job_name)}",
        f"stage: {s['stage']}",
        "-- bootstrap --",
        f"  operating candidates : {s['operating_candidates']:,}",
        f"  contingency candidates: {s['contingency_candidates']:,}",
        f"  screened candidates  : {s['screened_candidates']:,}",
        f"  selected candidates  : {s['selected_candidates']:,}",
        "-- map/reduce --",
        f"  shards done/total    : {s['shards_done']}/{s['shards_total']}",
        f"  shard reports        : {s['shard_reports']}",
        f"  solved/failed/skipped: {s['solved']:,} / {s['failed']:,} / {s['skipped']:,}  (processed {processed:,})",
        f"  reduce ok            : {s['reduce_ok']}",
    ]
    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    job_name = f"{args.job_name}{args.round_index:03d}"
    while True:
        s = collect(repo_root, args.campaign_id, args.round_index)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
        print(f"[{stamp}]\n{render(args.campaign_id, args.round_index, job_name, s)}\n", flush=True)
        if args.watch <= 0:
            break
        time.sleep(args.watch)


if __name__ == "__main__":
    main()
