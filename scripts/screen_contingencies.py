#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

try:
    from grid_data_factory.screening.selection import screen_candidates
except ModuleNotFoundError:
    _repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(_repo_root / "src"))
    from grid_data_factory.screening.selection import screen_candidates


def _require_yaml():
    try:
        import yaml  # type: ignore
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "Missing dependency 'pyyaml'. Install project requirements before running screening."
        ) from exc
    return yaml


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Apply multi-trigger screening and audit tagging to contingency candidates.")
    p.add_argument("--input", required=True, help="Input contingency candidates JSONL.")
    p.add_argument("--out", required=True, help="Output screened candidates JSONL.")
    p.add_argument("--audit-out", default="", help="Optional output JSONL with rejected audit sample.")
    p.add_argument("--config", default="configs/campaign_default.yaml")
    p.add_argument("--audit-fraction", type=float, default=0.10)
    p.add_argument("--seed", type=int, default=0)
    return p.parse_args()


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        yaml = _require_yaml()
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except ModuleNotFoundError:
        return {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=True) + "\n")


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]

    cfg = _load_yaml((repo_root / args.config).resolve())
    thresholds = ((cfg.get("screening") or {}).get("escalation_thresholds") or {})

    in_path = Path(args.input)
    in_path = in_path if in_path.is_absolute() else (repo_root / in_path).resolve()
    out_path = Path(args.out)
    out_path = out_path if out_path.is_absolute() else (repo_root / out_path).resolve()

    candidates = _read_jsonl(in_path)

    def _report(done: int, total: int, accepted: int) -> None:
        print(f"[screen] {done}/{total} candidates screened ({accepted} accepted)", flush=True)

    result = screen_candidates(
        candidates,
        thresholds,
        audit_fraction=args.audit_fraction,
        seed=args.seed,
        on_progress=_report,
    )
    audited = result["audited"]
    selected_for_ac = result["selected_for_ac"]

    _write_jsonl(out_path, selected_for_ac)

    if args.audit_out:
        audit_out = Path(args.audit_out)
        audit_out = audit_out if audit_out.is_absolute() else (repo_root / audit_out).resolve()
        _write_jsonl(audit_out, audited)

    print(
        json.dumps(
            {
                "ok": True,
                "input": str(in_path),
                "out": str(out_path),
                "input_count": len(candidates),
                "escalated_count": len(result["accepted"]),
                "rejected_count": len(result["rejected"]),
                "audit_count": len(audited),
                "selected_for_ac_count": len(selected_for_ac),
                "selection_reason_counts": result["selection_reason_counts"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
