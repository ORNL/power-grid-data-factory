#!/usr/bin/env python3
"""Build a static N-1 contingency set (non-islanding branches + generators) for SCOPF runs."""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from grid_data_factory.parsers.matpower import parse_matpower_case  # noqa: E402


def _bridge_branch_ids(case: dict) -> set[str]:
    adjacency: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for branch in case["branches"]:
        if int(branch.get("status", 1)) == 0:
            continue
        adjacency[branch["from"]].append((branch["to"], branch["branch_id"]))
        adjacency[branch["to"]].append((branch["from"], branch["branch_id"]))
    order: dict[str, int] = {}
    low: dict[str, int] = {}
    bridges: set[str] = set()
    for root in adjacency:
        if root in order:
            continue
        order[root] = low[root] = len(order)
        stack = [(root, None, iter(adjacency[root]))]
        while stack:
            node, parent_edge, neighbors = stack[-1]
            advanced = False
            for neighbor, edge in neighbors:
                if edge == parent_edge:
                    continue
                if neighbor in order:
                    low[node] = min(low[node], order[neighbor])
                else:
                    order[neighbor] = low[neighbor] = len(order)
                    stack.append((neighbor, edge, iter(adjacency[neighbor])))
                    advanced = True
                    break
            if not advanced:
                stack.pop()
                if stack:
                    parent = stack[-1][0]
                    low[parent] = min(low[parent], low[node])
                    if low[node] > order[parent]:
                        bridges.add(parent_edge)
    return bridges


def build_n1_contingency_set(case_path: Path, case_id: str, branches: int, generators: int, seed: int) -> dict:
    """Sample a static N-1 set; a negative count selects every eligible component."""
    case = parse_matpower_case(case_path, case_id)
    rng = random.Random(seed)
    bridges = _bridge_branch_ids(case)
    branch_ids = sorted({b["branch_id"] for b in case["branches"] if int(b.get("status", 1)) != 0} - bridges)
    # Skip zero-capacity units, whose outage is trivial.
    gen_ids = sorted(
        g["gen_id"] for g in case["generators"] if int(g.get("status", 1)) != 0 and float(g.get("pmax", 0.0)) > 0.0
    )
    n_branch = len(branch_ids) if branches < 0 else min(branches, len(branch_ids))
    n_gen = len(gen_ids) if generators < 0 else min(generators, len(gen_ids))
    picked = [("branch", i) for i in rng.sample(branch_ids, n_branch)]
    picked += [("generator", i) for i in rng.sample(gen_ids, n_gen)]
    contingencies = [
        {
            "contingency_id": f"n1_{kind}_{component_id}",
            "event_type": "simultaneous",
            "order": 1,
            "components": [{"type": kind, "id": component_id}],
        }
        for kind, component_id in picked
    ]
    b_tag = "all" if branches < 0 else str(branches)
    g_tag = "all" if generators < 0 else str(generators)
    return {
        "contingency_set_id": f"n1_b{b_tag}_g{g_tag}_s{seed}",
        "case_id": case_id,
        "seed": seed,
        "bridge_branches_excluded": len(bridges),
        "eligible_branch_count": len(branch_ids),
        "eligible_generator_count": len(gen_ids),
        "contingencies": contingencies,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-file", required=True)
    parser.add_argument("--case-id", default="")
    parser.add_argument("--branches", type=int, default=15, help="Negative = all non-bridge branches.")
    parser.add_argument("--generators", type=int, default=5, help="Negative = all generators with pmax > 0.")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    case_path = Path(args.case_file)
    document = build_n1_contingency_set(case_path, args.case_id or case_path.stem, args.branches, args.generators, args.seed)
    contingencies = document["contingencies"]
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(document, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(out), "count": len(contingencies), "bridges_excluded": document["bridge_branches_excluded"]}))


if __name__ == "__main__":
    main()
