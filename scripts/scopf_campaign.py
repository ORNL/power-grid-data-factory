#!/usr/bin/env python3
"""Prepare, run and summarize a resumable SCOPF campaign in an isolated output tree.

prepare: build the N-1 contingency sets and the task manifest.
run:     claim pending tasks atomically and solve them with run_scopf.py.
status:  count done / failed / pending tasks.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from make_n1_contingency_set import build_n1_contingency_set  # noqa: E402

SCOPF_OUTPUT_BASES = ("data/outputs/runs_scopf", "data/outputs/campaigns_scopf")


def _resolve(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (REPO_ROOT / path).resolve()


def check_isolated(path: Path) -> None:
    """Refuse any output path outside the dedicated SCOPF trees."""
    resolved = path.resolve()
    for base in SCOPF_OUTPUT_BASES:
        root = (REPO_ROOT / base).resolve()
        if resolved != root and root in resolved.parents:
            return
    raise SystemExit(f"Refusing SCOPF output path outside {SCOPF_OUTPUT_BASES}: {resolved}")


def load_config(path: Path) -> dict[str, Any]:
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    runs_root = _resolve(config["storage"]["runs_root"])
    campaign_root = _resolve(config["storage"]["campaign_root"])
    check_isolated(runs_root)
    check_isolated(campaign_root)
    config["_runs_root"] = runs_root
    config["_campaign_root"] = campaign_root
    return config


def _iter_cases(config: dict[str, Any]):
    """Yield (tier_name, settings, case) with case keys > tier keys > defaults."""
    defaults = config.get("defaults", {})
    for case in config.get("cases", []):
        yield "default", {**defaults, **case}, case
    for name, tier in (config.get("tiers") or {}).items():
        base = {**defaults, **{k: v for k, v in tier.items() if k != "cases"}}
        for case in tier.get("cases", []):
            case = {"case_file": case} if isinstance(case, str) else case
            yield name, {**base, **case}, case


def plan_tasks(config: dict[str, Any]) -> list[dict[str, Any]]:
    tasks = []
    for tier, settings, case in _iter_cases(config):
        case_file = case["case_file"]
        # run_scopf.py derives the case id from the file stem; contingency sets must match it.
        case_id = Path(case_file).stem
        for spec in settings.get("sets", []):
            for seed in settings.get("seeds", [0]):
                b, g = int(spec["branches"]), int(spec["generators"])
                b_tag = "all" if b < 0 else str(b)
                g_tag = "all" if g < 0 else str(g)
                set_id = f"n1_b{b_tag}_g{g_tag}_s{seed}"
                tasks.append(
                    {
                        "task_id": f"{case_id}__{set_id}",
                        "tier": tier,
                        "timeout_s": int(settings.get("timeout_s", config.get("solver", {}).get("timeout_s", 7200))),
                        "case_id": case_id,
                        "case_file": case_file,
                        "contingency_set_id": set_id,
                        "branches": b,
                        "generators": g,
                        "seed": int(seed),
                    }
                )
    ids = [t["task_id"] for t in tasks]
    if len(ids) != len(set(ids)):
        raise SystemExit("Duplicate task ids in campaign config")
    return tasks


def prepare(config: dict[str, Any]) -> dict[str, Any]:
    campaign_root = config["_campaign_root"]
    sets_dir = campaign_root / "contingency_sets"
    for sub in ("contingency_sets", "tasks/claims", "tasks/done", "tasks/failed", "reports", "logs"):
        (campaign_root / sub).mkdir(parents=True, exist_ok=True)
    config["_runs_root"].mkdir(parents=True, exist_ok=True)
    tasks = plan_tasks(config)
    for task in tasks:
        out = sets_dir / task["case_id"] / f"{task['contingency_set_id']}.json"
        task["contingency_set_file"] = str(out.relative_to(REPO_ROOT))
        if out.is_file():
            # Sets are immutable once written so reruns stay comparable.
            task["contingency_count"] = len(json.loads(out.read_text(encoding="utf-8"))["contingencies"])
            continue
        doc = build_n1_contingency_set(
            _resolve(task["case_file"]), task["case_id"], task["branches"], task["generators"], task["seed"]
        )
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        tmp.replace(out)
        task["contingency_count"] = len(doc["contingencies"])
    manifest = campaign_root / "tasks" / "manifest.jsonl"
    tmp = manifest.with_suffix(".jsonl.tmp")
    tmp.write_text("".join(json.dumps(t) + "\n" for t in tasks), encoding="utf-8")
    tmp.replace(manifest)
    return {"campaign_id": config["campaign_id"], "tasks": len(tasks), "manifest": str(manifest)}


def _read_manifest(campaign_root: Path) -> list[dict[str, Any]]:
    manifest = campaign_root / "tasks" / "manifest.jsonl"
    if not manifest.is_file():
        raise SystemExit(f"Missing manifest {manifest}; run 'prepare' first")
    return [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines() if line.strip()]


def _failure_count(campaign_root: Path, task_id: str) -> int:
    return len(list((campaign_root / "tasks" / "failed").glob(f"{task_id}.attempt*.json")))


def _claim(campaign_root: Path, task: dict[str, Any], max_attempts: int) -> Path | None:
    task_id = task["task_id"]
    if (campaign_root / "tasks" / "done" / f"{task_id}.json").exists():
        return None
    if _failure_count(campaign_root, task_id) >= max_attempts:
        return None
    claim = campaign_root / "tasks" / "claims" / task_id
    try:
        claim.mkdir()
    except FileExistsError:
        return None
    (claim / "owner.json").write_text(
        json.dumps({"host": socket.gethostname(), "pid": os.getpid(), "job": os.environ.get("SLURM_JOB_ID"), "t": time.time()}),
        encoding="utf-8",
    )
    return claim


def _release(claim: Path) -> None:
    for child in claim.iterdir():
        child.unlink()
    claim.rmdir()


def clear_stale_claims(config: dict[str, Any]) -> dict[str, Any]:
    """Release claims left by a killed job, counting each unfinished one as a failed attempt."""
    campaign_root = config["_campaign_root"]
    claims = list((campaign_root / "tasks" / "claims").iterdir())
    interrupted = 0
    for claim in claims:
        task_id = claim.name
        if not (campaign_root / "tasks" / "done" / f"{task_id}.json").exists():
            owner = claim / "owner.json"
            n = _failure_count(campaign_root, task_id) + 1
            summary = {
                "task_id": task_id,
                "rc": None,
                "termination_status": None,
                "error": "interrupted: claim left by a terminated job",
                "owner": json.loads(owner.read_text(encoding="utf-8")) if owner.is_file() else None,
            }
            dest = campaign_root / "tasks" / "failed" / f"{task_id}.attempt{n}.json"
            dest.write_text(json.dumps(summary, indent=2), encoding="utf-8")
            interrupted += 1
        _release(claim)
    return {"cleared": len(claims), "interrupted_attempts_recorded": interrupted}


def run_task(config: dict[str, Any], task: dict[str, Any], python: str) -> tuple[int, dict[str, Any]]:
    campaign_root = config["_campaign_root"]
    solver = config.get("solver", {})
    report = campaign_root / "reports" / f"{task['task_id']}.attempt{_failure_count(campaign_root, task['task_id']) + 1}.json"
    cmd = [
        python, str(REPO_ROOT / "scripts" / "run_scopf.py"),
        "--source", "pglib",
        "--case-file", task["case_file"],
        "--contingency-set", task["contingency_set_file"],
        "--runs-root", str(config["_runs_root"]),
        "--solver-id", solver.get("solver_id", "powermodels_scopf_pmsc_ipopt"),
        "--timeout-s", str(task.get("timeout_s", solver.get("timeout_s", 7200))),
        "--out", str(report),
    ]
    if solver.get("screen_contingencies", True):
        cmd.append("--screen-contingencies")
    env = dict(os.environ)
    env.setdefault("IPOPT_LINEAR_SOLVER", solver.get("linear_solver", "ma57"))
    log = campaign_root / "logs" / f"{task['task_id']}.log"
    t0 = time.time()
    with log.open("a", encoding="utf-8") as fh:
        fh.write(f"=== start {time.strftime('%FT%T')} job={os.environ.get('SLURM_JOB_ID')}\n")
        fh.flush()
        rc = subprocess.call(cmd, cwd=REPO_ROOT, env=env, stdout=fh, stderr=subprocess.STDOUT)
    summary: dict[str, Any] = {"task_id": task["task_id"], "rc": rc, "wall_s": round(time.time() - t0, 1), "report": str(report)}
    if report.is_file():
        result = (json.loads(report.read_text(encoding="utf-8")).get("results") or [{}])[0]
        for key in ("ok", "termination_status", "objective", "contingency_count", "admitted_contingency_count",
                    "excluded_contingencies", "attempt_dir", "run_id", "error"):
            summary[key] = result.get(key)
    return rc, summary


def run(config: dict[str, Any], python: str, max_tasks: int, tiers: list[str] | None = None) -> dict[str, Any]:
    campaign_root = config["_campaign_root"]
    max_attempts = int(config.get("solver", {}).get("max_attempts_per_task", 2))
    tasks = [t for t in _read_manifest(campaign_root) if not tiers or t.get("tier") in tiers]
    tasks.sort(key=lambda t: t["contingency_count"], reverse=True)
    processed = 0
    for task in tasks:
        if max_tasks and processed >= max_tasks:
            break
        claim = _claim(campaign_root, task, max_attempts)
        if claim is None:
            continue
        try:
            rc, summary = run_task(config, task, python)
            # A solved or classified run (incl. no admissible contingencies) is final; crashes are retried.
            final = rc == 0 or summary.get("termination_status") not in (None, "", "ERROR")
            if final:
                dest = campaign_root / "tasks" / "done" / f"{task['task_id']}.json"
            else:
                n = _failure_count(campaign_root, task["task_id"]) + 1
                dest = campaign_root / "tasks" / "failed" / f"{task['task_id']}.attempt{n}.json"
            dest.write_text(json.dumps(summary, indent=2), encoding="utf-8")
            processed += 1
        finally:
            _release(claim)
    return {"processed": processed, **status(config)}


def status(config: dict[str, Any]) -> dict[str, Any]:
    campaign_root = config["_campaign_root"]
    max_attempts = int(config.get("solver", {}).get("max_attempts_per_task", 2))
    tasks = _read_manifest(campaign_root)
    done = exhausted = running = 0
    outcomes: dict[str, int] = {}
    for task in tasks:
        done_file = campaign_root / "tasks" / "done" / f"{task['task_id']}.json"
        if done_file.exists():
            done += 1
            key = str(json.loads(done_file.read_text(encoding="utf-8")).get("termination_status"))
            outcomes[key] = outcomes.get(key, 0) + 1
        elif _failure_count(campaign_root, task["task_id"]) >= max_attempts:
            exhausted += 1
        elif (campaign_root / "tasks" / "claims" / task["task_id"]).exists():
            running += 1
    return {
        "campaign_id": config["campaign_id"],
        "tasks": len(tasks),
        "done": done,
        "failed_exhausted": exhausted,
        "running": running,
        "pending": len(tasks) - done - exhausted - running,
        "termination_status": outcomes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["prepare", "run", "status", "clear-stale-claims"])
    parser.add_argument("--config", default="configs/scopf_campaign_riker.yaml")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--max-tasks", type=int, default=0, help="Stop after this many tasks (0 = no limit).")
    parser.add_argument("--tier", action="append", help="run: only claim tasks from this tier (repeatable).")
    args = parser.parse_args()
    config = load_config(_resolve(args.config))
    if args.command == "prepare":
        out = prepare(config)
    elif args.command == "run":
        out = run(config, args.python, args.max_tasks, args.tier)
    elif args.command == "clear-stale-claims":
        # Only safe when no campaign job is running (e.g. after a timeout/cancel).
        out = clear_stale_claims(config)
    else:
        out = status(config)
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
