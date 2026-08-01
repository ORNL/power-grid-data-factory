from __future__ import annotations

import argparse
import json
from pathlib import Path

from grid_data_factory.preservation.artifacts import build_artifacts_manifest
from grid_data_factory.preservation.checksums import write_checksums
from grid_data_factory.storage.layout import finalize_attempt_directory


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--attempt-dir", required=True)
    p.add_argument("--terminal-status", default="SUCCESS")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    attempt_dir = Path(args.attempt_dir)
    marker = args.terminal_status.strip().upper()

    build_artifacts_manifest(attempt_dir)
    write_checksums(attempt_dir)

    (attempt_dir / marker).write_text("", encoding="utf-8")
    if attempt_dir.name.endswith(".in_progress"):
        finalized = finalize_attempt_directory(attempt_dir)
    else:
        finalized = attempt_dir

    summary = {"attempt": str(finalized), "terminal_status": marker}
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
