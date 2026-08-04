from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


REGISTRY_COLUMNS = [
    "run_id",
    "task",
    "case_id",
    "topology_id",
    "operating_point_id",
    "contingency_set_id",
    "solver_id",
    "attempt_id",
    "path",
    "numerical_status",
    "preservation_status",
    "objective",
    "runtime",
    "wallclock_seconds",
    "mpi_processes",
    "gpu_enabled",
    "gpu_type",
    "validation_status",
    "maximum_p_mismatch",
    "maximum_q_mismatch",
    "maximum_voltage_violation",
    "maximum_generator_violation",
    "maximum_branch_violation",
    "total_artifact_count",
    "total_size_bytes",
    "created_at",
]


def append_registry_record(runs_root: Path, record: dict) -> None:
    jsonl = runs_root / "run_registry.jsonl"
    parquet = runs_root / "run_registry.parquet"

    with jsonl.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=True) + "\n")

    row = {k: record.get(k) for k in REGISTRY_COLUMNS}
    df_new = pd.DataFrame([row])
    if parquet.exists():
        df_old = pd.read_parquet(parquet)
        df = pd.concat([df_old, df_new], ignore_index=True)
    else:
        df = df_new
    df.to_parquet(parquet, index=False)
