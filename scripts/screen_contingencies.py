#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
import sys
from typing import Any

try:
    from grid_data_factory.acquisition.audit_sampler import stratified_random_order
    from grid_data_factory.screening.escalation import should_escalate_to_ac
except ModuleNotFoundError:
    _repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(_repo_root / "src"))
    from grid_data_factory.acquisition.audit_sampler import stratified_random_order
    from grid_data_factory.screening.escalation import should_escalate_to_ac


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


def _band(x: float) -> str:
    if x < 0.25:
        return "low"
    if x < 0.6:
        return "medium"
    return "high"


def _augment_strata(cand: dict[str, Any]) -> None:
    cand.setdefault("grid_family", "unknown")
    cand.setdefault("operating_regime", "unknown")
    cand.setdefault("contingency_order", 0)
    cand.setdefault("dc_severity_band", _band(float(cand.get("dc_severity_score", 0.0))))
    cand.setdefault("voltage_risk_band", _band(float(cand.get("voltage_risk_score", 0.0))))
    cand.setdefault("reactive_risk_band", _band(float(cand.get("reactive_risk_score", 0.0))))


def _audit_sample(rejected: list[dict[str, Any]], audit_fraction: float, seed: int) -> list[dict[str, Any]]:
    if not rejected:
        return []

    target = max(1, int(round(len(rejected) * max(0.0, min(1.0, audit_fraction)))))
    ordered = stratified_random_order(
        candidates=rejected,
        strata_keys=(
            "grid_family",
            "operating_regime",
            "contingency_order",
            "dc_severity_band",
            "voltage_risk_band",
            "reactive_risk_band",
        ),
        seed=seed,
    )
    return ordered[:target]


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

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    total = len(candidates)
    for i, cand in enumerate(candidates):
        _augment_strata(cand)

        should_run, reasons = should_escalate_to_ac(cand, thresholds)
        cand["screening_decision"] = "accepted" if should_run else "rejected"
        cand["screening_reasons"] = reasons
        cand["required_audit_sample"] = False

        if should_run:
            accepted.append(cand)
        else:
            rejected.append(cand)

        if (i + 1) % 200000 == 0:
            print(f"[screen] {i + 1}/{total} candidates screened ({len(accepted)} accepted)", flush=True)

    audited = _audit_sample(rejected=rejected, audit_fraction=args.audit_fraction, seed=args.seed)
    audited_ids = {str(x.get("candidate_id")) for x in audited}

    selected_for_ac: list[dict[str, Any]] = []
    for cand in accepted + rejected:
        cid = str(cand.get("candidate_id"))
        if cid in audited_ids:
            cand["required_audit_sample"] = True
            if "audit_rejected_region" not in cand["screening_reasons"]:
                cand["screening_reasons"].append("audit_rejected_region")
            selected_for_ac.append(cand)
            continue

        if cand["screening_decision"] == "accepted":
            selected_for_ac.append(cand)

    counts_by_reason: dict[str, int] = defaultdict(int)
    for cand in selected_for_ac:
        for reason in cand.get("screening_reasons", []):
            counts_by_reason[str(reason)] += 1

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
                "escalated_count": len(accepted),
                "rejected_count": len(rejected),
                "audit_count": len(audited),
                "selected_for_ac_count": len(selected_for_ac),
                "selection_reason_counts": dict(counts_by_reason),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
