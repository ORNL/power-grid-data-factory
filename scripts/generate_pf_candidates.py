#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

try:
    from grid_data_factory.pf.candidates import generate_candidates, load_anchor_pool, read_prior_stratum_counts
except ModuleNotFoundError:
    _repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(_repo_root / "src"))
    from grid_data_factory.pf.candidates import generate_candidates, load_anchor_pool, read_prior_stratum_counts


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate stratified, post-projection-diverse PF candidates.")
    parser.add_argument("--anchor-index", required=True)
    parser.add_argument("--config", default="configs/pf_campaign_riker.yaml")
    parser.add_argument("--output", required=True)
    parser.add_argument("--summary", default="")
    parser.add_argument("--count", type=int, default=0)
    parser.add_argument("--seed", type=int, default=-1)
    parser.add_argument("--prior-samples", nargs="*", default=[])
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    config_path = Path(args.config)
    config_path = config_path if config_path.is_absolute() else repo_root / config_path
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    generation = config.get("generation") or {}
    count = args.count or int(generation.get("candidate_count", 10_000))
    seed = args.seed if args.seed >= 0 else int(generation.get("seed", 20260925))
    anchor_path = Path(args.anchor_index)
    anchor_path = anchor_path if anchor_path.is_absolute() else repo_root / anchor_path
    prior_paths = [Path(path) if Path(path).is_absolute() else repo_root / path for path in args.prior_samples]
    anchors = load_anchor_pool(
        anchor_path,
        maximum=int(generation.get("maximum_anchors", max(count, 1))),
        seed=seed,
        intact_only=bool(generation.get("intact_anchors_only", True)),
    )
    output = Path(args.output)
    output = output if output.is_absolute() else repo_root / output
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = output.with_suffix(output.suffix + ".in_progress")
    with temporary_output.open("w", encoding="utf-8") as handle:
        _, summary = generate_candidates(
            anchors,
            count,
            seed,
            distance_strata=config.get("control_distance_strata"),
            topology_quotas=config.get("topology_quotas"),
            criticality_quotas=config.get("contingency_criticality_quotas"),
            generator_response_quotas=config.get("generator_response_quotas"),
            prior_stratum_counts=read_prior_stratum_counts(prior_paths),
            max_attempts=int(generation.get("max_attempts_per_candidate", 50)),
            candidate_sink=lambda candidate: handle.write(json.dumps(candidate, sort_keys=True) + "\n"),
            retain_candidates=False,
        )
    temporary_output.replace(output)
    summary_path = Path(args.summary) if args.summary else output.with_suffix(".summary.json")
    summary_path = summary_path if summary_path.is_absolute() else repo_root / summary_path
    summary["anchor_index"] = str(anchor_path)
    summary["output"] = str(output)
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**summary, "summary": str(summary_path)}, indent=2))


if __name__ == "__main__":
    main()