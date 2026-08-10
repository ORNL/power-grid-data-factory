from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class CaseExecutionSettings:
    enabled: bool
    timeout_s: float
    tier: str
    reason: str


def load_execution_policy(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"Execution policy must be a mapping: {path}")
    return payload


def settings_for_case(policy: dict[str, Any], case_id: str, fallback_timeout_s: float) -> CaseExecutionSettings:
    defaults = policy.get("defaults") or {}
    cases = policy.get("cases") or {}
    if not isinstance(defaults, dict) or not isinstance(cases, dict):
        raise ValueError("Execution policy defaults and cases must be mappings")

    override = cases.get(case_id) or {}
    if not isinstance(override, dict):
        raise ValueError(f"Execution policy entry for {case_id} must be a mapping")

    enabled = bool(override.get("enabled", defaults.get("enabled", True)))
    timeout_s = float(override.get("timeout_s", defaults.get("timeout_s", fallback_timeout_s)))
    if timeout_s <= 0:
        raise ValueError(f"Execution timeout for {case_id} must be > 0")
    return CaseExecutionSettings(
        enabled=enabled,
        timeout_s=timeout_s,
        tier=str(override.get("tier", defaults.get("tier", "default"))),
        reason=str(override.get("reason", "")),
    )