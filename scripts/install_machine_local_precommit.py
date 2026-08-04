#!/usr/bin/env python3
from __future__ import annotations

import argparse
import stat
from pathlib import Path


HOOK_BODY = """#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT=$(git rev-parse --show-toplevel)
PYTHON_BIN=${PYTHON_BIN:-python3.11}

$PYTHON_BIN "$REPO_ROOT/scripts/check_machine_local_changes.py" --staged
"""


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Install pre-commit hook to block machine-local/generated files.")
    p.add_argument("--root", default=".", help="Repository root path.")
    p.add_argument("--force", action="store_true", help="Overwrite existing pre-commit hook.")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    repo = Path(args.root).resolve()
    hook = repo / ".git" / "hooks" / "pre-commit"

    if hook.exists() and not args.force:
        raise SystemExit("pre-commit hook already exists. Re-run with --force to overwrite.")

    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(HOOK_BODY, encoding="utf-8")
    mode = hook.stat().st_mode
    hook.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    print("installed", hook)


if __name__ == "__main__":
    main()
