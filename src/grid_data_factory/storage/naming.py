from __future__ import annotations

import re

_SAFE = re.compile(r"^[a-z0-9_\-]+$")


def normalize_name(name: str) -> str:
    out = name.strip().lower().replace(" ", "_")
    out = re.sub(r"[^a-z0-9_\-]", "", out)
    return out


def validate_name(name: str, field: str) -> None:
    if not _SAFE.match(name):
        raise ValueError(f"{field} must be lowercase filesystem-safe [a-z0-9_-], got: {name}")


def format_topology_id(index: int, description: str) -> str:
    return f"topology_{index:06d}_{normalize_name(description)}"


def format_operating_point_id(index: int, regime: str) -> str:
    return f"op_{index:06d}_{normalize_name(regime)}"


def format_attempt_id(index: int) -> str:
    return f"attempt_{index:06d}"
