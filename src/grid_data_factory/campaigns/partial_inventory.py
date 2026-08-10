from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import hashlib
import math
import re
from typing import Any


_CASE_RE = re.compile(r'"case_id"\s*:\s*"([^"]+)"')
_SUCCESS_RE = re.compile(r'"success"\s*:\s*(true|false)')
_STATUS_RE = re.compile(r'"termination_status"\s*:\s*"([^"]+)"')
_WALLCLOCK_RE = re.compile(r'"wallclock_seconds"\s*:\s*(null|[-+0-9.eE]+)')
_PERSISTENT_RE = re.compile(r'"persistent_julia"\s*:\s*(true|false)')


@dataclass(frozen=True)
class SampleSummary:
    case_id: str
    success: bool
    termination_status: str
    wallclock_seconds: float | None
    persistent_julia: bool | None


def extract_sample_summary(line: str) -> SampleSummary | None:
    case_match = _CASE_RE.search(line)
    success_match = _SUCCESS_RE.search(line)
    status_match = _STATUS_RE.search(line)
    if not case_match or not success_match or not status_match:
        return None

    wallclock_match = _WALLCLOCK_RE.search(line)
    wallclock = None
    if wallclock_match and wallclock_match.group(1) != "null":
        try:
            value = float(wallclock_match.group(1))
            wallclock = value if math.isfinite(value) and value >= 0 else None
        except ValueError:
            wallclock = None

    persistent_match = _PERSISTENT_RE.search(line[-16384:])
    return SampleSummary(
        case_id=case_match.group(1),
        success=success_match.group(1) == "true",
        termination_status=status_match.group(1),
        wallclock_seconds=wallclock,
        persistent_julia=(persistent_match.group(1) == "true" if persistent_match else None),
    )


@dataclass
class BoundedTimingSample:
    capacity: int
    values: list[tuple[int, float]] = field(default_factory=list)

    def add(self, identity: str, value: float) -> None:
        priority = int.from_bytes(hashlib.blake2b(identity.encode("utf-8"), digest_size=8).digest(), "big")
        item = (priority, value)
        if len(self.values) < self.capacity:
            self.values.append(item)
            return
        largest_index = max(range(len(self.values)), key=lambda index: self.values[index][0])
        if priority < self.values[largest_index][0]:
            self.values[largest_index] = item

    def quantile(self, fraction: float) -> float | None:
        if not self.values:
            return None
        ordered = sorted(value for _, value in self.values)
        index = round((len(ordered) - 1) * fraction)
        return ordered[max(0, min(index, len(ordered) - 1))]


@dataclass
class CaseInventory:
    timing_capacity: int
    records: int = 0
    successes: int = 0
    statuses: Counter[str] = field(default_factory=Counter)
    execution_modes: Counter[str] = field(default_factory=Counter)
    timings: BoundedTimingSample = field(init=False)

    def __post_init__(self) -> None:
        self.timings = BoundedTimingSample(self.timing_capacity)

    def add(self, summary: SampleSummary, identity: str) -> None:
        self.records += 1
        self.successes += int(summary.success)
        self.statuses[summary.termination_status] += 1
        mode = "persistent_julia" if summary.persistent_julia is True else "legacy" if summary.persistent_julia is None else "nonpersistent"
        self.execution_modes[mode] += 1
        if summary.wallclock_seconds is not None:
            self.timings.add(identity, summary.wallclock_seconds)


def recommend_tier(case: CaseInventory, min_records: int = 20) -> tuple[str, str]:
    if case.records < min_records:
        return "benchmark_required", f"only {case.records} sampled records"
    invalid_rate = case.statuses.get("INVALID_MODEL", 0) / case.records
    timeout_rate = case.statuses.get("timeout", 0) / case.records
    p95 = case.timings.quantile(0.95)
    if invalid_rate >= 0.25:
        return "quarantine_invalid_model", f"INVALID_MODEL rate {invalid_rate:.1%}"
    if timeout_rate >= 0.25:
        return "quarantine_timeout", f"timeout rate {timeout_rate:.1%}"
    if p95 is None:
        return "benchmark_required", "no wall-clock observations"
    if p95 > 120:
        return "slow_valid", f"sampled p95 wall time {p95:.1f}s"
    return "fast_valid", f"sampled p95 wall time {p95:.1f}s"


def case_inventory_dict(case_id: str, case: CaseInventory) -> dict[str, Any]:
    tier, reason = recommend_tier(case)
    records = case.records
    return {
        "case_id": case_id,
        "sampled_records": records,
        "success_count": case.successes,
        "success_rate": round(case.successes / records, 6) if records else 0.0,
        "timeout_count": case.statuses.get("timeout", 0),
        "timeout_rate": round(case.statuses.get("timeout", 0) / records, 6) if records else 0.0,
        "invalid_model_count": case.statuses.get("INVALID_MODEL", 0),
        "invalid_model_rate": round(case.statuses.get("INVALID_MODEL", 0) / records, 6) if records else 0.0,
        "termination_statuses": dict(sorted(case.statuses.items())),
        "execution_modes": dict(sorted(case.execution_modes.items())),
        "timing_sample_size": len(case.timings.values),
        "wallclock_p50_s": case.timings.quantile(0.50),
        "wallclock_p95_s": case.timings.quantile(0.95),
        "recommended_tier": tier,
        "recommendation_reason": reason,
    }