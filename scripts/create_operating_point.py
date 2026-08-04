#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from random import Random
from typing import Any

try:
    from grid_data_factory.sources.registry import bus_count_for, dataset_for, grid_family_for, resolve_case_file
    from grid_data_factory.topology.generation import generate_topology_variants, read_network_skeleton
    from grid_data_factory.scenarios.load_snapshots import load_snapshot_registry
except ModuleNotFoundError:
    _repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(_repo_root / "src"))
    from grid_data_factory.sources.registry import bus_count_for, dataset_for, grid_family_for, resolve_case_file
    from grid_data_factory.topology.generation import generate_topology_variants, read_network_skeleton
    from grid_data_factory.scenarios.load_snapshots import load_snapshot_registry

from grid_data_factory.storage import paths  # noqa: E402


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


def _fallback_regimes_from_text(text: str) -> list[str]:
    marker = "operating_regimes:"
    idx = text.find(marker)
    if idx < 0:
        return []

    tail = text[idx + len(marker) :]
    out: list[str] = []
    for line in tail.splitlines():
        if not line.strip():
            continue
        if re.match(r"^\S", line):
            break
        m = re.match(r"^\s*-\s*(.+?)\s*$", line)
        if m:
            out.append(m.group(1).strip())
        elif out:
            break
    return out


def _fallback_local_noise(text: str, default: float = 0.02) -> float:
    m = re.search(r"local_noise_stddev\s*:\s*([0-9.eE+\-]+)", text)
    if not m:
        return default
    return float(m.group(1))


def _halton(index: int, base: int) -> float:
    f = 1.0
    r = 0.0
    i = index
    while i > 0:
        f = f / base
        r = r + f * (i % base)
        i = i // base
    return r


def _lhs_value(position: int, total: int, rng: Random) -> float:
    lo = position / total
    hi = (position + 1) / total
    return lo + (hi - lo) * rng.random()


def _sample_unit_vector(dim: int, idx: int, total: int, sampler: str, rng: Random) -> list[float]:
    if sampler == "sobol":
        primes = [2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41]
        return [_halton(idx + 1, primes[d % len(primes)]) for d in range(dim)]

    if sampler == "stratified":
        block = max(1, int(math.sqrt(total)))
        row = idx // block
        base = (row + 0.5) / block
        return [min(0.999, max(0.001, base + (rng.random() - 0.5) * 0.1)) for _ in range(dim)]

    if sampler == "time_series":
        t = idx / max(total - 1, 1)
        wave = 0.5 + 0.5 * math.sin(2.0 * math.pi * t)
        return [min(0.999, max(0.001, wave + (rng.random() - 0.5) * 0.08)) for _ in range(dim)]

    perm = list(range(total))
    rng.shuffle(perm)
    return [_lhs_value((perm[(idx + d) % total]), total, rng) for d in range(dim)]


def _lin(lo: float, hi: float, x: float) -> float:
    return float(lo) + (float(hi) - float(lo)) * float(x)


