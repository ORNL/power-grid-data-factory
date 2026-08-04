from __future__ import annotations

from collections import defaultdict


def update_active_constraint_ledger(rows: list[dict], solved_record: dict) -> list[dict]:
    signature = solved_record.get("active_constraint_signature", {})
    by_key = {(r["constraint_family"], r["component_id"]): dict(r) for r in rows}

    for key, state in signature.items():
        family, component_id = (key.split(":", 1) + ["unknown"])[:2]
        ledger_key = (family, component_id)
        row = by_key.get(
            ledger_key,
            {
                "constraint_family": family,
                "component_id": component_id,
                "active_count": 0,
                "near_active_count": 0,
                "last_discovery_round": solved_record.get("round_index", -1),
            },
        )
        if state == 2:
            row["active_count"] += 1
        elif state == 1:
            row["near_active_count"] += 1

        row["last_discovery_round"] = solved_record.get("round_index", -1)
        by_key[ledger_key] = row

    return list(by_key.values())


def activation_frequency_by_family(rows: list[dict]) -> dict[str, int]:
    agg: dict[str, int] = defaultdict(int)
    for row in rows:
        agg[row.get("constraint_family", "unknown")] += int(row.get("active_count", 0))
    return dict(agg)
