from __future__ import annotations


def sequential_n1n1_record(
    first_outage: str,
    second_outage: str,
    intermediate_run_id: str,
    final_run_id: str,
    corrective_action: dict,
) -> dict:
    return {
        "event_type": "sequential_n1n1",
        "first_outage": first_outage,
        "second_outage": second_outage,
        "intermediate_run_id": intermediate_run_id,
        "final_run_id": final_run_id,
        "corrective_action": corrective_action,
    }
