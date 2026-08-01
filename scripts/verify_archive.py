#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from grid_data_factory.preservation.archive import verify_archive


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--archive", required=True)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    out = verify_archive(Path(args.archive))
    print(json.dumps(out, indent=2))
    if not out["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
