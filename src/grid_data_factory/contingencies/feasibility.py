"""Enumeration-time feasibility prefilters for contingency candidates.

The AC-OPF pipeline is *preventive* (no corrective redispatch / load shed), so a
large share of enumerated contingencies are structurally infeasible before the
solver is ever invoked. Diagnosis of a real map-reduce round showed ~82% of
solves returning ``LOCALLY_INFEASIBLE``, driven by three cheap-to-detect causes:

1. **Islanding** — a switched-off topology plus a branch contingency disconnects
   the network graph, so no power flow exists.
2. **Generation inadequacy** — the available generation capacity (after fleet
   availability / renewable scaling) cannot cover the scaled load, so power
   balance is impossible.
3. **Contingency order too high for small cases** — N-k and cascades on tiny
   networks (case14 / case57) are almost always infeasible.

These filters are applied at enumeration time (``expand_one``), *before* the
candidates reach the screening/audit stage, because the campaign runs with
``audit_fraction=1.0`` which bypasses screening entirely. Filtering here shrinks
the candidate pool at its source, independent of the audit fraction.

Design constraints:
- **Conservative**: only drop candidates that are *provably* infeasible so we
  never discard a solvable operating point. The adequacy margin is a small
  loss allowance, not the (much larger) planning reserve margin.
- **Fail-open**: if the case network cannot be resolved (e.g. toy case ids in
  unit tests, or a missing file) the network-based filters silently no-op; only
  the case-size order cap (which needs just a bus-count hint) still applies.
"""
from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

from grid_data_factory.topology.generation import _num_components, read_network_skeleton

# --- Case-size order caps (bus count tiers) --------------------------------
# Small cases (<= SMALL) allow N-1 simultaneous only; medium cases (<= MEDIUM)
# allow up to N-2 simultaneous / sequential-n1n1; larger cases keep the full
# requested order and rely on the connectivity + adequacy filters instead.
SMALL_CASE_MAX_BUS = 20
MEDIUM_CASE_MAX_BUS = 60

# Required generation headroom over scaled load (covers transmission losses).
# Deliberately small so only truly power-balance-infeasible points are dropped.
ADEQUACY_MARGIN = 0.03


class CaseContext:
    """Cached per-case network facts used by the feasibility filters."""

    __slots__ = (
        "case_id",
        "bus_count",
        "edges",
        "branch_edge_index",
        "base_components",
        "total_pd",
        "gen_list",
        "gen_id_to_pos",
    )

    def __init__(
        self,
        case_id: str,
        bus_count: int,
        edges: list[tuple[int, int]],
        branch_edge_index: dict[str, int],
        base_components: int,
        total_pd: float,
        gen_list: list[float],
        gen_id_to_pos: dict[str, int],
    ) -> None:
        self.case_id = case_id
        self.bus_count = bus_count
        self.edges = edges
        self.branch_edge_index = branch_edge_index
        self.base_components = base_components
        self.total_pd = total_pd
        self.gen_list = gen_list
        self.gen_id_to_pos = gen_id_to_pos


@functools.lru_cache(maxsize=128)
def _context_cached(case_id: str, repo_root_str: str | None) -> CaseContext | None:
    if not repo_root_str:
        return None
    try:
        from grid_data_factory.parsers.matpower import parse_matpower_case
        from grid_data_factory.sources.registry import resolve_case_file

        repo_root = Path(repo_root_str)
        case_file = resolve_case_file(repo_root, case_id)
        if case_file is None or not Path(case_file).exists():
            return None

        bus_ids, branches = read_network_skeleton(Path(case_file))
        index_of = {bus_id: i for i, bus_id in enumerate(bus_ids)}
        edges: list[tuple[int, int]] = []
        branch_edge_index: dict[str, int] = {}
        for i, br in enumerate(branches):
            a = index_of.get(br["from"], 0)
            b = index_of.get(br["to"], 0)
            edges.append((a, b))
            branch_edge_index[br["branch_id"]] = i
        base_components = _num_components(len(bus_ids), edges, set())

        case_data = parse_matpower_case(Path(case_file), case_id)
        total_pd = sum(float(load.get("pd", 0.0)) for load in case_data.get("loads", []))
        gen_list = [float(g.get("pmax", 0.0)) for g in case_data.get("generators", [])]
        gen_id_to_pos = {
            str(g.get("gen_id")): pos for pos, g in enumerate(case_data.get("generators", []))
        }
        return CaseContext(
            case_id=case_id,
            bus_count=len(bus_ids),
            edges=edges,
            branch_edge_index=branch_edge_index,
            base_components=base_components,
            total_pd=total_pd,
            gen_list=gen_list,
            gen_id_to_pos=gen_id_to_pos,
        )
    except Exception:
        # Fail-open: any parsing/resolution problem disables network filters.
        return None


def build_case_context(case_id: str, repo_root: Any) -> CaseContext | None:
    """Return a cached :class:`CaseContext`, or ``None`` when unavailable."""
    repo_root_str = str(repo_root) if repo_root is not None else None
    return _context_cached(case_id, repo_root_str)


def bus_count_hint(case_id: str, repo_root: Any) -> int:
    """Best-effort bus count for order caps, even without a resolvable case file."""
    ctx = build_case_context(case_id, repo_root)
    if ctx is not None:
        return ctx.bus_count
    # Fall back to the enumeration component pool, which knows canonical sizes
    # from the case id string alone.
    try:
        from grid_data_factory.contingencies.enumeration import case_component_pool

        pool = case_component_pool(case_id, repo_root)
        return len(pool.get("bus", []))
    except Exception:
        return 0


