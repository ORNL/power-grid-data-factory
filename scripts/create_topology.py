#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from grid_data_factory.parsers.matpower import parse_base_mva as _parse_base_mva
    from grid_data_factory.parsers.matpower import parse_matrix as _parse_matrix
    from grid_data_factory.storage.naming import format_topology_id
except ModuleNotFoundError:
    _repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(_repo_root / "src"))
    from grid_data_factory.parsers.matpower import parse_base_mva as _parse_base_mva
    from grid_data_factory.parsers.matpower import parse_matrix as _parse_matrix
    from grid_data_factory.storage.naming import format_topology_id


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_matpower_case(case_file: Path) -> dict:
    text = case_file.read_text(encoding="utf-8", errors="ignore")
    base_mva = _parse_base_mva(text)

    bus_rows = _parse_matrix(text, "bus")
    gen_rows = _parse_matrix(text, "gen")
    branch_rows = _parse_matrix(text, "branch")
    gencost_rows = _parse_matrix(text, "gencost")

    if not bus_rows or not branch_rows:
        raise ValueError("Case file is missing required bus/branch sections")

    buses: list[dict] = []
    loads: list[dict] = []
    for row in bus_rows:
        bus_id = str(int(row[0]))
        buses.append(
            {
                "bus_id": bus_id,
                "type": int(row[1]),
                "area": int(row[6]),
                "base_kv": float(row[9]),
                "zone": int(row[10]),
                "vmin": float(row[12]),
                "vmax": float(row[11]),
            }
        )

        pd = float(row[2])
        qd = float(row[3])
        if abs(pd) > 0.0 or abs(qd) > 0.0:
            loads.append(
                {
                    "load_id": f"load_{len(loads) + 1:06d}",
                    "bus_id": bus_id,
                    "nominal_pd": pd,
                    "nominal_qd": qd,
                }
            )

    gen_cost_by_index: dict[int, list[float]] = {}
    for idx, row in enumerate(gencost_rows):
        if len(row) < 5:
            continue
        n_coeff = int(row[3])
        gen_cost_by_index[idx] = [float(v) for v in row[4 : 4 + n_coeff]]

    generators: list[dict] = []
    for idx, row in enumerate(gen_rows):
        generators.append(
            {
                "gen_id": f"gen_{idx + 1:06d}",
                "bus_id": str(int(row[0])),
                "status": int(row[7]),
                "pmin": float(row[9]),
                "pmax": float(row[8]),
                "qmin": float(row[4]),
                "qmax": float(row[3]),
                "vg_setpoint": float(row[5]),
                "cost": gen_cost_by_index.get(idx),
            }
        )

    branches: list[dict] = []
    for idx, row in enumerate(branch_rows):
        branches.append(
            {
                "branch_id": f"branch_{idx + 1:06d}",
                "from": str(int(row[0])),
                "to": str(int(row[1])),
                "status": int(row[10]),
                "r": float(row[2]),
                "x": float(row[3]),
                "b": float(row[4]),
                "rate_a": float(row[5]),
                "rate_b": float(row[6]),
                "rate_c": float(row[7]),
                "ratio": float(row[8]),
                "angle": float(row[9]),
                "angmin": float(row[11]),
                "angmax": float(row[12]),
            }
        )

    return {
        "base_mva": base_mva,
        "buses": buses,
        "loads": loads,
        "generators": generators,
        "branches": branches,
    }


def _next_topology_index(case_dir: Path) -> int:
    if not case_dir.exists():
        return 0

    max_idx = -1
    for p in case_dir.glob("topology_*.json"):
        parts = p.stem.split("_", 2)
        if len(parts) >= 3 and parts[1].isdigit():
            max_idx = max(max_idx, int(parts[1]))
    return max_idx + 1


def _resolve_case_file(repo_root: Path, source: str, case_id: str, case_file: str | None) -> Path:
    if case_file:
        path = Path(case_file)
        if not path.is_absolute():
            path = (repo_root / path).resolve()
        return path

    if source in {"pglib", "pglib_opf"}:
        return (repo_root / "external" / "pglib-opf" / f"{case_id}.m").resolve()

    raise ValueError(f"No default case-file mapping for source: {source}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Create a topology artifact from a source case file.")
    p.add_argument("--source", default="pglib")
    p.add_argument("--case-id", required=True)
    p.add_argument("--case-file", default=None)
    p.add_argument("--description", default="baseline")
    p.add_argument("--topology-index", type=int, default=None)
    p.add_argument("--registry-root", default="data/topology_registry")
    p.add_argument("--out", default=None, help="Optional explicit output path for topology JSON")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]

    source_case_file = _resolve_case_file(repo_root, args.source, args.case_id, args.case_file)
    if not source_case_file.exists():
        raise SystemExit(f"case file not found: {source_case_file}")

    parsed = _parse_matpower_case(source_case_file)

    registry_root = (repo_root / args.registry_root).resolve()
    case_dir = registry_root / args.case_id
    case_dir.mkdir(parents=True, exist_ok=True)

    topo_index = args.topology_index if args.topology_index is not None else _next_topology_index(case_dir)
    topology_id = format_topology_id(topo_index, args.description)

    if args.out:
        out_path = Path(args.out)
        if not out_path.is_absolute():
            out_path = (repo_root / out_path).resolve()
    else:
        out_path = case_dir / f"{topology_id}.json"

    topology = {
        "topology_id": topology_id,
        "case_id": args.case_id,
        "source": args.source,
        "description": args.description,
        "created_at": _now(),
        "source_case_file": str(source_case_file),
        "base_mva": parsed["base_mva"],
        "buses": parsed["buses"],
        "branches": parsed["branches"],
        "generators": parsed["generators"],
        "loads": parsed["loads"],
        "counts": {
            "buses": len(parsed["buses"]),
            "branches": len(parsed["branches"]),
            "generators": len(parsed["generators"]),
            "loads": len(parsed["loads"]),
        },
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(topology, indent=2), encoding="utf-8")

    record = {
        "topology_id": topology_id,
        "case_id": args.case_id,
        "source": args.source,
        "description": args.description,
        "created_at": topology["created_at"],
        "path": str(out_path),
        "source_case_file": str(source_case_file),
        "counts": topology["counts"],
    }
    with (registry_root / "topology_registry.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=True) + "\n")

    print(json.dumps({"ok": True, **record}, indent=2))


if __name__ == "__main__":
    main()