def _regime_ranges(regime: str) -> dict[str, tuple[float, float]]:
    table: dict[str, dict[str, tuple[float, float]]] = {
        "baseline": {
            "global_load_scale": (0.98, 1.02),
            "renewable_scale": (0.9, 1.1),
            "reserve_margin": (0.12, 0.20),
            "branch_rating_scale": (0.98, 1.02),
            "cost_scale": (0.95, 1.05),
        },
        "low_load": {
            "global_load_scale": (0.70, 0.92),
            "renewable_scale": (0.7, 1.2),
            "reserve_margin": (0.20, 0.40),
            "branch_rating_scale": (1.0, 1.08),
            "cost_scale": (0.9, 1.05),
        },
        "summer_peak": {
            "global_load_scale": (1.05, 1.30),
            "renewable_scale": (0.8, 1.1),
            "reserve_margin": (0.05, 0.16),
            "branch_rating_scale": (0.9, 1.0),
            "cost_scale": (1.0, 1.2),
        },
        "winter_peak": {
            "global_load_scale": (1.02, 1.24),
            "renewable_scale": (0.7, 1.0),
            "reserve_margin": (0.05, 0.15),
            "branch_rating_scale": (0.9, 1.0),
            "cost_scale": (1.0, 1.25),
        },
        "high_renewable": {
            "global_load_scale": (0.9, 1.1),
            "renewable_scale": (1.1, 1.4),
            "reserve_margin": (0.08, 0.2),
            "branch_rating_scale": (0.92, 1.05),
            "cost_scale": (0.85, 1.0),
        },
        "low_renewable": {
            "global_load_scale": (0.9, 1.15),
            "renewable_scale": (0.4, 0.9),
            "reserve_margin": (0.10, 0.25),
            "branch_rating_scale": (0.92, 1.03),
            "cost_scale": (1.0, 1.2),
        },
        "high_import": {
            "global_load_scale": (0.95, 1.20),
            "renewable_scale": (0.8, 1.2),
            "reserve_margin": (0.06, 0.16),
            "branch_rating_scale": (0.90, 1.0),
            "cost_scale": (0.95, 1.15),
        },
        "high_export": {
            "global_load_scale": (0.85, 1.05),
            "renewable_scale": (0.9, 1.3),
            "reserve_margin": (0.08, 0.20),
            "branch_rating_scale": (0.92, 1.02),
            "cost_scale": (0.9, 1.1),
        },
        "maintenance": {
            "global_load_scale": (0.90, 1.10),
            "renewable_scale": (0.8, 1.1),
            "reserve_margin": (0.06, 0.16),
            "branch_rating_scale": (0.80, 0.95),
            "cost_scale": (0.95, 1.15),
        },
        "branch_derating": {
            "global_load_scale": (0.92, 1.20),
            "renewable_scale": (0.75, 1.2),
            "reserve_margin": (0.06, 0.16),
            "branch_rating_scale": (0.70, 0.92),
            "cost_scale": (0.95, 1.1),
        },
        "generator_cost_shift": {
            "global_load_scale": (0.9, 1.15),
            "renewable_scale": (0.8, 1.2),
            "reserve_margin": (0.08, 0.20),
            "branch_rating_scale": (0.92, 1.02),
            "cost_scale": (1.1, 1.35),
        },
        "reactive_stress": {
            "global_load_scale": (0.95, 1.2),
            "renewable_scale": (0.8, 1.1),
            "reserve_margin": (0.06, 0.16),
            "branch_rating_scale": (0.90, 1.0),
            "cost_scale": (0.95, 1.15),
        },
        "extreme_peak": {
            "global_load_scale": (1.2, 1.45),
            "renewable_scale": (0.6, 1.0),
            "reserve_margin": (0.02, 0.10),
            "branch_rating_scale": (0.75, 0.95),
            "cost_scale": (1.15, 1.5),
        },
        "shoulder": {
            "global_load_scale": (0.9, 1.05),
            "renewable_scale": (0.9, 1.2),
            "reserve_margin": (0.10, 0.24),
            "branch_rating_scale": (0.95, 1.03),
            "cost_scale": (0.92, 1.08),
        },
    }
    return table.get(regime, table["baseline"])


def _compute_scores(params: dict[str, float], regime: str, rng: Random) -> dict[str, float]:
    load_stress = max(0.0, params["global_load_scale"] - 1.0)
    reserve_stress = max(0.0, 0.2 - params["reserve_margin"])
    thermal_stress = max(0.0, 1.0 - params["branch_rating_scale"])
    q_stress = max(0.0, params["regional_reactive_scale_north"] - 1.0) + max(0.0, params["regional_reactive_scale_south"] - 1.0)

    novelty = min(
        1.0,
        0.4 * abs(params["global_load_scale"] - 1.0)
        + 0.25 * abs(params["renewable_scale"] - 1.0)
        + 0.2 * abs(params["cost_scale"] - 1.0)
        + 0.15 * rng.random(),
    )
    active_constraint = min(1.0, 0.45 * load_stress + 0.35 * thermal_stress + 0.2 * q_stress)
    security_boundary = min(1.0, 0.5 * load_stress + 0.3 * reserve_stress + 0.2 * thermal_stress)
    contingency_severity = min(1.0, 0.4 * thermal_stress + 0.4 * load_stress + 0.2 * rng.random())

    credibility_penalty = 0.0
    if regime in {"extreme_peak", "maintenance"}:
        credibility_penalty += 0.1
    if params["global_load_scale"] > 1.35 and params["reserve_margin"] > 0.22:
        credibility_penalty += 0.25
    if params["branch_rating_scale"] < 0.75 and params["renewable_scale"] > 1.3:
        credibility_penalty += 0.2

    physical_credibility = max(0.0, min(1.0, 0.95 - credibility_penalty))
    uncertainty = min(1.0, 0.25 + 0.35 * novelty + 0.2 * rng.random())

    return {
        "novelty_score": novelty,
        "active_constraint_score": active_constraint,
        "security_boundary_score": security_boundary,
        "contingency_severity_score": contingency_severity,
        "physical_credibility_score": physical_credibility,
        "model_uncertainty_score": uncertainty,
    }


