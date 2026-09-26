from __future__ import annotations

import copy
import hashlib
import json
import math
import random
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Iterable

import pyarrow.parquet as pq

from grid_data_factory.pf.anchors import topology_hash
from grid_data_factory.pf.controls import apply_response_policy, balanced_redispatch, controls_from_case, normalized_control_distance
from grid_data_factory.pf.schemas import PFCandidate, PFControls


DEFAULT_DISTANCE_STRATA = {
    "exact_consistency": {"fraction": 0.02, "minimum": 0.0, "maximum": 0.0},
    "near_opf": {"fraction": 0.18, "minimum": 0.005, "maximum": 0.03},
    "intermediate": {"fraction": 0.35, "minimum": 0.03, "maximum": 0.10},
    "large_credible": {"fraction": 0.25, "minimum": 0.10, "maximum": 0.25},
    "boundary_directed": {"fraction": 0.20, "minimum": 0.18, "maximum": 0.30},
}

DEFAULT_TOPOLOGY_QUOTAS = {
    "intact": 0.50,
    "n1_branch": 0.25,
    "n1_generator": 0.15,
    "n2_structured": 0.10,
}


def allocate_quota(total: int, fractions: dict[str, float], existing: dict[str, int] | None = None) -> dict[str, int]:
    if total < 0 or not fractions or any(value < 0.0 for value in fractions.values()):
        raise ValueError("Quota totals and fractions must be non-negative")
    fraction_total = sum(fractions.values())
    if fraction_total <= 0.0:
        raise ValueError("At least one quota fraction must be positive")
    existing = existing or {}
    target_total = total + sum(existing.values())
    deficits = {
        key: max(0.0, target_total * value / fraction_total - existing.get(key, 0))
        for key, value in fractions.items()
    }
    deficit_total = sum(deficits.values())
    weights = deficits if deficit_total > 0.0 else fractions
    weight_total = sum(weights.values())
    raw = {key: total * value / weight_total for key, value in weights.items()}
    counts = {key: math.floor(value) for key, value in raw.items()}
    remaining = total - sum(counts.values())
    order = sorted(raw, key=lambda key: (-(raw[key] - counts[key]), key))
    for key in order[:remaining]:
        counts[key] += 1
    return counts


