"""Apply enumerated contingencies (component outages) to a parsed case.

Removes the outaged branches/generators for simultaneous and sequential N-1-1
events on a deep copy so the input case is never mutated.
"""
from __future__ import annotations

import json
from typing import Any


def remove_component(case_data: dict[str, Any], comp_type: str, comp_id: str) -> None:
    if comp_type == "branch":
        case_data["branches"] = [x for x in case_data.get("branches", []) if str(x.get("branch_id")) != comp_id]
    elif comp_type == "generator":
        case_data["generators"] = [x for x in case_data.get("generators", []) if str(x.get("gen_id")) != comp_id]


def apply_contingency(case_data: dict[str, Any], contingency: dict[str, Any] | None) -> dict[str, Any]:
    if not contingency:
        return case_data

    out = json.loads(json.dumps(case_data))
    event_type = contingency.get("event_type")
    if event_type == "simultaneous":
        for comp in contingency.get("components", []):
            remove_component(out, str(comp.get("type")), str(comp.get("id")))
    elif event_type == "sequential_n1n1":
        first = contingency.get("first_outage") or {}
        second = contingency.get("second_outage") or {}
        remove_component(out, str(first.get("type")), str(first.get("id")))
        remove_component(out, str(second.get("type")), str(second.get("id")))

    return out