def _gen_index(gid: str) -> int:
    try:
        return int(str(gid).split("_")[-1]) - 1
    except (ValueError, IndexError):
        return -1


def contingency_branch_ids(contingency: dict[str, Any]) -> set[str]:
    """Branch ids removed by a contingency (any event type)."""
    ids: set[str] = set()
    event_type = contingency.get("event_type")
    if event_type == "simultaneous":
        for comp in contingency.get("components", []):
            if comp.get("type") == "branch":
                ids.add(str(comp.get("id")))
    elif event_type == "sequential_n1n1":
        for key in ("first_outage", "second_outage"):
            comp = contingency.get(key) or {}
            if comp.get("type") == "branch":
                ids.add(str(comp.get("id")))
    elif event_type == "sequential_cascade":
        for stage in contingency.get("stages", []):
            if stage.get("type") == "branch":
                ids.add(str(stage.get("id")))
    return ids


def contingency_generator_ids(contingency: dict[str, Any]) -> set[str]:
    """Generator ids removed by a contingency (any event type)."""
    ids: set[str] = set()
    event_type = contingency.get("event_type")
    if event_type == "simultaneous":
        for comp in contingency.get("components", []):
            if comp.get("type") == "generator":
                ids.add(str(comp.get("id")))
    elif event_type == "sequential_n1n1":
        for key in ("first_outage", "second_outage"):
            comp = contingency.get(key) or {}
            if comp.get("type") == "generator":
                ids.add(str(comp.get("id")))
    elif event_type == "sequential_cascade":
        for stage in contingency.get("stages", []):
            if stage.get("type") == "generator":
                ids.add(str(stage.get("id")))
    return ids


def creates_island(
    ctx: CaseContext,
    switched_off_branches: Any,
    contingency: dict[str, Any],
    reinforced_branches: Any = None,
) -> bool:
    """True if switched-off + contingency branch removals disconnect the graph.

    Reinforced corridors carry a solve-time parallel circuit (``<id>_parallel``).
    For connectivity they are modelled as a second edge with the same endpoints,
    so losing one circuit of a reinforced corridor keeps it connected while a
    common-mode loss of both circuits can still island it.
    """
    edges = ctx.edges
    branch_edge_index = ctx.branch_edge_index

    reinforced = [
        str(b) for b in (reinforced_branches or []) if str(b) in ctx.branch_edge_index
    ]
    if reinforced:
        edges = list(ctx.edges)
        branch_edge_index = dict(ctx.branch_edge_index)
        for bid in reinforced:
            src_idx = ctx.branch_edge_index[bid]
            branch_edge_index[f"{bid}_parallel"] = len(edges)
            edges.append(ctx.edges[src_idx])

    removed: set[int] = set()
    for bid in switched_off_branches or []:
        idx = branch_edge_index.get(str(bid))
        if idx is not None:
            removed.add(idx)
    for bid in contingency_branch_ids(contingency):
        idx = branch_edge_index.get(bid)
        if idx is not None:
            removed.add(idx)
    if not removed:
        return False
    return _num_components(ctx.bus_count, edges, removed) > ctx.base_components


def _avail_factor(pos: int, fleet_availability: float, renewable_scale: float) -> float:
    # Mirrors apply_operating_point: every 3rd generator is treated as renewable.
    factor = fleet_availability * (renewable_scale if pos % 3 == 0 else 1.0)
    return max(0.1, min(1.2, factor))


def generation_adequate(
    ctx: CaseContext,
    op_params: dict[str, Any],
    contingency: dict[str, Any] | None,
    margin: float = ADEQUACY_MARGIN,
) -> bool:
    """True if available generation can cover the scaled load (power balance).

    Replicates the runtime availability scaling from ``apply_operating_point``.
    Uses the global load scale only (regional scales are symmetric around 1.0),
    plus a small loss margin, so only power-balance-infeasible points are dropped.
    """
    fleet = float(op_params.get("generator_fleet_availability", 1.0))
    renew = float(op_params.get("renewable_scale", 1.0))
    global_load = float(op_params.get("global_load_scale", 1.0))

    total_avail = 0.0
    for pos, pmax in enumerate(ctx.gen_list):
        total_avail += pmax * _avail_factor(pos, fleet, renew)

    if contingency is not None:
        for gid in contingency_generator_ids(contingency):
            pos = ctx.gen_id_to_pos.get(gid)
            if pos is None:
                idx = _gen_index(gid)
                pos = idx if 0 <= idx < len(ctx.gen_list) else None
            if pos is not None:
                total_avail -= ctx.gen_list[pos] * _avail_factor(pos, fleet, renew)

    scaled_load = global_load * ctx.total_pd
    return total_avail >= (1.0 + margin) * scaled_load


def order_allowed(bus_count: int, order: int, event_type: str) -> bool:
    """Case-size-aware cap on contingency order / event type."""
    if bus_count <= 0:
        return True  # unknown size: do not restrict
    if bus_count <= SMALL_CASE_MAX_BUS:
        return order <= 1 and event_type == "simultaneous"
    if bus_count <= MEDIUM_CASE_MAX_BUS:
        return order <= 2 and event_type in ("simultaneous", "sequential_n1n1")
    return True
