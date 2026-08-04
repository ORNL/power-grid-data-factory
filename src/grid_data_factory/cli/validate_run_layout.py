from __future__ import annotations

import argparse
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--runs-root", required=True)
    return p.parse_args()


def main() -> None:
    runs_root = Path(parse_args().runs_root)
    required = ["pf", "dc_opf", "ac_opf", "scopf"]
    missing = [t for t in required if not (runs_root / t).exists()]
    print(json.dumps({"ok": len(missing) == 0, "missing": missing}, indent=2))
    if missing:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
