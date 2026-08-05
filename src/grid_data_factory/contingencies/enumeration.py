from __future__ import annotations

import json
from pathlib import Path
from random import Random
from types import SimpleNamespace
from typing import Any

from grid_data_factory.contingencies.ontology import ONTOLOGY_CLASSES
from grid_data_factory.sources.registry import bus_count_for


def case_component_pool(case_id: str, repo_root: Path | None = None) -> dict[str, list[str]]:
    lower = case_id.lower()
    buses = bus_count_for(repo_root, case_id) if repo_root is not None else None
    if buses and buses > 0:
        branches = max(1, int(round(buses * 1.5)))
        gens = max(1, int(round(buses * 0.15)))
    elif "118" in lower:
        buses, branches, gens = 118, 186, 54
    elif "57" in lower:
        buses, branches, gens = 57, 80, 7
    elif "14" in lower:
        buses, branches, gens = 14, 20, 5
    else:
        buses, branches, gens = 80, 120, 20

    return {
        "branch": [f"branch_{i:06d}" for i in range(1, branches + 1)],
        "generator": [f"gen_{i:06d}" for i in range(1, gens + 1)],
        "bus": [f"bus_{i:06d}" for i in range(1, buses + 1)],
    }


def with_scores(base: dict[str, Any], order_k: int, contingency_class: str, base_severity: float, base_credibility: float) -> dict[str, Any]:
    out = dict(base)
    out["contingency_order"] = order_k
    out["contingency_class"] = contingency_class

    severity_boost = 0.08 * order_k
    if contingency_class in {"electrically_interacting", "common_corridor", "sequential_n1n1", "sequential_cascade", "cut_set"}:
        severity_boost += 0.10

    out["contingency_severity_score"] = min(1.0, float(base_severity) + severity_boost)
    out["security_boundary_score"] = min(1.0, float(out.get("security_boundary_score", 0.0)) + 0.06 * order_k)
    out["active_constraint_score"] = min(1.0, float(out.get("active_constraint_score", 0.0)) + 0.05 * order_k)
    out["physical_credibility_score"] = max(0.0, min(1.0, base_credibility))
    out["estimated_compute_cost"] = float(out.get("estimated_compute_cost", 1.0)) * (1.0 + 0.4 * order_k)
    return out


def pick_distinct(rng: Random, pool: list[str], count: int) -> list[str]:
    if count <= 0:
        return []
    if count >= len(pool):
        return list(pool)
    return rng.sample(pool, count)


def unique_components(components: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, str]] = []
    for comp in components:
        key = (str(comp.get("type")), str(comp.get("id")))
        if key in seen:
            continue
        seen.add(key)
        out.append({"type": key[0], "id": key[1]})
    return out


def build_kplus_components(pool: dict[str, list[str]], k: int, rng: Random) -> list[dict[str, str]]:
    # Branch-led interacting set of k distinct components; terminates even when the
    # generator pool is small by drawing without replacement from the combined pool.
    combined: list[dict[str, str]] = [{"type": "branch", "id": b} for b in pool["branch"]]
    combined += [{"type": "generator", "id": g} for g in pool["generator"]]
    if k >= len(combined):
        return unique_components(combined)
    chosen = rng.sample(combined, k)
    if pool["branch"] and not any(c["type"] == "branch" for c in chosen):
        chosen[0] = {"type": "branch", "id": rng.choice(pool["branch"])}
    return unique_components(chosen)


