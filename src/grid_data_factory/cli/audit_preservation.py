from __future__ import annotations

import argparse
import json
from pathlib import Path

from grid_data_factory.preservation.audit import audit_runs_root


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--runs-root", required=True)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    report = audit_runs_root(Path(args.runs_root))
    print(json.dumps(report, indent=2))
    if not report["ok"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
