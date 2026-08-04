#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from grid_data_factory.storage.naming import format_topology_id
    from grid_data_factory.topology.artifacts import (
        build_topology_artifact,
        next_topology_index,
        parse_topology_case,
        resolve_source_case_file,
    )
except ModuleNotFoundError:
    _repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(_repo_root / "src"))
    from grid_data_factory.storage.naming import format_topology_id
    from grid_data_factory.topology.artifacts import (
        build_topology_artifact,
        next_topology_index,
        parse_topology_case,
        resolve_source_case_file,
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Create a topology artifact from a source case file.")
    p.add_argument("--source", default="pglib")
    p.add_argument("--case-id", required=True)
    p.add_argument("--case-file", default=None)
    p.add_argument("--description", default="baseline")
    p.add_argument("--topology-index", type=int, default=None)
    p.add_argument("--registry-root", default="data/derived/registries/topology")
    p.add_argument("--out", default=None, help="Optional explicit output path for topology JSON")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]

    source_case_file = resolve_source_case_file(repo_root, args.source, args.case_id, args.case_file)
    if not source_case_file.exists():
        raise SystemExit(f"case file not found: {source_case_file}")

    parsed = parse_topology_case(source_case_file)

    registry_root = (repo_root / args.registry_root).resolve()
    case_dir = registry_root / args.case_id
    case_dir.mkdir(parents=True, exist_ok=True)

    topo_index = args.topology_index if args.topology_index is not None else next_topology_index(case_dir)
    topology_id = format_topology_id(topo_index, args.description)

    if args.out:
        out_path = Path(args.out)
        if not out_path.is_absolute():
            out_path = (repo_root / out_path).resolve()
    else:
        out_path = case_dir / f"{topology_id}.json"

    topology = build_topology_artifact(
        topology_id=topology_id,
        case_id=args.case_id,
        source=args.source,
        description=args.description,
        source_case_file=source_case_file,
        parsed=parsed,
    )

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
