from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_registry_record_jsonl(runs_root: Path, record: dict[str, Any]) -> None:
    jsonl = runs_root / "run_registry.jsonl"
    jsonl.parent.mkdir(parents=True, exist_ok=True)
    with jsonl.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=True) + "\n")


def append_registry_record_safe(runs_root: Path, record: dict[str, Any]) -> None:
    # Uses the parquet-backed writer when pandas is importable, else JSONL only (Cray Python).
    try:
        from grid_data_factory.storage.registry import append_registry_record
    except ModuleNotFoundError:
        _append_registry_record_jsonl(runs_root, record)
        return
    try:
        append_registry_record(runs_root, record)
    except ModuleNotFoundError:
        _append_registry_record_jsonl(runs_root, record)


def write_common_attempt_files(
    in_progress: Path,
    run_meta: dict[str, Any],
    cmd_args: list[str],
    script_name: str,
) -> None:
    run_yaml = "\n".join([f"{k}: {v}" for k, v in run_meta.items()]) + "\n"
    (in_progress / "run.yaml").write_text(run_yaml, encoding="utf-8")
    (in_progress / "command.txt").write_text(
        f"python scripts/{script_name} " + " ".join(cmd_args), encoding="utf-8"
    )
    (in_progress / "command.json").write_text(
        json.dumps({"executable": "python3.11", "args": [f"scripts/{script_name}", *cmd_args]}, indent=2),
        encoding="utf-8",
    )
    (in_progress / "environment" / "environment.json").write_text(
        json.dumps({"created_at": utc_now_iso(), "python": "3.11", "script": script_name}, indent=2),
        encoding="utf-8",
    )
