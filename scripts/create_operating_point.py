#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from random import Random
from typing import Any

try:
    from grid_data_factory.sources.registry import bus_count_for, dataset_for, grid_family_for
    from grid_data_factory.scenarios.load_snapshots import load_snapshot_registry
    from grid_data_factory.scenarios.operating_point_generation import (
        build_candidate,
        build_snapshot_candidate,
        choose_regime,
        fallback_local_noise,
        fallback_regimes_from_text,
        prepare_topologies,
        prepare_expansions,
        sample_unit_vector,
    )
except ModuleNotFoundError:
    _repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(_repo_root / "src"))
    from grid_data_factory.sources.registry import bus_count_for, dataset_for, grid_family_for
    from grid_data_factory.scenarios.load_snapshots import load_snapshot_registry
    from grid_data_factory.scenarios.operating_point_generation import (
        build_candidate,
        build_snapshot_candidate,
        choose_regime,
        fallback_local_noise,
        fallback_regimes_from_text,
        prepare_topologies,
        prepare_expansions,
        sample_unit_vector,
    )


def _require_yaml():
    try:
        import yaml  # type: ignore
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "Missing dependency 'pyyaml'. Install project requirements before running operating-point generation."
        ) from exc
    return yaml


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate structured operating-point candidates for adaptive campaigns.")
    p.add_argument("--config", default="configs/campaign_default.yaml")
    p.add_argument("--operating-config", default="configs/operating_points.yaml")
    p.add_argument("--cases", nargs="+", default=["pglib_opf_case14_ieee", "pglib_opf_case57_ieee", "pglib_opf_case118_ieee"])
    p.add_argument("--per-case", type=int, default=500)
    p.add_argument(
        "--sampler",
        choices=["sobol", "latin_hypercube", "stratified", "regime_specific", "time_series"],
        default="latin_hypercube",
    )
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--topologies-per-case", type=int, default=6, help="Distinct independent base topologies to generate per case (>=1).")
    p.add_argument("--max-switched-branches", type=int, default=3, help="Maximum persistently switched-off branches per non-baseline topology.")
    p.add_argument(
        "--expansions-per-case",
        type=int,
        default=0,
        help="Network-expansion plans (new buses/generators/transformers) to cycle across candidates per case. 0 (default) disables the expansion axis.",
    )
    p.add_argument(
        "--expansion-max-steps",
        type=int,
        default=1,
        help="Max units per expansion plan. >1 emits cascades (units added sequentially, each seeing the prior ones). Default 1 = single-unit plans.",
    )
    p.add_argument(
        "--expansion-size-multiplier",
        type=float,
        default=1.0,
        help="Scale factor on each new unit's capacity (generator pmax, branch rating). Default 1.0 = self-scaled to the case median.",
    )
    p.add_argument(
        "--expansion-max-additions-fraction",
        type=float,
        default=0.0,
        help="Relative cascade depth: ceiling = round(fraction * bus_count), floored at 1, capped by --expansion-max-steps. Bigger grids get proportionally bigger expansions. 0 (default) = use --expansion-max-steps directly.",
    )
    p.add_argument(
        "--transformer-tap-sigma",
        type=float,
        default=0.0,
        help="Per-sample transformer tap sweep magnitude (multiplicative, U[1-sigma,1+sigma), clamped to [0.9,1.1]). Only affects transformers. 0 (default) = off. Requires run_opf_expansion.jl to be observed.",
    )
    p.add_argument(
        "--transformer-shift-sigma",
        type=float,
        default=0.0,
        help="Per-sample transformer phase-shift sweep magnitude in degrees (additive, U[-sigma,+sigma)). Only affects transformers. 0 (default) = off. Requires run_opf_expansion.jl to be observed.",
    )
    p.add_argument(
        "--load-snapshots",
        choices=["auto", "off"],
        default="auto",
        help="Emit reference load-snapshot operating points when a snapshot registry exists for a case.",
    )
    p.add_argument("--out", required=True, help="Output JSONL path.")
    return p.parse_args()


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        yaml = _require_yaml()
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        return payload or {}
    except ModuleNotFoundError:
        return {}


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    campaign_path = (repo_root / args.config).resolve()
    operating_path = (repo_root / args.operating_config).resolve()

    campaign_cfg = _load_yaml(campaign_path)
    op_cfg = _load_yaml(operating_path)
    campaign_text = campaign_path.read_text(encoding="utf-8") if campaign_path.exists() else ""
    op_text = operating_path.read_text(encoding="utf-8") if operating_path.exists() else ""

    regimes = list(campaign_cfg.get("operating_regimes") or [])
    if not regimes:
        regimes = fallback_regimes_from_text(campaign_text)
    if not regimes:
        regimes = list((op_cfg.get("operating_points") or {}).get("regimes") or ["baseline"])
    if not regimes:
        regimes = ["baseline"]

    local_noise_stddev = float(
        ((op_cfg.get("operating_points") or {}).get("correlated_sampling") or {}).get("local_noise_stddev", 0.02)
    )
    if not op_cfg:
        local_noise_stddev = fallback_local_noise(op_text, default=0.02)
    rng = Random(args.seed)

    rows: list[dict[str, Any]] = []
    dim = 18
    for case_id in args.cases:
        family = grid_family_for(repo_root, case_id)
        dataset = dataset_for(repo_root, case_id)
        bus_count = bus_count_for(repo_root, case_id)
        topologies = prepare_topologies(repo_root, case_id, args.topologies_per_case, args.seed, args.max_switched_branches)
        expansions = (
            prepare_expansions(
                repo_root,
                case_id,
                args.expansions_per_case,
                args.seed,
                max_steps=args.expansion_max_steps,
                size_multiplier=args.expansion_size_multiplier,
                max_additions_fraction=args.expansion_max_additions_fraction,
            )
            if args.expansions_per_case > 0
            else []
        )
        for i in range(args.per_case):
            regime = choose_regime(regimes, i, args.per_case, args.sampler, rng)
            vec = sample_unit_vector(dim=dim, idx=i, total=args.per_case, sampler=args.sampler, rng=rng)
            topology = topologies[i % len(topologies)]
            expansion = expansions[i % len(expansions)] if expansions else None
            rows.append(
                build_candidate(
                    case_id,
                    i,
                    regime,
                    vec,
                    local_noise_stddev,
                    rng,
                    args.sampler,
                    grid_family=family,
                    dataset=dataset,
                    bus_count=bus_count,
                    topology=topology,
                    expansion=expansion,
                    transformer_tap_sigma=args.transformer_tap_sigma,
                    transformer_shift_sigma=args.transformer_shift_sigma,
                )
            )

        if args.load_snapshots == "auto":
            registry = load_snapshot_registry(repo_root, case_id)
            snapshots = list((registry or {}).get("snapshots", {}).values()) if registry else []
            for j, snapshot in enumerate(snapshots):
                rows.append(
                    build_snapshot_candidate(
                        case_id,
                        j,
                        snapshot,
                        grid_family=family,
                        dataset=dataset,
                        bus_count=bus_count,
                        topology=topologies[0],
                    )
                )

    out = Path(args.out)
    out = out if out.is_absolute() else (repo_root / out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=True) + "\n")

    print(
        json.dumps(
            {
                "ok": True,
                "out": str(out),
                "cases": args.cases,
                "per_case": args.per_case,
                "total": len(rows),
                "sampler": args.sampler,
                "seed": args.seed,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
