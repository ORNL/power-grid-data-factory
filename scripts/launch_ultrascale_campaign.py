#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

try:
    from grid_data_factory.campaigns.planning import plan_round_budgets
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from grid_data_factory.campaigns.planning import plan_round_budgets


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate or submit chained Slurm map/reduce campaign rounds for ultra-scale execution."
    )
    p.add_argument("--campaign-id", required=True)
    p.add_argument("--config", default="configs/campaign_ultrascale.yaml")
    p.add_argument("--slurm-script", default="configs/slurm/andes_powermodels_acopf_mapreduce_10n_36h.sbatch")

    p.add_argument("--rounds", type=int, default=10)
    p.add_argument("--start-round", type=int, default=0)
    p.add_argument("--seed-start", type=int, default=7)

    p.add_argument("--budget", type=int, default=50000)
    p.add_argument("--budget-step", type=int, default=0)
    p.add_argument(
        "--total-budget",
        type=int,
        default=0,
        help="When > 0, split this campaign-wide budget across rounds via --budget-schedule (overrides --budget/--budget-step).",
    )
    p.add_argument("--budget-schedule", choices=["constant", "linear", "geometric"], default="constant")
    p.add_argument("--budget-ratio", type=float, default=1.0)
    p.add_argument("--per-case", type=int, default=5000)
    p.add_argument("--sampler", default="sobol")

    p.add_argument("--cases", nargs="+", default=[])
    p.add_argument("--cases-file", default="")

    p.add_argument("--solver-id", default="powermodels_ac_opf_ipopt_campaign")
    p.add_argument("--timeout-s", type=int, default=1800)
    p.add_argument("--runs-root", default="data/runs")

    p.add_argument("--shard-count", type=int, default=0, help="0 means use Slurm ntasks.")
    p.add_argument("--max-candidates", type=int, default=0)
    p.add_argument("--continue-on-error", type=int, choices=[0, 1], default=1)

    p.add_argument("--coverage-keys", default="dataset,topology_id,operating_regime,contingency_class")
    p.add_argument("--min-per-bucket", type=int, default=1)
    p.add_argument("--enforce-coverage", type=int, choices=[0, 1], default=1)
    p.add_argument("--backfill-from-pool", type=int, choices=[0, 1], default=1)
    p.add_argument("--max-backfill-additions", type=int, default=0)
    p.add_argument("--score-key", default="novelty_score")
    p.add_argument("--max-failure-fraction", type=float, default=0.5)
    p.add_argument("--topologies-per-case", type=int, default=6)
    p.add_argument("--max-switched-branches", type=int, default=3)

    p.add_argument("--nodes", type=int, default=10)
    p.add_argument("--ntasks-per-node", type=int, default=16)
    p.add_argument("--cpus-per-task", type=int, default=1)
    p.add_argument("--time", default="36:00:00")

    p.add_argument("--submit", action="store_true")
    p.add_argument("--chain", action="store_true", help="When --submit, chain rounds with afterok dependency.")
    return p.parse_args()


def _read_cases_file(path: Path) -> list[str]:
    out: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.extend(line.split())
    return out


def _unique_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


def _build_export_payload(args: argparse.Namespace, round_index: int, seed: int, budget: int, cases: list[str]) -> str:
    exports = {
        "CAMPAIGN_ID": args.campaign_id,
        "ROUND_INDEX": str(round_index),
        "CONFIG": args.config,
        "CASES": " ".join(cases),
        "PER_CASE": str(args.per_case),
        "SAMPLER": args.sampler,
        "BUDGET": str(budget),
        "SEED": str(seed),
        "SOLVER_ID": args.solver_id,
        "TIMEOUT_S": str(args.timeout_s),
        "RUNS_ROOT": args.runs_root,
        "MAX_CANDIDATES": str(args.max_candidates),
        "RUN_BOOTSTRAP": "1",
        "CONTINUE_ON_ERROR": str(args.continue_on_error),
        "COVERAGE_KEYS": args.coverage_keys,
        "MIN_PER_BUCKET": str(args.min_per_bucket),
        "ENFORCE_COVERAGE": str(args.enforce_coverage),
        "BACKFILL_FROM_POOL": str(args.backfill_from_pool),
        "MAX_BACKFILL_ADDITIONS": str(args.max_backfill_additions),
        "SCORE_KEY": args.score_key,
        "MAX_FAILURE_FRACTION": str(args.max_failure_fraction),
        "TOPOLOGIES_PER_CASE": str(args.topologies_per_case),
        "MAX_SWITCHED_BRANCHES": str(args.max_switched_branches),
    }
    if args.shard_count > 0:
        exports["SHARD_COUNT"] = str(args.shard_count)

    return "ALL," + ",".join(f"{k}={v}" for k, v in exports.items())


