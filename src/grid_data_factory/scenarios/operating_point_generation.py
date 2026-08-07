from __future__ import annotations

import json
import math
import re
from pathlib import Path
from random import Random
from typing import Any

from grid_data_factory.sources.registry import resolve_case_file
from grid_data_factory.topology.generation import generate_topology_variants, read_network_skeleton
from grid_data_factory.storage import paths


def fallback_regimes_from_text(text: str) -> list[str]:
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


def fallback_local_noise(text: str, default: float = 0.02) -> float:
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


def sample_unit_vector_legacy(dim: int, idx: int, total: int, sampler: str, rng: Random) -> list[float]:
    """Original sampler. Kept for reference/reproducibility of earlier runs.

    WARNING (computational complexity): the ``latin_hypercube`` branch below
    rebuilds and shuffles a fresh permutation of size ``total`` on EVERY call.
    Because ``sample_unit_vector`` is invoked once per operating point and
    ``total`` equals ``per_case``, the cost to generate a full batch is
    O(total) work per point x total points = O(total^2). At ``per_case`` in the
    hundreds this is negligible, but at ``per_case = 210_000`` it explodes to
    ~4.4e10 Python-level operations per case and dominates the whole bootstrap
    (hours per round). Prefer :func:`sample_unit_vector` (O(1) in ``total``).
    """
    if sampler == "sobol":
        primes = [2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53, 59, 61]
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

    # O(total) per call because of the allocation + shuffle -> O(total^2) per batch.
    perm = list(range(total))
    rng.shuffle(perm)
    return [_lhs_value((perm[(idx + d) % total]), total, rng) for d in range(dim)]


