from __future__ import annotations

import json
from pathlib import Path
from typing import Any

def _require_pandas():
    try:
        import pandas as pd  # type: ignore
    except ModuleNotFoundError:
        return None
    return pd


def _require_yaml():
    try:
        import yaml  # type: ignore
    except ModuleNotFoundError:
        return None
    return yaml


def _fallback_jsonl_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".jsonl")

LEDGER_FILES = (
    "candidate_registry.parquet",
    "acquisition_decisions.parquet",
    "diversity_ledger.parquet",
    "active_constraint_ledger.parquet",
    "security_boundary_ledger.parquet",
    "contingency_portfolio.parquet",
    "screening_audit.parquet",
)


def create_campaign_layout(campaign_root: Path, campaign_config: dict[str, Any]) -> None:
    pd = _require_pandas()
    yaml = _require_yaml()

    campaign_root.mkdir(parents=True, exist_ok=True)
    (campaign_root / "round_summaries").mkdir(parents=True, exist_ok=True)

    (campaign_root / "README.md").write_text(
        "Adaptive campaign artifacts and ledgers.\n",
        encoding="utf-8",
    )
    if yaml is not None:
        cfg_text = yaml.safe_dump(campaign_config, sort_keys=False)
    else:
        cfg_text = json.dumps(campaign_config, indent=2)
    (campaign_root / "campaign_config.yaml").write_text(cfg_text, encoding="utf-8")

    for name in LEDGER_FILES:
        p = campaign_root / name
        if pd is not None and not p.exists():
            pd.DataFrame().to_parquet(p, index=False)
        if pd is None:
            _fallback_jsonl_path(p).touch(exist_ok=True)


def append_parquet_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    pd = _require_pandas()

    if not rows:
        return

    if pd is None:
        fallback = _fallback_jsonl_path(path)
        fallback.parent.mkdir(parents=True, exist_ok=True)
        with fallback.open("a", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=True) + "\n")
        return

    new_df = pd.DataFrame(rows)
    if path.exists():
        old_df = pd.read_parquet(path)
        out_df = pd.concat([old_df, new_df], ignore_index=True)
    else:
        out_df = new_df
    out_df.to_parquet(path, index=False)


def write_round_summary(campaign_root: Path, round_index: int, summary: dict[str, Any]) -> Path:
    out = campaign_root / "round_summaries" / f"round_{round_index:03d}.json"
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return out
