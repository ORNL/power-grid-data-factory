#!/usr/bin/env python3.11
"""Preview deterministic network-expansion variants for a case.

Parses a case (MATPOWER .m or canonical JSON) and prints the expansion plans
that :func:`generate_expansion_variants` would produce, with a summary of the
new buses/generators/transformers each plan inserts. Read-only; nothing is
solved or written unless --out is given.

Examples:
  python3.11 scripts/preview_expansion.py --case pglib_opf_case14_ieee
  python3.11 scripts/preview_expansion.py --case-file data/canonical/foo.json -n 8 --seed 3
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from grid_data_factory.topology.expansion import (  # noqa: E402
    apply_expansion,
    generate_expansion_variants,
    is_connected_after_expansion,
)


def _load_case(case: str | None, case_file: str | None) -> dict:
    if case_file:
        path = Path(case_file)
        if path.suffix == ".json":
            return json.loads(path.read_text(encoding="utf-8"))
        from grid_data_factory.parsers.matpower import parse_matpower_case

        return parse_matpower_case(path, path.stem)
    if case:
        from grid_data_factory.parsers.matpower import parse_matpower_case
        from grid_data_factory.sources.registry import resolve_case_file

        path = resolve_case_file(REPO_ROOT, case)
        return parse_matpower_case(path, case)
    raise SystemExit("provide --case <id> or --case-file <path>")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--case", help="case id resolvable via the source registry")
    ap.add_argument("--case-file", help="path to a .m or canonical .json case")
    ap.add_argument("-n", "--n-variants", type=int, default=6)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--max-steps", type=int, default=1, help="Max units per plan; >1 emits cascade plans.")
    ap.add_argument("--size-multiplier", type=float, default=1.0, help="Scale factor on each new unit's capacity.")
    ap.add_argument("--max-additions-fraction", type=float, default=0.0, help="Relative cascade depth = round(fraction * bus_count); bigger grids get bigger expansions.")
    ap.add_argument("--out", help="optional path to write the plans as JSON")
    args = ap.parse_args()

    case = _load_case(args.case, args.case_file)
    case_id = str(case.get("case_id", args.case or "case"))
    plans = generate_expansion_variants(
        case_id, case, n_variants=args.n_variants, seed=args.seed,
        max_steps=args.max_steps, size_multiplier=args.size_multiplier,
        max_additions_fraction=args.max_additions_fraction,
    )

    print(f"case: {case_id}")
    print(f"  buses={len(case.get('buses', []))} generators={len(case.get('generators', []))} "
          f"branches={len(case.get('branches', []))} loads={len(case.get('loads', []))}")
    print(f"expansion plans (seed={args.seed}, requested={args.n_variants}, produced={len(plans)}):")
    for plan in plans:
        cls = plan["expansion_class"]
        if cls == "baseline":
            print(f"  - {plan['expansion_id']}: baseline (no expansion)")
            continue
        out = apply_expansion(case, plan)
        connected = "connected" if is_connected_after_expansion(case, plan) else "DISCONNECTED"
        cascade = f" [{'>'.join(c.split('_')[0] for c in plan['cascade_classes'])}]" if plan.get("cascade_classes") else ""
        print(
            f"  - {plan['expansion_id']}: {cls}{cascade} | "
            f"{plan.get('step_count', 1)} step(s) | "
            f"+{plan.get('added_bus_count', 0)} bus, "
            f"+{plan.get('added_generator_count', 0)} gen, "
            f"+{plan.get('added_branch_count', 0)} branch, "
            f"+{plan.get('added_load_count', 0)} load, "
            f"moved {plan.get('moved_branch_count', 0)} | "
            f"buses {len(case.get('buses', []))}->{len(out['buses'])} | {connected}"
        )

    if args.out:
        Path(args.out).write_text(json.dumps(plans, indent=2), encoding="utf-8")
        print(f"wrote {len(plans)} plans -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
