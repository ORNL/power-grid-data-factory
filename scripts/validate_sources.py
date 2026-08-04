#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

try:
    from grid_data_factory.sources.validation import validate_sources
except ModuleNotFoundError:
    _repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(_repo_root / "src"))
    from grid_data_factory.sources.validation import validate_sources


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Validate source acquisition completeness and integrity.")
    p.add_argument("--config", default="configs/sources.yaml")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    cfg_path = (repo_root / args.config).resolve()

    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    sources = cfg.get("sources", {})

    result = validate_sources(sources, repo_root, cfg_path)

    print(json.dumps(result, indent=2))
    if not result["ok"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
