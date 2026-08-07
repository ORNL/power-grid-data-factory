"""Genuine independent-topology generation.

Produces distinct base network configurations for a case via two mechanisms:
persistently switching out branches (planned line switching / maintenance
configurations, guaranteeing no new islands) and adding parallel circuits to
existing corridors (grid upgrades / reinforcement). Together these span both
degraded and reinforced networks, complementing the N-k contingency axis.

Topology variants are deterministic for a given ``(case_id, seed)`` so the same
campaign inputs always reproduce the same topologies.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from random import Random
from typing import Any

try:
    from grid_data_factory.parsers.matpower import parse_matrix as _parse_matrix
    from grid_data_factory.storage.naming import format_topology_id
except ModuleNotFoundError:  # pragma: no cover - exercised only without src on path
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from grid_data_factory.parsers.matpower import parse_matrix as _parse_matrix
    from grid_data_factory.storage.naming import format_topology_id


def read_network_skeleton(case_file: Path) -> tuple[list[str], list[dict[str, str]]]:
    """Return (bus_ids, branches) with branch ids aligned to the solver parser."""
    text = case_file.read_text(encoding="utf-8", errors="ignore")
    bus_ids = [str(int(r[0])) for r in _parse_matrix(text, "bus")]
    branches: list[dict[str, str]] = []
    for idx, r in enumerate(_parse_matrix(text, "branch")):
        branches.append(
            {
                "branch_id": f"branch_{idx + 1:06d}",
                "from": str(int(r[0])),
                "to": str(int(r[1])),
                "x": float(r[3]),
            }
        )
    return bus_ids, branches


def _stable_seed(case_id: str, seed: int, index: int) -> int:
    digest = hashlib.sha256(f"{case_id}|{seed}|{index}".encode("utf-8")).hexdigest()
    return int(digest[:16], 16)


def _parallel_rating_scale(branch_id: str) -> float:
    """Deterministic per-corridor ampacity ratio for an added parallel circuit.

    Different corridors are reinforced with different (standard) conductor
    choices, so the ratio is derived from the branch id alone — no seed
    threading needed and stable across processes. Range [1.5, 2.5): the new
    circuit carries 1.5x-2.5x the existing thermal rating.
    """
    digest = hashlib.sha256(f"parallel|{branch_id}".encode("utf-8")).hexdigest()
    return 1.5 + (int(digest[:8], 16) % 1000) / 1000.0


def _parallel_circuit(src: dict[str, Any]) -> dict[str, Any]:
    """Build a distinct parallel circuit for a reinforced corridor.

    Models a modern, higher-ampacity conductor on the same structure rather than
    an identical clone: the thermal rating is raised by ``_parallel_rating_scale``
    and the series resistance lowered proportionally (a larger conductor cross
    section reduces R), while reactance and charging susceptance — set by tower
    geometry / conductor spacing, not cross section — are kept equal to the
    existing circuit. An ``rate_a`` of >= 1e6 is treated as "unlimited" and left
    untouched.
    """
    dup = dict(src)
    bid = str(src.get("branch_id"))
    dup["branch_id"] = f"{bid}_parallel"
    scale = _parallel_rating_scale(bid)

    r = float(src.get("r", 0.0) or 0.0)
    if r > 0.0:
        dup["r"] = r / scale

    rate = float(src.get("rate_a", 0.0) or 0.0)
    if 0.0 < rate < 1.0e6:
        dup["rate_a"] = rate * scale

    return dup


def _num_components(n: int, edges: list[tuple[int, int]], removed: set[int]) -> int:
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, (a, b) in enumerate(edges):
        if i in removed or a == b:
            continue
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    return len({find(i) for i in range(n)})


def _find_bridges(n: int, adj: list[list[tuple[int, int]]]) -> set[int]:
    """Iterative Tarjan bridge detection (safe for very large networks)."""
    disc = [-1] * n
    low = [0] * n
    visited = [False] * n
    bridges: set[int] = set()
    timer = 0

    for start in range(n):
        if visited[start]:
            continue
        visited[start] = True
        disc[start] = low[start] = timer
        timer += 1
        stack: list[tuple[int, int, Any]] = [(start, -1, iter(adj[start]))]
        while stack:
            u, parent_edge, it = stack[-1]
            descended = False
            for v, edge_index in it:
                if edge_index == parent_edge:
                    continue
                if not visited[v]:
                    visited[v] = True
                    disc[v] = low[v] = timer
                    timer += 1
                    stack.append((v, edge_index, iter(adj[v])))
                    descended = True
                    break
                low[u] = min(low[u], disc[v])
            if not descended:
                stack.pop()
                if stack:
                    pu = stack[-1][0]
                    low[pu] = min(low[pu], low[u])
                    if low[u] > disc[pu]:
                        bridges.add(parent_edge)
    return bridges


def _class_for_k(k: int) -> str:
    if k <= 1:
        return "single_line_switching"
    if k == 2:
        return "double_line_switching"
    return "maintenance_configuration"


def _reinforce_class_for_m(m: int) -> str:
    # All upgrade classes carry the "upgrade" token so any reinforced topology is
    # identifiable by substring in its topology_id / run_id / output path.
    if m <= 1:
        return "single_line_upgrade"
    if m == 2:
        return "double_line_upgrade"
    return "multi_line_upgrade"


def _k_for_index(index: int, max_switched: int) -> int:
    if max_switched <= 1:
        return 1
    return 1 + ((index - 1) % max_switched)


def _weighted_sample_wor(rng: Random, indices: list[int], weights: list[float], m: int) -> list[int]:
    """Weighted sampling without replacement (Efraimidis-Spirakis A-Res).

    Draws ``m`` distinct indices with probability proportional to ``weights``.
    Deterministic for a given ``rng`` state; O(len(indices)) per call.
    """
    if m >= len(indices):
        return list(indices)
    keyed: list[tuple[float, int]] = []
    for idx, w in zip(indices, weights):
        u = rng.random()
        key = u ** (1.0 / max(w, 1e-9))
        keyed.append((key, idx))
    keyed.sort(reverse=True)
    return [idx for _, idx in keyed[:m]]


def _make_variant(
    index: int,
    topology_class: str,
    switched_ids: list[str],
    bus_count: int,
    branch_count: int,
    reinforced_ids: list[str] | None = None,
) -> dict[str, Any]:
    reinforced_ids = reinforced_ids or []
    return {
        "topology_id": format_topology_id(index, topology_class),
        "topology_index": index,
        "topology_class": topology_class,
        "switched_off_branches": switched_ids,
        "switched_branch_count": len(switched_ids),
        "reinforced_branches": reinforced_ids,
        "reinforced_branch_count": len(reinforced_ids),
        "in_service_branch_count": branch_count - len(switched_ids) + len(reinforced_ids),
        "bus_count": bus_count,
    }


def generate_topology_variants(
    case_id: str,
    bus_ids: list[str],
    branches: list[dict[str, str]],
    n_variants: int = 6,
    seed: int = 0,
    max_switched: int = 3,
    max_reinforced: int = 2,
) -> list[dict[str, Any]]:
    """Generate up to ``n_variants`` distinct topologies around the baseline.

    The first variant is always the unmodified baseline. Remaining variants
    interleave two mechanisms:

    * **line switching** (N-0 degradation): open non-bridge branches while
      preserving the number of connected components.
    * **grid upgrade** (reinforcement): add a parallel circuit to one or more
      existing corridors. This complements N-k contingencies by covering
      *reinforced* networks, not only degraded ones. Adding edges can never
      disconnect the network, so upgrades apply even to radial cases where no
      branch is switchable. Reinforcement targets are biased toward the
      corridors where an upgrade actually changes the dispatch -- bridges
      (single lines whose loss islands the grid) and high-reactance corridors --
      while retaining a random-exploration floor for diversity.
    """
    n = len(bus_ids)
    branch_count = len(branches)
    baseline = _make_variant(0, "baseline", [], n, branch_count)
    if n == 0 or branch_count == 0 or n_variants <= 1:
        return [baseline]

    index_of = {bus: i for i, bus in enumerate(bus_ids)}
    edges: list[tuple[int, int]] = []
    adj: list[list[tuple[int, int]]] = [[] for _ in range(n)]
    for edge_index, br in enumerate(branches):
        a = index_of.get(br["from"])
        b = index_of.get(br["to"])
        if a is None or b is None:
            edges.append((0, 0))
            continue
        edges.append((a, b))
        adj[a].append((b, edge_index))
        adj[b].append((a, edge_index))

    base_components = _num_components(n, edges, set())
    bridges = _find_bridges(n, adj)
    switchable = [i for i in range(branch_count) if i not in bridges and edges[i][0] != edges[i][1]]
    reinforceable = [i for i in range(branch_count) if edges[i][0] != edges[i][1]]

    # Reinforcement priority: reinforcing a bridge adds a redundant path (the
    # single highest-value upgrade), and reinforcing a high-reactance corridor
    # yields the largest impedance reduction / transfer gain. A floor of 1.0
    # keeps every branch reachable for exploration.
    x_vals = [float(br.get("x", 0.0) or 0.0) for br in branches]
    x_max = max((x_vals[i] for i in reinforceable), default=0.0)
    reinforce_weights = {
        i: 1.0 + (2.0 if i in bridges else 0.0) + (1.5 * x_vals[i] / x_max if x_max > 0.0 else 0.0)
        for i in reinforceable
    }

    variants = [baseline]
    if not switchable and not reinforceable:
        return variants

    seen_switch: set[frozenset[int]] = {frozenset()}
    seen_reinforce: set[frozenset[int]] = set()
    index = 1
    attempts = 0
    max_attempts = max(50, n_variants * 50)
    while len(variants) < n_variants and attempts < max_attempts:
        attempts += 1
        rng = Random(_stable_seed(case_id, seed, index))
        # Reserve ~1/3 of the non-baseline budget for grid upgrades; fall back to
        # upgrades when nothing is switchable (e.g. purely radial networks).
        make_upgrade = reinforceable and (index % 3 == 0 or not switchable)
        index += 1
        if make_upgrade:
            m = min(len(reinforceable), 1 + ((index - 2) % max(1, max_reinforced)))
            pick = frozenset(_weighted_sample_wor(rng, reinforceable, [reinforce_weights[i] for i in reinforceable], m))
            if pick in seen_reinforce:
                continue
            seen_reinforce.add(pick)
            reinforced_ids = [branches[i]["branch_id"] for i in sorted(pick)]
            variants.append(_make_variant(len(variants), _reinforce_class_for_m(m), [], n, branch_count, reinforced_ids=reinforced_ids))
            continue
        if not switchable:
            continue
        k = min(len(switchable), _k_for_index(index - 1, max_switched))
        pick = frozenset(rng.sample(switchable, k))
        if pick in seen_switch:
            continue
        if _num_components(n, edges, set(pick)) != base_components:
            continue
        seen_switch.add(pick)
        switched_ids = [branches[i]["branch_id"] for i in sorted(pick)]
        variants.append(_make_variant(len(variants), _class_for_k(k), switched_ids, n, branch_count))

    return variants


def apply_topology(
    case_data: dict[str, Any],
    switched_off_branches: list[str] | None,
    reinforced_branches: list[str] | None = None,
) -> dict[str, Any]:
    """Return a case with branches opened and/or reinforced with parallel circuits.

    ``switched_off_branches`` are persistently opened (removed). Each id in
    ``reinforced_branches`` adds a parallel circuit to the referenced corridor.
    The new circuit is modelled as a *distinct, modern conductor* on the same
    right-of-way (not an identical clone): a deterministic higher thermal rating
    with a proportionally lower series resistance, while reactance and charging
    (geometry-dominated) are kept equal to the existing circuit. The AC network
    equations then combine the two circuits, yielding a lower corridor impedance
    and higher capacity than either circuit alone.
    """
    if not switched_off_branches and not reinforced_branches:
        return case_data

    original = case_data.get("branches", [])
    branches = list(original)

    if switched_off_branches:
        remove = {str(b) for b in switched_off_branches}
        branches = [b for b in branches if str(b.get("branch_id")) not in remove]

    if reinforced_branches:
        by_id = {str(b.get("branch_id")): b for b in original}
        for bid in reinforced_branches:
            src = by_id.get(str(bid))
            if src is None:
                continue
            branches.append(_parallel_circuit(src))

    out = dict(case_data)
    out["branches"] = branches
    return out
