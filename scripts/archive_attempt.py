#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from grid_data_factory.preservation.archive import create_archive, verify_archive


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--attempt-dir", required=True)
    p.add_argument("--format", default="tar.gz")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    archive = create_archive(Path(args.attempt_dir), fmt=args.format)
    print(json.dumps(verify_archive(archive), indent=2))


if __name__ == "__main__":
    main()
