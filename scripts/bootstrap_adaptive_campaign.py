#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

try:
    from grid_data_factory.storage import paths
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from grid_data_factory.storage import paths


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Bootstrap one adaptive campaign round from generated operating and contingency candidates.")
    p.add_argument("--campaign-id", required=True)
    p.add_argument("--config", default="configs/campaign_default.yaml")
    p.add_argument("--cases", nargs="+", default=["pglib_opf_case14_ieee", "pglib_opf_case57_ieee", "pglib_opf_case118_ieee"])
    p.add_argument("--per-case", type=int, default=500)
    p.add_argument("--sampler", default="latin_hypercube")
    p.add_argument("--budget", type=int, default=600)
    p.add_argument("--round-index", type=int, default=0)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--topologies-per-case", type=int, default=6)
    p.add_argument("--max-switched-branches", type=int, default=3)
    p.add_argument("--max-k", type=int, default=10)
    p.add_argument("--sequential-cascade-per-operating-point", type=int, default=0, help="Ordered sequential cascades per depth (0 disables cascades).")
    p.add_argument("--sequential-max-len", type=int, default=10, help="Maximum sequential cascade depth; capped at --max-k.")
    p.add_argument("--workers", type=int, default=1, help="Parallel workers for the enumeration stage (0=all cores).")
    return p.parse_args()


def _run(cmd: list[str], cwd: Path, env: dict[str, str]) -> None:
    # Stream stdout/stderr live so stage progress is visible in the job log.
    proc = subprocess.run(cmd, cwd=cwd, env=env)
    if proc.returncode != 0:
        raise RuntimeError(
            json.dumps(
                {
                    "ok": False,
                    "cmd": cmd,
                    "returncode": proc.returncode,
                },
                indent=2,
            )
        )


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    src_path = str(repo_root / "src")
    env["PYTHONPATH"] = src_path + (":" + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")

    campaign_root = paths.campaign_root(repo_root, args.campaign_id)
    op_jsonl = campaign_root / "seed_operating_candidates.jsonl"
    ctg_jsonl = campaign_root / "seed_contingency_candidates.jsonl"
    screened_jsonl = campaign_root / "round0_screened_candidates.jsonl"
    audit_jsonl = campaign_root / "round0_screening_audit.jsonl"

    create_cmd = [
        sys.executable,
        str(repo_root / "scripts" / "create_operating_point.py"),
        "--config",
        args.config,
        "--cases",
        *args.cases,
        "--per-case",
        str(args.per_case),
        "--sampler",
        args.sampler,
        "--topologies-per-case",
        str(args.topologies_per_case),
        "--max-switched-branches",
        str(args.max_switched_branches),
        "--seed",
        str(args.seed),
        "--out",
        str(op_jsonl),
    ]

    enum_cmd = [
        sys.executable,
        str(repo_root / "scripts" / "enumerate_contingencies.py"),
        "--input",
        str(op_jsonl),
        "--out",
        str(ctg_jsonl),
        "--max-k",
        str(args.max_k),
        "--sequential-cascade-per-operating-point",
        str(args.sequential_cascade_per_operating_point),
        "--sequential-max-len",
        str(args.sequential_max_len),
        "--seed",
        str(args.seed),
        "--workers",
        str(args.workers),
    ]

    screen_cmd = [
        sys.executable,
        str(repo_root / "scripts" / "screen_contingencies.py"),
        "--input",
        str(ctg_jsonl),
        "--out",
        str(screened_jsonl),
        "--audit-out",
        str(audit_jsonl),
        "--config",
        args.config,
        "--seed",
        str(args.seed),
    ]

    round_cmd = [
        sys.executable,
        str(repo_root / "scripts" / "run_adaptive_campaign_round.py"),
        "--campaign-id",
        args.campaign_id,
        "--config",
        args.config,
        "--candidates-jsonl",
        str(screened_jsonl),
        "--round-index",
        str(args.round_index),
        "--budget",
        str(args.budget),
        "--seed",
        str(args.seed),
    ]

    _run(create_cmd, repo_root, env)
    _run(enum_cmd, repo_root, env)
    _run(screen_cmd, repo_root, env)
    _run(round_cmd, repo_root, env)

    print(
        json.dumps(
            {
                "ok": True,
                "campaign_id": args.campaign_id,
                "campaign_root": str(campaign_root),
                "artifacts": {
                    "operating_candidates": str(op_jsonl),
                    "contingency_candidates": str(ctg_jsonl),
                    "screened_candidates": str(screened_jsonl),
                    "screening_audit": str(audit_jsonl),
                },
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
