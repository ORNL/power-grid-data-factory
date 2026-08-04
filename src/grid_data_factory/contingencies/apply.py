"""Apply enumerated contingencies (component outages) to a parsed case.

Removes the outaged branches/generators for simultaneous and sequential N-1-1
events on a deep copy so the input case is never mutated.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any


def _outaged_components(contingency: dict[str, Any]) -> list[tuple[str, str]]:
    event_type = contingency.get("event_type")
    if event_type == "sequential_n1n1":
        first = contingency.get("first_outage") or {}
        second = contingency.get("second_outage") or {}
        return [
            (str(first.get("type")), str(first.get("id"))),
            (str(second.get("type")), str(second.get("id"))),
        ]
    return [(str(c.get("type")), str(c.get("id"))) for c in contingency.get("components", [])]


def _sanitize_token(token: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", token)


def contingency_slug(contingency: dict[str, Any] | None, max_readable: int = 48) -> str:
    """Build a deterministic, filesystem-safe, human-readable directory token.

    The token encodes the outage order and components so a directory reveals the
    contingency at a glance; a content hash of the physical outage set (not the
    enumeration index) guarantees uniqueness and stability across re-enumeration.
    """
    if not contingency:
        return "ctg_base"
    comps = _outaged_components(contingency)
    sequential = contingency.get("event_type") == "sequential_n1n1"
    order = len(comps)
    key_comps = comps if sequential else sorted(comps)
    readable = "-".join(f"{t[:1]}{_sanitize_token(i)}" for t, i in key_comps)
    if len(readable) > max_readable:
        readable = readable[:max_readable]
    kind = "seq" if sequential else "k"
    canonical = json.dumps(["sequential" if sequential else "simultaneous", key_comps], sort_keys=True)
    digest = hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:8]
    return f"ctg_{kind}{order}_{readable}_{digest}"


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