def expand_one(base: dict[str, Any], rng: Random, sampling: Any, repo_root: Path | None = None) -> list[dict[str, Any]]:
    case_id = str(base.get("case_id"))
    cid = str(base.get("candidate_id"))
    pool = case_component_pool(case_id, repo_root)
    base_sev = float(base.get("contingency_severity_score", 0.0))
    base_cred = float(base.get("physical_credibility_score", 0.9))

    out: list[dict[str, Any]] = []
    event_index = 0

    for branch in pick_distinct(rng, pool["branch"], sampling.n1_per_operating_point):
        event_index += 1
        row = with_scores(base, order_k=1, contingency_class="independent_random", base_severity=base_sev, base_credibility=base_cred)
        row["candidate_id"] = f"{cid}::ctg::{event_index:03d}"
        row["contingency"] = {
            "contingency_id": f"ctg_{event_index:06d}",
            "order": 1,
            "event_type": "simultaneous",
            "components": [{"type": "branch", "id": branch}],
            "ontology_labels": ["independent_random"],
            "credibility_source": "electrically_inferred",
        }
        out.append(row)

    branch_pairs = pick_distinct(rng, pool["branch"], 2 * sampling.n2_random_per_operating_point)
    for i in range(0, len(branch_pairs), 2):
        if i + 1 >= len(branch_pairs):
            break
        event_index += 1
        row = with_scores(base, order_k=2, contingency_class="common_corridor", base_severity=base_sev, base_credibility=max(0.0, base_cred - 0.05))
        row["candidate_id"] = f"{cid}::ctg::{event_index:03d}"
        row["contingency"] = {
            "contingency_id": f"ctg_{event_index:06d}",
            "order": 2,
            "event_type": "simultaneous",
            "components": [{"type": "branch", "id": branch_pairs[i]}, {"type": "branch", "id": branch_pairs[i + 1]}],
            "ontology_labels": ["common_corridor", "independent_random"],
            "credibility_source": "topologically_inferred",
        }
        out.append(row)

    for _ in range(sampling.n2_interacting_per_operating_point):
        event_index += 1
        b = rng.choice(pool["branch"])
        g = rng.choice(pool["generator"])
        row = with_scores(base, order_k=2, contingency_class="electrically_interacting", base_severity=base_sev, base_credibility=max(0.0, base_cred - 0.02))
        row["candidate_id"] = f"{cid}::ctg::{event_index:03d}"
        row["contingency"] = {
            "contingency_id": f"ctg_{event_index:06d}",
            "order": 2,
            "event_type": "simultaneous",
            "components": [{"type": "branch", "id": b}, {"type": "generator", "id": g}],
            "ontology_labels": ["electrically_interacting", "generator_export_path"],
            "credibility_source": "electrically_inferred",
        }
        out.append(row)

    for _ in range(sampling.n1n1_per_operating_point):
        event_index += 1
        first = rng.choice(pool["branch"])
        second = rng.choice(pool["generator"])
        row = with_scores(base, order_k=2, contingency_class="sequential_n1n1", base_severity=base_sev, base_credibility=base_cred)
        row["candidate_id"] = f"{cid}::ctg::{event_index:03d}"
        row["contingency"] = {
            "contingency_id": f"ctg_{event_index:06d}",
            "order": 2,
            "event_type": "sequential_n1n1",
            "first_outage": {"type": "branch", "id": first},
            "second_outage": {"type": "generator", "id": second},
            "allowed_corrective_action": "redispatch_and_voltage_control",
            "ontology_labels": ["sequential_n1n1"],
            "credibility_source": "synthetic_scenario_assumption",
        }
        out.append(row)

    max_k = max(2, int(sampling.max_k))
    nk_count = max(0, int(sampling.nk_per_operating_point))
    for k in range(3, max_k + 1):
        for _ in range(nk_count):
            event_index += 1
            components = build_kplus_components(pool, k, rng)
            # As K grows, degrade credibility mildly unless interactions are clear.
            cred = max(0.0, base_cred - 0.03 * (k - 2))
            row = with_scores(
                base,
                order_k=k,
                contingency_class="cut_set",
                base_severity=base_sev,
                base_credibility=cred,
            )
            row["candidate_id"] = f"{cid}::ctg::{event_index:03d}"
            row["contingency"] = {
                "contingency_id": f"ctg_{event_index:06d}",
                "order": k,
                "event_type": "simultaneous",
                "components": components,
                "ontology_labels": ["cut_set", "electrically_interacting"],
                "credibility_source": "synthetic_scenario_assumption",
            }
            out.append(row)

    # Sequential cascades of ordered single outages (depth 3..seq_max_len); depth-2
    # sequences are already covered by the sequential_n1n1 stream above. Off by
    # default (count 0) so existing enumeration output is unchanged.
    seq_count = max(0, int(getattr(sampling, "sequential_cascade_per_operating_point", 0)))
    seq_max_len = int(getattr(sampling, "sequential_max_len", max_k))
    seq_max_len = max(2, min(seq_max_len, max_k))
    for length in range(3, seq_max_len + 1):
        for _ in range(seq_count):
            components = build_kplus_components(pool, length, rng)
            if len(components) < 3:
                continue
            event_index += 1
            # The drawn order defines the temporal sequence; a corrective-action
            # window sits between stages (never after the final trip).
            stages: list[dict[str, Any]] = []
            for stage_idx, comp in enumerate(components, start=1):
                stage: dict[str, Any] = {"index": stage_idx, "type": comp["type"], "id": comp["id"]}
                if stage_idx < len(components):
                    stage["corrective_action"] = "redispatch_and_voltage_control"
                stages.append(stage)
            depth = len(stages)
            # Credibility decays with depth but stays above a same-size cut_set
            # because operators can respond between stages.
            cred = max(0.0, base_cred - 0.025 * (depth - 2))
            row = with_scores(base, order_k=depth, contingency_class="sequential_cascade", base_severity=base_sev, base_credibility=cred)
            row["candidate_id"] = f"{cid}::ctg::{event_index:03d}"
            row["contingency"] = {
                "contingency_id": f"ctg_{event_index:06d}",
                "order": depth,
                "event_type": "sequential_cascade",
                "stages": stages,
                # Endogenous consumers apply the first seed_stage_count stages as the
                # initiating event and let physics drive the rest; exogenous consumers
                # apply every stage in order.
                "seed_stage_count": 1,
                "allowed_corrective_action": "redispatch_and_voltage_control",
                "ontology_labels": ["cascade_induced"],
                "credibility_source": "synthetic_scenario_assumption",
            }
            out.append(row)

    for row in out:
        labels = row.get("contingency", {}).get("ontology_labels", [])
        unknown = [x for x in labels if x not in ONTOLOGY_CLASSES]
        if unknown:
            raise ValueError(f"Unknown ontology labels: {unknown}")

    return out


def split_rows(rows: list[Any], n: int) -> list[list[Any]]:
    # Contiguous chunks preserving input order.
    k, m = divmod(len(rows), n)
    chunks: list[list[Any]] = []
    start = 0
    for i in range(n):
        size = k + (1 if i < m else 0)
        chunks.append(rows[start:start + size])
        start += size
    return [c for c in chunks if c]


def expand_chunk(chunk_index: int, rows: list[dict[str, Any]], sampling: dict[str, Any], repo_root_str: str, shard_path_str: str) -> tuple[int, int]:
    # Per-row deterministic seeding makes output independent of worker count and chunk boundaries.
    repo_root = Path(repo_root_str)
    args = SimpleNamespace(**sampling)
    seed = sampling["seed"]
    count = 0
    with open(shard_path_str, "w", encoding="utf-8") as fh:
        for row in rows:
            rng = Random(f"{seed}::{row.get('candidate_id')}")
            for out in expand_one(row, rng, args, repo_root):
                fh.write(json.dumps(out, ensure_ascii=True) + "\n")
                count += 1
    return chunk_index, count
