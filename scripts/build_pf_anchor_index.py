#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from grid_data_factory.pf.anchors import build_anchor_index
except ModuleNotFoundError:
    _repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(_repo_root / "src"))
    from grid_data_factory.pf.anchors import build_anchor_index


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a compact PF anchor index from successful AC-OPF samples.")
    parser.add_argument("inputs", nargs="+", help="JSONL files or glob patterns")
    parser.add_argument("--output", required=True, help="Output Parquet path")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    paths: list[Path] = []
    for pattern in args.inputs:
        candidate = Path(pattern)
        if candidate.is_file():
            paths.append(candidate)
        else:
            paths.extend(repo_root.glob(pattern))
    if not paths:
        raise SystemExit("No AC-OPF sample files matched")
    output = Path(args.output)
    output = output if output.is_absolute() else repo_root / output
    print(json.dumps(build_anchor_index(paths, output), indent=2))


if __name__ == "__main__":
    main()