def _band(x: float) -> str:
    if x < 0.25:
        return "low"
    if x < 0.6:
        return "medium"
    return "high"


def _case_size_hint(case_id: str, bus_count: int | None = None) -> int:
    if bus_count and bus_count > 0:
        return int(bus_count)
    lower = case_id.lower()
    if "118" in lower:
        return 118
    if "57" in lower:
        return 57
    if "14" in lower:
        return 14
    return 100


def _estimated_cost(case_id: str, params: dict[str, float], bus_count: int | None = None) -> float:
    nbus = _case_size_hint(case_id, bus_count)
    stress = 1.0 + max(0.0, params["global_load_scale"] - 1.0) + max(0.0, 1.0 - params["branch_rating_scale"])
    return float(nbus) * stress


def _choose_regime(regimes: list[str], idx: int, total: int, sampler: str, rng: Random) -> str:
    if sampler == "regime_specific":
        return regimes[idx % len(regimes)]
    if sampler == "time_series":
        season = int((idx / max(total, 1)) * len(regimes))
        return regimes[min(season, len(regimes) - 1)]
    return regimes[rng.randrange(len(regimes))]


def _build_candidate(
    case_id: str,
    candidate_index: int,
    regime: str,
    vec: list[float],
    local_noise_stddev: float,
    rng: Random,
    sampler: str,
    grid_family: str = "unknown",
    dataset: str = "unknown",
    bus_count: int | None = None,
    topology: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rr = _regime_ranges(regime)
    params = {
        "global_load_scale": _lin(*rr["global_load_scale"], vec[0]),
        "renewable_scale": _lin(*rr["renewable_scale"], vec[1]),
        "reserve_margin": _lin(*rr["reserve_margin"], vec[2]),
        "branch_rating_scale": _lin(*rr["branch_rating_scale"], vec[3]),
        "cost_scale": _lin(*rr["cost_scale"], vec[4]),
        "regional_load_scale_north": _lin(0.92, 1.08, vec[5]),
        "regional_load_scale_south": _lin(0.92, 1.08, vec[6]),
        "regional_load_scale_east": _lin(0.92, 1.08, vec[7]),
        "regional_load_scale_west": _lin(0.92, 1.08, vec[8]),
        "regional_reactive_scale_north": _lin(0.9, 1.12, vec[9]),
        "regional_reactive_scale_south": _lin(0.9, 1.12, vec[10]),
        "wind_availability_north": _lin(0.5, 1.1, vec[11]),
        "solar_availability_south": _lin(0.5, 1.1, vec[12]),
        "hydro_availability": _lin(0.75, 1.1, vec[0]),
        "generator_fleet_availability": _lin(0.85, 1.0, vec[1]),
        "area_interchange_target": _lin(-0.15, 0.15, vec[2]),
    }

    for key in (
        "regional_load_scale_north",
        "regional_load_scale_south",
        "regional_load_scale_east",
        "regional_load_scale_west",
        "regional_reactive_scale_north",
        "regional_reactive_scale_south",
    ):
        params[key] = float(params[key]) * float(math.exp(rng.gauss(0.0, local_noise_stddev)))

    scores = _compute_scores(params, regime, rng)
    dc_severity_score = min(1.0, 0.55 * scores["security_boundary_score"] + 0.45 * scores["contingency_severity_score"])
    voltage_risk_score = min(
        1.0,
        0.6 * max(0.0, params["regional_reactive_scale_north"] - 1.0)
        + 0.4 * max(0.0, params["regional_reactive_scale_south"] - 1.0),
    )
    reactive_risk_score = min(1.0, 0.5 * max(0.0, 1.0 - params["reserve_margin"]) + 0.5 * voltage_risk_score)

    topology = topology or {"topology_id": "topology_000000_baseline", "topology_class": "baseline", "switched_off_branches": [], "switched_branch_count": 0}

    return {
        "candidate_id": f"{case_id}::op::{candidate_index:06d}",
        "grid_id": case_id,
        "grid_family": grid_family,
        "dataset": dataset,
        "case_id": case_id,
        "task": "ac_opf",
        "candidate_generation_mechanism": f"space_filling_{sampler}",
        "operating_regime": regime,
        "operating_point_parameters": params,
        "contingency_order": 0,
        "contingency_class": "none",
        "topology_id": topology["topology_id"],
        "topology_class": topology.get("topology_class", "baseline"),
        "switched_off_branches": list(topology.get("switched_off_branches", [])),
        "switched_branch_count": int(topology.get("switched_branch_count", 0)),
        "security_margin_hint": 0.2 - scores["security_boundary_score"],
        "dc_severity_score": dc_severity_score,
        "voltage_risk_score": voltage_risk_score,
        "reactive_risk_score": reactive_risk_score,
        "dc_severity_band": _band(dc_severity_score),
        "voltage_risk_band": _band(voltage_risk_score),
        "reactive_risk_band": _band(reactive_risk_score),
        "estimated_compute_cost": _estimated_cost(case_id, params, bus_count),
        **scores,
    }


_BASELINE_TOPOLOGY = {
    "topology_id": "topology_000000_baseline",
    "topology_index": 0,
    "topology_class": "baseline",
    "switched_off_branches": [],
    "switched_branch_count": 0,
}


def _prepare_topologies(repo_root: Path, case_id: str, n_variants: int, seed: int, max_switched: int) -> list[dict[str, Any]]:
    if n_variants <= 1:
        return [dict(_BASELINE_TOPOLOGY)]
    try:
        case_file = resolve_case_file(repo_root, case_id)
        if not case_file.exists():
            return [dict(_BASELINE_TOPOLOGY)]
        bus_ids, branches = read_network_skeleton(case_file)
        variants = generate_topology_variants(case_id, bus_ids, branches, n_variants=n_variants, seed=seed, max_switched=max_switched)
    except Exception:  # noqa: BLE001 - fall back to baseline-only topology on any parse failure
        return [dict(_BASELINE_TOPOLOGY)]

    registry_dir = paths.topology_registry_dir(repo_root) / case_id
    registry_dir.mkdir(parents=True, exist_ok=True)
    with (registry_dir / "topology_variants.jsonl").open("w", encoding="utf-8") as fh:
        for v in variants:
            record = dict(v)
            record["case_id"] = case_id
            record["seed"] = seed
            fh.write(json.dumps(record, ensure_ascii=True) + "\n")
    return variants


_DIFFICULTY_STRESS = {"easy": 0.2, "medium": 0.5, "hard": 0.8, "unknown": 0.4}

_NEUTRAL_OP_PARAMS = {
    "global_load_scale": 1.0,
    "renewable_scale": 1.0,
    "reserve_margin": 0.15,
    "branch_rating_scale": 1.0,
    "cost_scale": 1.0,
    "regional_load_scale_north": 1.0,
    "regional_load_scale_south": 1.0,
    "regional_load_scale_east": 1.0,
    "regional_load_scale_west": 1.0,
    "regional_reactive_scale_north": 1.0,
    "regional_reactive_scale_south": 1.0,
    "wind_availability_north": 1.0,
    "solar_availability_south": 1.0,
    "hydro_availability": 1.0,
    "generator_fleet_availability": 1.0,
    "area_interchange_target": 0.0,
}


def _build_snapshot_candidate(
    case_id: str,
    candidate_index: int,
    snapshot: dict[str, Any],
    grid_family: str,
    dataset: str,
    bus_count: int | None,
    topology: dict[str, Any],
) -> dict[str, Any]:
    difficulty = str(snapshot.get("difficulty", "unknown"))
    voltage_regime = str(snapshot.get("voltage_regime", "unknown"))
    season = str(snapshot.get("season", "unknown"))

    stress = _DIFFICULTY_STRESS.get(difficulty, 0.4)
    if voltage_regime == "tight":
        stress = min(1.0, stress + 0.1)

    params = dict(_NEUTRAL_OP_PARAMS)
    params["load_snapshot_id"] = snapshot["snapshot_id"]

    regime = f"seasonal_{season}" if season != "unknown" else "seasonal_snapshot"

    return {
        "candidate_id": f"{case_id}::snap::{candidate_index:06d}",
        "grid_id": case_id,
        "grid_family": grid_family,
        "dataset": dataset,
        "case_id": case_id,
        "task": "ac_opf",
        "candidate_generation_mechanism": "reference_load_snapshot",
        "operating_regime": regime,
        "operating_point_parameters": params,
        "load_snapshot_id": snapshot["snapshot_id"],
        "snapshot_season": season,
        "snapshot_voltage_regime": voltage_regime,
        "snapshot_difficulty": difficulty,
        "snapshot_total_pd": float(snapshot.get("total_pd", 0.0)),
        "contingency_order": 0,
        "contingency_class": "none",
        "topology_id": topology["topology_id"],
        "topology_class": topology.get("topology_class", "baseline"),
        "switched_off_branches": list(topology.get("switched_off_branches", [])),
        "switched_branch_count": int(topology.get("switched_branch_count", 0)),
        "security_margin_hint": max(0.0, 0.2 - stress * 0.2),
        "dc_severity_score": stress,
        "voltage_risk_score": 0.6 * stress if voltage_regime == "tight" else 0.3 * stress,
        "reactive_risk_score": 0.5 * stress,
        "dc_severity_band": _band(stress),
        "voltage_risk_band": _band(0.6 * stress if voltage_regime == "tight" else 0.3 * stress),
        "reactive_risk_band": _band(0.5 * stress),
        "estimated_compute_cost": float(_case_size_hint(case_id, bus_count)) * (1.0 + stress),
        "novelty_score": 0.8,
        "active_constraint_score": stress,
        "security_boundary_score": stress,
        "contingency_severity_score": 0.5 * stress,
        "physical_credibility_score": 1.0,
        "model_uncertainty_score": 0.5,
    }


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
        regimes = _fallback_regimes_from_text(campaign_text)
    if not regimes:
        regimes = list((op_cfg.get("operating_points") or {}).get("regimes") or ["baseline"])
    if not regimes:
        regimes = ["baseline"]

    local_noise_stddev = float(
        ((op_cfg.get("operating_points") or {}).get("correlated_sampling") or {}).get("local_noise_stddev", 0.02)
    )
    if not op_cfg:
        local_noise_stddev = _fallback_local_noise(op_text, default=0.02)
    rng = Random(args.seed)

    rows: list[dict[str, Any]] = []
    dim = 13
    for case_id in args.cases:
        family = grid_family_for(repo_root, case_id)
        dataset = dataset_for(repo_root, case_id)
        bus_count = bus_count_for(repo_root, case_id)
        topologies = _prepare_topologies(repo_root, case_id, args.topologies_per_case, args.seed, args.max_switched_branches)
        for i in range(args.per_case):
            regime = _choose_regime(regimes, i, args.per_case, args.sampler, rng)
            vec = _sample_unit_vector(dim=dim, idx=i, total=args.per_case, sampler=args.sampler, rng=rng)
            topology = topologies[i % len(topologies)]
            rows.append(
                _build_candidate(
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
                )
            )

        if args.load_snapshots == "auto":
            registry = load_snapshot_registry(repo_root, case_id)
            snapshots = list((registry or {}).get("snapshots", {}).values()) if registry else []
            for j, snapshot in enumerate(snapshots):
                rows.append(
                    _build_snapshot_candidate(
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
