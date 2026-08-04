"""Genuine independent-topology generation.

Produces distinct base network configurations (N-0 topologies) for a case by
persistently switching out branches (planned line switching / maintenance
configurations) while guaranteeing the network does not gain new islands.

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
            }
        )
    return bus_ids, branches


def _stable_seed(case_id: str, seed: int, index: int) -> int:
    digest = hashlib.sha256(f"{case_id}|{seed}|{index}".encode("utf-8")).hexdigest()
    return int(digest[:16], 16)


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


def _k_for_index(index: int, max_switched: int) -> int:
    if max_switched <= 1:
        return 1
    return 1 + ((index - 1) % max_switched)


def _make_variant(index: int, topology_class: str, switched_ids: list[str], bus_count: int, branch_count: int) -> dict[str, Any]:
    return {
        "topology_id": format_topology_id(index, topology_class),
        "topology_index": index,
        "topology_class": topology_class,
        "switched_off_branches": switched_ids,
        "switched_branch_count": len(switched_ids),
        "in_service_branch_count": branch_count - len(switched_ids),
        "bus_count": bus_count,
    }


def generate_topology_variants(
    case_id: str,
    bus_ids: list[str],
    branches: list[dict[str, str]],
    n_variants: int = 6,
    seed: int = 0,
    max_switched: int = 3,
) -> list[dict[str, Any]]:
    """Generate up to ``n_variants`` distinct, connectivity-preserving topologies.

    The first variant is always the unmodified baseline. Remaining variants open
    non-bridge branches such that the number of connected components is unchanged.
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

    variants = [baseline]
    if not switchable:
        return variants

    seen: set[frozenset[int]] = {frozenset()}
    index = 1
    attempts = 0
    max_attempts = max(50, n_variants * 50)
    while len(variants) < n_variants and attempts < max_attempts:
        attempts += 1
        rng = Random(_stable_seed(case_id, seed, index))
        k = min(len(switchable), _k_for_index(index, max_switched))
        pick = sorted(rng.sample(switchable, k))
        key = frozenset(pick)
        index += 1
        if key in seen:
            continue
        if _num_components(n, edges, set(pick)) != base_components:
            continue
        seen.add(key)
        switched_ids = [branches[i]["branch_id"] for i in pick]
        variants.append(_make_variant(len(variants), _class_for_k(k), switched_ids, n, branch_count))

    return variants


def apply_topology(case_data: dict[str, Any], switched_off_branches: list[str] | None) -> dict[str, Any]:
    """Return a case with the given branches persistently opened (out of service)."""
    if not switched_off_branches:
        return case_data
    remove = {str(b) for b in switched_off_branches}
    out = dict(case_data)
    out["branches"] = [b for b in case_data.get("branches", []) if str(b.get("branch_id")) not in remove]
    return out
