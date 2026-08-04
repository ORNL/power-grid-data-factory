"""Shared test bootstrap: put ``src`` on the path and expose the repo root."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def case_available(case_id: str) -> bool:
    from grid_data_factory.sources.registry import resolve_case_file

    try:
        resolve_case_file(REPO_ROOT, case_id)
        return True
    except Exception:
        return False