def _build_sbatch_command(
    repo_root: Path,
    args: argparse.Namespace,
    round_index: int,
    seed: int,
    budget: int,
    cases: list[str],
    dependency_jobid: str,
) -> list[str]:
    cmd = [
        "sbatch",
        "--parsable",
        "--nodes",
        str(args.nodes),
        "--ntasks-per-node",
        str(args.ntasks_per_node),
        "--cpus-per-task",
        str(args.cpus_per_task),
        "--time",
        args.time,
        "--job-name",
        f"pgdf_mr_r{round_index:03d}",
        "--export",
        _build_export_payload(args, round_index, seed, budget, cases),
    ]
    if dependency_jobid:
        cmd.extend(["--dependency", f"afterok:{dependency_jobid}"])
    cmd.append(str((repo_root / args.slurm_script).resolve()))
    return cmd


def main() -> None:
    args = parse_args()
    if args.rounds <= 0:
        raise SystemExit("--rounds must be > 0")

    repo_root = Path(__file__).resolve().parents[1]
    cases = list(args.cases)
    if args.cases_file:
        cases.extend(_read_cases_file((repo_root / args.cases_file).resolve()))
    cases = _unique_keep_order(cases)
    if not cases:
        raise SystemExit("No cases supplied. Use --cases and/or --cases-file.")

    plan: list[dict[str, object]] = []
    dependency_jobid = ""

    if args.total_budget > 0:
        round_budgets = plan_round_budgets(
            total_budget=args.total_budget,
            rounds=args.rounds,
            schedule=args.budget_schedule,
            ratio=args.budget_ratio,
        )
    else:
        round_budgets = [args.budget + i * args.budget_step for i in range(args.rounds)]

    for i in range(args.rounds):
        round_index = args.start_round + i
        seed = args.seed_start + i
        budget = round_budgets[i]
        if budget <= 0:
            raise SystemExit(f"Round {round_index}: computed budget must be > 0")

        cmd = _build_sbatch_command(
            repo_root=repo_root,
            args=args,
            round_index=round_index,
            seed=seed,
            budget=budget,
            cases=cases,
            dependency_jobid=(dependency_jobid if args.chain else ""),
        )

        record: dict[str, object] = {
            "round_index": round_index,
            "seed": seed,
            "budget": budget,
            "command": cmd,
        }

        if args.submit:
            proc = subprocess.run(cmd, capture_output=True, text=True, cwd=repo_root)
            if proc.returncode != 0:
                raise RuntimeError(
                    json.dumps(
                        {
                            "ok": False,
                            "round_index": round_index,
                            "returncode": proc.returncode,
                            "stdout": proc.stdout,
                            "stderr": proc.stderr,
                            "command": cmd,
                        },
                        indent=2,
                    )
                )
            jobid = proc.stdout.strip().split(";")[0].strip()
            record["jobid"] = jobid
            if args.chain:
                dependency_jobid = jobid
        else:
            record["jobid"] = ""

        plan.append(record)

    out = {
        "ok": True,
        "submit": args.submit,
        "chain": args.chain,
        "campaign_id": args.campaign_id,
        "config": args.config,
        "rounds": args.rounds,
        "start_round": args.start_round,
        "total_budget": args.total_budget,
        "budget_schedule": args.budget_schedule if args.total_budget > 0 else "",
        "round_budgets": round_budgets,
        "cases_count": len(cases),
        "cases": cases,
        "plan": plan,
    }
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
