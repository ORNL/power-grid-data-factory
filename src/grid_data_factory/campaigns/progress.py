from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class SolveProgress:
    def __init__(
        self,
        path: Path | None,
        campaign_id: str,
        total_candidates: int,
        initial: dict[str, Any] | None = None,
        interval_seconds: float = 60.0,
    ) -> None:
        self.path = path
        self.interval_seconds = max(1.0, float(interval_seconds))
        self.last_write = 0.0
        initial = initial or {}
        self.state: dict[str, Any] = {
            "schema_version": 1,
            "campaign_id": campaign_id,
            "phase": "running",
            "total_candidates": total_candidates,
            "attempted": int(initial.get("attempted", 0)),
            "converged": int(initial.get("converged", 0)),
            "unsuccessful": int(initial.get("unsuccessful", 0)),
            "resumed_attempts": int(initial.get("attempted", 0)),
            "session_attempted": 0,
            "session_converged": 0,
            "session_unsuccessful": 0,
            "errors": 0,
            "skipped": 0,
            "counts_by_case": dict(initial.get("counts_by_case", {})),
            "converged_by_case": dict(initial.get("converged_by_case", {})),
            "termination_statuses": dict(initial.get("termination_statuses", {})),
            "session_converged_by_case": {},
            "session_termination_statuses": {},
            "current_candidate_id": None,
            "updated_at": None,
        }
        self.write(force=True)

    @staticmethod
    def _increment(values: dict[str, int], key: str) -> None:
        values[key] = int(values.get(key, 0)) + 1

    def started(self, candidate_id: str) -> None:
        first_candidate = self.state["current_candidate_id"] is None
        self.state["current_candidate_id"] = candidate_id
        self.write(force=first_candidate)

    def result(self, case_id: str, candidate_id: str, result: dict[str, Any]) -> None:
        self.state["attempted"] += 1
        self.state["session_attempted"] += 1
        self.state["current_candidate_id"] = candidate_id
        self._increment(self.state["counts_by_case"], case_id)
        self._increment(self.state["termination_statuses"], str(result.get("termination_status", "unknown")))
        self._increment(self.state["session_termination_statuses"], str(result.get("termination_status", "unknown")))
        if bool(result.get("success", False)):
            self.state["converged"] += 1
            self.state["session_converged"] += 1
            self._increment(self.state["converged_by_case"], case_id)
            self._increment(self.state["session_converged_by_case"], case_id)
        else:
            self.state["unsuccessful"] += 1
            self.state["session_unsuccessful"] += 1
        self.write()

    def error(self, candidate_id: str) -> None:
        self.state["errors"] += 1
        self.state["current_candidate_id"] = candidate_id
        self.write()

    def skipped(self, candidate_id: str) -> None:
        self.state["skipped"] += 1
        self.state["current_candidate_id"] = candidate_id
        self.write()

    def complete(self) -> None:
        self.state["phase"] = "complete"
        self.state["current_candidate_id"] = None
        self.write(force=True)

    def write(self, force: bool = False) -> None:
        if self.path is None:
            return
        now = time.monotonic()
        if not force and now - self.last_write < self.interval_seconds:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.state["updated_at"] = datetime.now(timezone.utc).isoformat()
        temporary = self.path.with_suffix(self.path.suffix + f".{os.getpid()}.tmp")
        temporary.write_text(json.dumps(self.state, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(self.path)
        self.last_write = now