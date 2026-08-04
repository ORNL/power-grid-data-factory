#!/usr/bin/env python3
"""Auto-detect the furthest incomplete campaign round and (re)submit it.

A round is "complete" when its deterministic reduce marker
``data/campaigns/<campaign_id>/round_summaries/round_<NNN>_mapreduce_reduce_report.json``
exists with ``"ok": true``. The furthest incomplete round is the lowest round
index that is not complete; because rounds run in sequence, that is the round
currently in progress. It is resubmitted with ``RESUME=1`` so the SLURM job
skips finished shards and finalized runs. With ``--chain`` the remaining rounds
are also queued, each depending on the previous via ``afterok``.

Resource flags (``--nodes``, ``--ntasks-per-node``, ``--cpus-per-task``,
``--time``) override the sbatch header when set, so continuation matches the
original batch (e.g. Andes allows 36h only at <=64 nodes).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

try:
    from grid_data_factory.campaigns.planning import plan_round_budgets
    from grid_data_factory.storage import paths
except ModuleNotFoundError:
    _repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(_repo_root / "src"))
    from grid_data_factory.campaigns.planning import plan_round_budgets
    from grid_data_factory.storage import paths


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--campaign-id", required=True)
    p.add_argument("--rounds", type=int, required=True, help="Total number of rounds in the campaign.")
    p.add_argument(
        "--sbatch",
        default="configs/slurm/andes_powermodels_acopf_mapreduce_10n_36h.sbatch",
        help="Path to the per-round map/reduce sbatch script.",
    )
    p.add_argument("--config", default="configs/campaign_default.yaml")
    p.add_argument("--total-budget", type=int, default=0, help="If >0, split across rounds via the budget schedule.")
    p.add_argument("--budget", type=int, default=0, help="Per-round budget when --total-budget is not used.")
    p.add_argument("--budget-schedule", default="constant", choices=("constant", "linear", "geometric"))
    p.add_argument("--budget-ratio", type=float, default=1.0)
    p.add_argument("--chain", action="store_true", help="Also queue all subsequent rounds, chained via afterok.")
    p.add_argument("--dry-run", action="store_true", help="Print the submission plan without calling sbatch.")
    p.add_argument("--nodes", type=int, default=0, help="Override sbatch node count (0 keeps the template header value).")
    p.add_argument("--ntasks-per-node", type=int, default=0, help="Override sbatch tasks per node (0 keeps the header value).")
    p.add_argument("--cpus-per-task", type=int, default=0, help="Override sbatch cpus per task (0 keeps the header value).")
    p.add_argument("--time", default="", help="Override sbatch walltime, e.g. 36:00:00 (empty keeps the header value).")
    p.add_argument(
        "--set",
        dest="extra_env",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Extra environment override passed through to the sbatch job (repeatable).",
    )
    return p.parse_args()


def reduce_marker_path(repo_root: Path, campaign_id: str, round_index: int) -> Path:
    return paths.campaign_root(repo_root, campaign_id) / "round_summaries" / f"round_{round_index:03d}_mapreduce_reduce_report.json"


def round_complete(repo_root: Path, campaign_id: str, round_index: int) -> bool:
    marker = reduce_marker_path(repo_root, campaign_id, round_index)
    if not marker.exists():
        return False
    try:
        return bool(json.loads(marker.read_text(encoding="utf-8")).get("ok"))
    except (json.JSONDecodeError, OSError):
        return False


def first_incomplete_round(repo_root: Path, campaign_id: str, rounds: int) -> int | None:
    for r in range(rounds):
        if not round_complete(repo_root, campaign_id, r):
            return r
    return None


def compute_round_budgets(total_budget: int, budget: int, rounds: int, schedule: str, ratio: float) -> list[int]:
    if total_budget > 0:
        return plan_round_budgets(total_budget, rounds, schedule=schedule, ratio=ratio)
    return [budget] * rounds


def parse_extra_env(pairs: list[str]) -> dict[str, str]:
    env: dict[str, str] = {}
    for item in pairs:
        if "=" not in item:
            raise ValueError(f"--set expects KEY=VALUE, got: {item}")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"--set expects a non-empty key, got: {item}")
        env[key] = value
    return env


def build_submit_env(base_env: dict[str, str], campaign_id: str, round_index: int, budget: int, resume: bool, extra_env: dict[str, str]) -> dict[str, str]:
    env = dict(base_env)
    env.update(extra_env)  # explicit overrides first
    env["CAMPAIGN_ID"] = campaign_id
    env["ROUND_INDEX"] = str(round_index)
    env["RESUME"] = "1" if resume else "0"
    if budget > 0:
        env["BUDGET"] = str(budget)
    return env


def build_resource_flags(nodes: int, ntasks_per_node: int, cpus_per_task: int, time: str) -> list[str]:
    flags: list[str] = []
    if nodes > 0:
        flags += ["--nodes", str(nodes)]
    if ntasks_per_node > 0:
        flags += ["--ntasks-per-node", str(ntasks_per_node)]
    if cpus_per_task > 0:
        flags += ["--cpus-per-task", str(cpus_per_task)]
    if time:
        flags += ["--time", time]
    return flags


def submit_round(repo_root: Path, sbatch_path: Path, env: dict[str, str], dependency: str | None, dry_run: bool, resource_flags: list[str] | None = None) -> str:
    resource_flags = resource_flags or []
    cmd = ["sbatch", "--parsable", *resource_flags]
    if dependency:
        cmd.append(f"--dependency=afterok:{dependency}")
    cmd.append(str(sbatch_path))
    overrides = {k: env[k] for k in ("CAMPAIGN_ID", "ROUND_INDEX", "RESUME", "BUDGET") if k in env}
    if dry_run:
        dep = f" (afterok:{dependency})" if dependency else ""
        res = f" {resource_flags}" if resource_flags else ""
        print(f"[dry-run] sbatch{dep}{res} {sbatch_path}  overrides={overrides}")
        return f"DRYRUN_R{env['ROUND_INDEX']}"
    proc = subprocess.run(cmd, env=env, cwd=str(repo_root), capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"sbatch failed (rc={proc.returncode}): {proc.stderr.strip()}")
    job_id = proc.stdout.strip().split(";")[0]
    dep = f" (afterok:{dependency})" if dependency else ""
    print(f"submitted job {job_id}{dep}  overrides={overrides}")
    return job_id


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]

    if args.rounds <= 0:
        raise SystemExit("--rounds must be a positive integer")

    sbatch_path = Path(args.sbatch)
    sbatch_path = sbatch_path if sbatch_path.is_absolute() else (repo_root / sbatch_path)
    if not sbatch_path.exists():
        raise SystemExit(f"sbatch script not found: {sbatch_path}")

    if not args.dry_run and shutil.which("sbatch") is None:
        raise SystemExit("sbatch not found on PATH; run on a Slurm submit host or use --dry-run")

    extra_env = parse_extra_env(args.extra_env)
    budgets = compute_round_budgets(args.total_budget, args.budget, args.rounds, args.budget_schedule, args.budget_ratio)
    resource_flags = build_resource_flags(args.nodes, args.ntasks_per_node, args.cpus_per_task, args.time)

    start = first_incomplete_round(repo_root, args.campaign_id, args.rounds)
    if start is None:
        print(json.dumps({"campaign_id": args.campaign_id, "rounds": args.rounds, "status": "all_rounds_complete"}, indent=2))
        return

    completed = [r for r in range(args.rounds) if round_complete(repo_root, args.campaign_id, r)]
    print(f"campaign={args.campaign_id} rounds={args.rounds} completed={completed} furthest_incomplete={start}")

    targets = range(start, args.rounds) if args.chain else [start]
    prev_job: str | None = None
    submitted: list[dict[str, object]] = []
    for r in targets:
        env = build_submit_env(dict(os.environ), args.campaign_id, r, budgets[r], resume=True, extra_env=extra_env)
        job_id = submit_round(repo_root, sbatch_path, env, dependency=prev_job, dry_run=args.dry_run, resource_flags=resource_flags)
        submitted.append({"round_index": r, "budget": budgets[r], "job_id": job_id, "dependency": prev_job})
        prev_job = job_id

    print(json.dumps({"campaign_id": args.campaign_id, "furthest_incomplete_round": start, "submitted": submitted}, indent=2))


if __name__ == "__main__":
    main()