def _mix64(x: int) -> int:
    """SplitMix64 finalizer: a fast, deterministic, process-independent hash.

    Runs in O(1) (a fixed number of 64-bit integer ops) and does NOT depend on
    ``PYTHONHASHSEED``, so the permutation it drives is reproducible across
    processes/nodes -- essential for a distributed campaign.
    """
    x &= 0xFFFFFFFFFFFFFFFF
    x = ((x ^ (x >> 30)) * 0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
    x = ((x ^ (x >> 27)) * 0x94D049BB133111EB) & 0xFFFFFFFFFFFFFFFF
    return x ^ (x >> 31)


def _text_key(s: str) -> int:
    """FNV-1a hash of a string -> stable 64-bit int (unlike salted ``hash``)."""
    h = 0xCBF29CE484222325
    for ch in s.encode("utf-8"):
        h = ((h ^ ch) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return h


def _coprime_multiplier(seed: int, total: int) -> int:
    """Return a multiplier ``a`` coprime with ``total``.

    With ``gcd(a, total) == 1`` the affine map ``i -> (a*i + b) mod total`` is a
    bijection (an "affine cipher") over ``[0, total)`` -- i.e. a full permutation
    -- which is exactly what a Latin-hypercube design needs (one sample per
    stratum, no collisions).

    Complexity: ``total`` has O(log total) distinct prime factors, so a coprime
    is found within O(log total) probes in the worst case and O(1) on average;
    each gcd probe is O(log total). This runs a constant number of times per
    dimension and is independent of the batch size.
    """
    if total <= 1:
        return 1
    a = _mix64(seed) % total
    if a == 0:
        a = 1
    while math.gcd(a, total) != 1:
        a = (a + 1) % total
        if a == 0:
            a = 1
    return a


def sample_unit_vector(dim: int, idx: int, total: int, sampler: str, rng: Random) -> list[float]:
    """Draw a ``dim``-length unit-cube vector for operating point ``idx``.

    Computational complexity: O(dim) per call and, crucially, O(1) in ``total``.
    Generating a full batch is therefore O(dim * total) -- linear in the number
    of operating points -- versus the O(total^2) of
    :func:`sample_unit_vector_legacy`. This is what makes large ``per_case``
    values (e.g. 210_000) feasible: the whole bootstrap stays proportional to
    the amount of data produced instead of its square.

    The ``latin_hypercube`` design is realised without ever materialising a
    size-``total`` permutation array. For each dimension ``d`` we build an
    affine permutation ``stratum(idx) = (a_d * idx + b_d) mod total`` whose
    coefficients are derived deterministically from ``(d, total)`` via
    :func:`_mix64` (NOT from the shared ``rng`` stream, so the permutation is
    stable across the whole batch and reproducible across processes). Because
    ``a_d`` is coprime with ``total``, ``idx`` sweeping ``[0, total)`` hits every
    stratum exactly once -- the defining LHS property. ``rng`` only adds
    intra-stratum jitter, matching the legacy behaviour.
    """
    if sampler == "sobol":
        primes = [2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53, 59, 61]
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

    # latin_hypercube (default): O(dim) per call, O(1) in `total` -- no shuffle.
    if total <= 1:
        return [min(0.999, max(0.001, rng.random())) for _ in range(dim)]
    out: list[float] = []
    for d in range(dim):
        a = _coprime_multiplier(_mix64(total * 0x9E3779B1 + d), total)
        b = _mix64(total + d * 0x85EBCA77) % total
        stratum = (a * idx + b) % total
        out.append((stratum + rng.random()) / total)
    return out


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


def choose_regime(regimes: list[str], idx: int, total: int, sampler: str, rng: Random) -> str:
    if sampler == "regime_specific":
        return regimes[idx % len(regimes)]
    if sampler == "time_series":
        season = int((idx / max(total, 1)) * len(regimes))
        return regimes[min(season, len(regimes) - 1)]
    return regimes[rng.randrange(len(regimes))]


def build_candidate(
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
    # Tolerate shorter sample vectors from legacy callers (line-parameter and
    # cost-permutation dims 13-17 default to the stratum midpoint = no bias).
    if len(vec) < 18:
        vec = list(vec) + [0.5] * (18 - len(vec))
    # Deterministic, process-independent perturbation seed for this candidate:
    # folds the case id (so different grids with the same index diverge) with
    # the candidate index. Drives per-branch admittance noise + cost permutation.
    pert_seed = _mix64(_text_key(case_id) ^ _mix64(candidate_index + 1))
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
        # Per-branch i.i.d. transmission-line admittance perturbation: the
        # candidate stores only the deviation magnitude (sigma) per quantity and
        # a shared seed; each branch's actual factor U[1-sigma, 1+sigma) is
        # derived deterministically at solve time (see apply_operating_point).
        "line_resistance_sigma": _lin(0.0, 0.15, vec[13]),
        "line_reactance_sigma": _lin(0.0, 0.15, vec[14]),
        "line_charging_sigma": _lin(0.0, 0.20, vec[15]),
        "perturbation_seed": int(pert_seed),
        # Generator cost-curve permutation: activate on ~half the candidates.
        "cost_permutation": 1.0 if vec[16] > 0.5 else 0.0,
        # Per-bus i.i.d. shunt susceptance (Bs) perturbation: models switchable
        # capacitor-bank / reactor availability. Applied at solve time.
        "bus_shunt_susceptance_sigma": _lin(0.0, 0.25, vec[17]),
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
        "reinforced_branches": list(topology.get("reinforced_branches", [])),
        "reinforced_branch_count": int(topology.get("reinforced_branch_count", 0)),
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
    "reinforced_branches": [],
    "reinforced_branch_count": 0,
}


def prepare_topologies(repo_root: Path, case_id: str, n_variants: int, seed: int, max_switched: int) -> list[dict[str, Any]]:
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
    "line_resistance_sigma": 0.0,
    "line_reactance_sigma": 0.0,
    "line_charging_sigma": 0.0,
    "perturbation_seed": 0,
    "cost_permutation": 0.0,
    "bus_shunt_susceptance_sigma": 0.0,
}


def build_snapshot_candidate(
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
        "reinforced_branches": list(topology.get("reinforced_branches", [])),
        "reinforced_branch_count": int(topology.get("reinforced_branch_count", 0)),
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