def read_prior_stratum_counts(paths: Iterable[Path]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for path in paths:
        if not Path(path).exists():
            continue
        with Path(path).open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("task") == "pf" and record.get("outcome_class") == "converged_valid":
                    counts[str(record.get("control_distance_stratum", "unknown"))] += 1
    return dict(counts)


def load_anchor_pool(path: Path, maximum: int, seed: int, intact_only: bool = True) -> list[dict[str, Any]]:
    if maximum <= 0:
        raise ValueError("maximum must be positive")
    rng = random.Random(seed)
    reservoir: list[dict[str, Any]] = []
    eligible_count = 0
    parquet = pq.ParquetFile(path)
    for batch in parquet.iter_batches():
        for row in batch.to_pylist():
            source_candidate = json.loads(row["candidate_json"])
            if intact_only and source_candidate.get("contingency"):
                continue
            eligible_count += 1
            if len(reservoir) < maximum:
                reservoir.append(row)
            else:
                index = rng.randrange(eligible_count)
                if index < maximum:
                    reservoir[index] = row
    if not reservoir:
        raise ValueError("Anchor index contains no eligible successful intact AC-OPF records")
    reservoir.sort(key=lambda row: (row["source_ac_opf_run_id"], row["candidate_id"]))
    return reservoir


def _anchor_case_and_controls(anchor: dict[str, Any]) -> tuple[dict[str, Any], PFControls]:
    case_data = json.loads(anchor["resolved_case_json"])
    anchor_controls = json.loads(anchor["controls_json"])
    controls = controls_from_case(case_data).model_dump()
    controls["generators"].update(anchor_controls.get("generators", {}))
    parsed_controls = PFControls.model_validate(controls)
    for generator in case_data.get("generators", []):
        gid = str(generator["gen_id"])
        if gid in parsed_controls.generators:
            control = parsed_controls.generators[gid]
            generator.update({"pg": control.pg, "qg": control.qg or 0.0, "vg": control.vg})
    return case_data, parsed_controls


def _sample_controls(
    case_data: dict[str, Any],
    anchor_controls: PFControls,
    minimum: float,
    maximum: float,
    rng: random.Random,
    max_attempts: int,
) -> tuple[PFControls, float, dict[str, Any]]:
    if maximum == 0.0:
        return anchor_controls, 0.0, {"proposed_delta_pg": {}, "applied_delta_pg": {}, "residual_mw": 0.0}
    generators = [gen for gen in case_data.get("generators", []) if int(gen.get("status", 1)) > 0]
    target = rng.uniform(minimum, maximum)
    bus_by_id = {str(bus["bus_id"]): bus for bus in case_data.get("buses", [])}
    for _ in range(max_attempts):
        direction = {str(gen["gen_id"]): rng.uniform(-1.0, 1.0) * (float(gen["pmax"]) - float(gen["pmin"])) for gen in generators}
        generator_bus_ids = {str(gen["bus_id"]) for gen in generators}
        voltage_direction = {bus_id: rng.uniform(-1.0, 1.0) for bus_id in generator_bus_ids}
        anchor_voltage_by_bus = {
            bus_id: next(
                anchor_controls.generators[str(gen["gen_id"])].vg
                for gen in generators
                if str(gen["bus_id"]) == bus_id
            )
            for bus_id in generator_bus_ids
        }
        scale = max(target, 0.01)
        best: tuple[PFControls, float, dict[str, Any]] | None = None
        for _ in range(10):
            deltas = {gid: scale * value for gid, value in direction.items()}
            voltages = {}
            for gen in generators:
                gid = str(gen["gen_id"])
                bus_id = str(gen["bus_id"])
                bus = bus_by_id[bus_id]
                span = float(bus.get("vmax", 1.1)) - float(bus.get("vmin", 0.9))
                proposed = anchor_voltage_by_bus[bus_id] + scale * voltage_direction[bus_id] * span
                voltages[gid] = min(float(bus.get("vmax", 1.1)), max(float(bus.get("vmin", 0.9)), proposed))
            controls, metadata = balanced_redispatch(case_data, deltas, voltage_setpoints=voltages)
            distance = normalized_control_distance(case_data, anchor_controls, controls)
            best = controls, distance, metadata
            if minimum <= distance <= maximum:
                return best
            scale *= target / max(distance, 1e-9)
        if best is not None and minimum <= best[1] <= maximum:
            return best
    raise ValueError(f"Could not generate controls in distance stratum [{minimum}, {maximum}]")


def _sample_contingency(case_data: dict[str, Any], topology_class: str, rng: random.Random) -> tuple[dict[str, Any] | None, str]:
    branches = [branch for branch in case_data.get("branches", []) if int(branch.get("status", 1)) > 0]
    generators = [
        gen for gen in case_data.get("generators", [])
        if int(gen.get("status", 1)) > 0 and not any(
            str(bus["bus_id"]) == str(gen["bus_id"]) and int(bus.get("type", 1)) == 3
            for bus in case_data.get("buses", [])
        )
    ]
    if topology_class == "intact":
        return None, "fixed_controls_slack_loss"
    if topology_class == "n1_branch" and branches:
        branch = rng.choice(branches)
        return {"event_type": "simultaneous", "components": [{"type": "branch", "id": str(branch["branch_id"])}]}, "fixed_controls_slack_loss"
    if topology_class == "n1_generator" and generators:
        generator = rng.choice(generators)
        return {"event_type": "simultaneous", "components": [{"type": "generator", "id": str(generator["gen_id"])}]}, "reserve_participation"
    if topology_class == "n2_structured" and len(branches) >= 2:
        first = rng.randrange(len(branches))
        adjacent = [
            branch for index, branch in enumerate(branches) if index != first and {
                str(branch["from"]), str(branch["to"])
            } & {str(branches[first]["from"]), str(branches[first]["to"])}
        ]
        second = rng.choice(adjacent or [branch for index, branch in enumerate(branches) if index != first])
        return {
            "event_type": "simultaneous",
            "components": [
                {"type": "branch", "id": str(branches[first]["branch_id"])},
                {"type": "branch", "id": str(second["branch_id"])},
            ],
        }, "fixed_controls_slack_loss"
    raise ValueError(f"Anchor case cannot support topology class {topology_class}")


def _criticality_buckets(
    case_data: dict[str, Any], source_solution: dict[str, Any]
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    branch_solution = source_solution.get("branch") or {}
    generator_solution = source_solution.get("gen") or {}
    branch_score = {
        str(branch["branch_id"]): max(
            abs(float((branch_solution.get(str(index)) or {}).get("pf", 0.0))),
            abs(float((branch_solution.get(str(index)) or {}).get("pt", 0.0))),
        )
        for index, branch in enumerate(case_data.get("branches", []), start=1)
    }
    generator_score = {
        str(generator["gen_id"]): abs(float((generator_solution.get(str(index)) or {}).get("pg", 0.0)))
        for index, generator in enumerate(case_data.get("generators", []), start=1)
    }
    branch_ranked = sorted(
        case_data.get("branches", []),
        key=lambda branch: branch_score[str(branch["branch_id"])],
        reverse=True,
    )
    generator_ranked = sorted(
        case_data.get("generators", []),
        key=lambda generator: generator_score[str(generator["gen_id"])],
        reverse=True,
    )

    def buckets(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        if not rows:
            return {"critical": [], "intermediate": [], "benign": [], "random_audit": []}
        third = max(1, math.ceil(len(rows) / 3))
        return {
            "critical": rows[:third],
            "intermediate": rows[third : 2 * third] or rows,
            "benign": rows[2 * third :] or rows[-third:],
            "random_audit": rows,
        }

    return buckets(branch_ranked), buckets(generator_ranked)


def _sample_stratified_contingency(
    case_data: dict[str, Any],
    source_solution: dict[str, Any],
    topology_class: str,
    rng: random.Random,
    criticality_stratum: str,
    generator_policy_id: str,
) -> tuple[dict[str, Any] | None, str]:
    branch_buckets, generator_buckets = _criticality_buckets(case_data, source_solution)
    if topology_class == "intact":
        return None, "fixed_controls_slack_loss"
    if topology_class == "n1_branch":
        branch = rng.choice(branch_buckets[criticality_stratum])
        return {"event_type": "simultaneous", "components": [{"type": "branch", "id": str(branch["branch_id"])}]}, "fixed_controls_slack_loss"
    if topology_class == "n1_generator":
        reference_bus_ids = {
            str(bus["bus_id"]) for bus in case_data.get("buses", []) if int(bus.get("type", 1)) == 3
        }
        eligible = [
            generator for generator in generator_buckets[criticality_stratum]
            if str(generator["bus_id"]) not in reference_bus_ids
        ]
        if not eligible:
            eligible = [
                generator for generator in generator_buckets["random_audit"]
                if str(generator["bus_id"]) not in reference_bus_ids
            ]
        if not eligible:
            raise ValueError("No non-reference generator available for outage")
        generator = rng.choice(eligible)
        return {"event_type": "simultaneous", "components": [{"type": "generator", "id": str(generator["gen_id"])}]}, generator_policy_id
    return _sample_contingency(case_data, topology_class, rng)


def generate_candidates(
    anchors: list[dict[str, Any]],
    count: int,
    seed: int,
    distance_strata: dict[str, dict[str, float]] | None = None,
    topology_quotas: dict[str, float] | None = None,
    prior_stratum_counts: dict[str, int] | None = None,
    criticality_quotas: dict[str, float] | None = None,
    generator_response_quotas: dict[str, float] | None = None,
    max_attempts: int = 50,
    candidate_sink: Callable[[dict[str, Any]], None] | None = None,
    retain_candidates: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if count <= 0:
        raise ValueError("count must be positive")
    distance_strata = distance_strata or DEFAULT_DISTANCE_STRATA
    topology_quotas = topology_quotas or DEFAULT_TOPOLOGY_QUOTAS
    criticality_quotas = criticality_quotas or {"critical": 0.40, "intermediate": 0.25, "benign": 0.25, "random_audit": 0.10}
    generator_response_quotas = generator_response_quotas or {"reserve_participation": 0.50, "fixed_participation": 0.25, "governor_droop": 0.25}
    distance_counts = allocate_quota(count, {key: value["fraction"] for key, value in distance_strata.items()}, prior_stratum_counts)
    topology_counts = allocate_quota(count, topology_quotas)
    distance_schedule = [name for name in sorted(distance_counts) for _ in range(distance_counts[name])]
    exact_count = distance_counts.get("exact_consistency", 0)
    if topology_counts.get("intact", 0) < exact_count:
        raise ValueError("The intact topology quota must accommodate all exact-consistency candidates")
    topology_counts["intact"] -= exact_count
    topology_schedule = ["intact"] * exact_count + [
        name for name in sorted(topology_counts) for _ in range(topology_counts[name])
    ]
    contingency_count = sum(value for key, value in topology_counts.items() if key != "intact")
    criticality_counts = allocate_quota(contingency_count, criticality_quotas)
    criticality_schedule = [name for name in sorted(criticality_counts) for _ in range(criticality_counts[name])]
    response_counts = allocate_quota(topology_counts.get("n1_generator", 0), generator_response_quotas)
    response_schedule = [name for name in sorted(response_counts) for _ in range(response_counts[name])]
    rng = random.Random(seed)
    rng.shuffle(distance_schedule)
    rng.shuffle(topology_schedule)
    rng.shuffle(criticality_schedule)
    rng.shuffle(response_schedule)
    candidates: list[dict[str, Any]] = []
    rejected = Counter()
    distance_actual = Counter()
    topology_actual = Counter()
    criticality_actual = Counter()
    response_actual = Counter()
    split_actual = Counter()
    contingency_index = 0
    generator_outage_index = 0

    for index in range(count):
        stratum = distance_schedule[index]
        topology_class = topology_schedule[index]
        if stratum == "exact_consistency" and topology_class != "intact":
            swap_index = next(
                (other for other in range(index + 1, count) if topology_schedule[other] == "intact"),
                None,
            )
            if swap_index is None:
                raise ValueError("Unable to pair exact-consistency controls with an intact topology")
            topology_schedule[index], topology_schedule[swap_index] = topology_schedule[swap_index], topology_schedule[index]
            topology_class = "intact"
        generated = None
        last_error: ValueError | KeyError | None = None
        for attempt in range(max_attempts):
            anchor = anchors[(index + attempt) % len(anchors)]
            try:
                case_data, anchor_controls = _anchor_case_and_controls(anchor)
                controls, distance, redispatch = _sample_controls(
                    case_data,
                    anchor_controls,
                    float(distance_strata[stratum]["minimum"]),
                    float(distance_strata[stratum]["maximum"]),
                    rng,
                    max_attempts=10,
                )
                source_solution = json.loads(anchor.get("solution_json") or "{}")
                criticality_stratum = "none" if topology_class == "intact" else criticality_schedule[contingency_index]
                policy_id = (
                    response_schedule[generator_outage_index]
                    if topology_class == "n1_generator"
                    else "fixed_controls_slack_loss"
                )
                contingency, policy_id = _sample_stratified_contingency(
                    case_data, source_solution, topology_class, rng, criticality_stratum, policy_id
                )
                active_generators = [gid for gid in controls.generators if not any(
                    component.get("type") == "generator" and str(component.get("id")) == gid
                    for component in (contingency or {}).get("components", [])
                )]
                equal_weights = {gid: 1.0 for gid in active_generators}
                response_policy: dict[str, Any] = {"policy_id": policy_id}
                if policy_id == "fixed_participation":
                    response_policy["participation_factors"] = equal_weights
                elif policy_id == "governor_droop":
                    response_policy["droop_coefficients"] = equal_weights
                post_case, _, _ = apply_response_policy(case_data, contingency, controls, response_policy)
                topology_id = f"{anchor['topology_id']}::{topology_class}"
                identity = hashlib.sha256(
                    json.dumps([anchor["source_ac_opf_run_id"], index, seed, stratum, topology_class], separators=(",", ":")).encode()
                ).hexdigest()[:16]
                generated = PFCandidate(
                    candidate_id=f"pf::{identity}",
                    case_id=str(anchor["case_id"]),
                    source_ac_opf_run_id=str(anchor["source_ac_opf_run_id"]),
                    parent_network_id=str(anchor["parent_network_id"]),
                    parent_topology_id=str(anchor["parent_topology_id"]),
                    parent_topology_hash=str(anchor["topology_hash"]),
                    topology_id=topology_id,
                    topology_hash=topology_hash(post_case),
                    dataset_split=str(anchor["dataset_split"]),
                    resolved_case=case_data,
                    controls=controls,
                    control_sampling_method="exact_anchor" if stratum == "exact_consistency" else "balanced_multigenerator",
                    control_distance=distance,
                    control_distance_stratum=stratum,
                    response_policy=response_policy,
                    contingency=contingency,
                    topology_class=topology_class,
                    contingency_order=len((contingency or {}).get("components", [])),
                    perturbation_seed=seed,
                    redispatch_metadata=redispatch,
                    candidate_generation_mechanism=stratum,
                    source_ac_opf_solution=(json.loads(anchor["solution_json"]) if stratum == "exact_consistency" else None),
                    contingency_criticality_stratum=criticality_stratum,
                    anchor_controls=anchor_controls.model_dump(mode="json"),
                ).model_dump(mode="json")
                break
            except (ValueError, KeyError) as exc:
                rejected[type(exc).__name__] += 1
                last_error = exc
        if generated is None:
            detail = f": {last_error}" if last_error is not None else ""
            raise ValueError(f"Unable to fill {stratum}/{topology_class} after {max_attempts} attempts{detail}")
        if candidate_sink is not None:
            candidate_sink(generated)
        if retain_candidates:
            candidates.append(generated)
        distance_actual[generated["control_distance_stratum"]] += 1
        topology_actual[generated["topology_class"]] += 1
        criticality_actual[generated["contingency_criticality_stratum"]] += 1
        response_actual[generated["response_policy"]["policy_id"]] += 1
        split_actual[generated["dataset_split"]] += 1
        if topology_class != "intact":
            contingency_index += 1
        if topology_class == "n1_generator":
            generator_outage_index += 1

    summary = {
        "candidate_count": sum(distance_actual.values()),
        "seed": seed,
        "distance_counts": dict(distance_actual),
        "topology_counts": dict(topology_actual),
        "criticality_counts": dict(criticality_actual),
        "response_policy_counts": dict(response_actual),
        "dataset_split_counts": dict(split_actual),
        "prior_stratum_counts": prior_stratum_counts or {},
        "rejected_attempts": dict(rejected),
    }
    return candidates, summary