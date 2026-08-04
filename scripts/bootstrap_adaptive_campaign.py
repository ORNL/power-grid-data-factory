#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


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
    return p.parse_args()


def _run(cmd: list[str], cwd: Path, env: dict[str, str]) -> None:
    proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            json.dumps(
                {
                    "ok": False,
                    "cmd": cmd,
                    "returncode": proc.returncode,
                    "stdout": proc.stdout,
                    "stderr": proc.stderr,
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

    campaign_root = repo_root / "data" / "campaigns" / args.campaign_id
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
        "--seed",
        str(args.seed),
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
