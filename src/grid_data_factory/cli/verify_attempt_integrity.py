from __future__ import annotations

import argparse
import json
from pathlib import Path

from grid_data_factory.preservation.checksums import verify_checksums


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--attempt-dir", required=True)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    ok, errors = verify_checksums(Path(args.attempt_dir))
    print(json.dumps({"ok": ok, "errors": errors}, indent=2))
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
