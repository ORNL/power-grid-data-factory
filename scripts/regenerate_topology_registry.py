#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Regenerate topology registry entries from a TSV case inventory in one command."
    )
    p.add_argument(
        "--cases-tsv",
        default="data/analysis/topology_candidates_campaign.tsv",
        help="TSV with columns: source, case_id, topology_id, case_file.",
    )
    p.add_argument("--registry-root", default="data/topology_registry")
    p.add_argument("--description", default="baseline")
    p.add_argument(
        "--topology-index",
        type=int,
        default=0,
        help="Fixed topology index for all cases (default: 0 -> topology_000000_<description>).",
    )
    p.add_argument("--clear-registry", action="store_true", help="Delete and recreate registry-root before regenerating.")
    p.add_argument("--continue-on-error", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--limit", type=int, default=0, help="Optional max number of cases to process (0 means all).")
    p.add_argument("--out", default="data/analysis/topology_regeneration_report.json")
    return p.parse_args()


def _load_cases(cases_tsv: Path) -> list[dict[str, str]]:
    if not cases_tsv.exists():
        raise FileNotFoundError(f"cases TSV not found: {cases_tsv}")

    with cases_tsv.open("r", encoding="utf-8") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        rows = list(reader)

    required = {"source", "case_id", "case_file"}
    if not rows:
        return []
    missing = required.difference(rows[0].keys())
    if missing:
        raise ValueError(f"cases TSV missing required column(s): {sorted(missing)}")

    dedup: dict[tuple[str, str, str], dict[str, str]] = {}
    for row in rows:
        source = (row.get("source") or "").strip()
        case_id = (row.get("case_id") or "").strip()
        case_file = (row.get("case_file") or "").strip()
        if not source or not case_id or not case_file:
            continue
        dedup[(source, case_id, case_file)] = row

    return [dedup[k] for k in sorted(dedup.keys())]


def _run_create_topology(
    repo_root: Path,
    row: dict[str, str],
    registry_root: str,
    description: str,
    topology_index: int | None,
) -> dict[str, Any]:
    cmd = [
        "python3.11",
        str(repo_root / "scripts" / "create_topology.py"),
        "--source",
        str(row["source"]),
        "--case-id",
        str(row["case_id"]),
        "--case-file",
        str(row["case_file"]),
        "--description",
        description,
        "--registry-root",
        registry_root,
    ]
    if topology_index is not None:
        cmd.extend(["--topology-index", str(topology_index)])

    proc = subprocess.run(cmd, cwd=repo_root, text=True, capture_output=True)
    result: dict[str, Any] = {
        "source": row["source"],
        "case_id": row["case_id"],
        "case_file": row["case_file"],
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
    }

    if proc.stdout:
        result["stdout"] = proc.stdout
    if proc.stderr:
        result["stderr"] = proc.stderr

    if proc.returncode == 0:
        try:
            payload = json.loads(proc.stdout)
            result["topology_id"] = payload.get("topology_id")
            result["path"] = payload.get("path")
        except Exception:
            pass

    return result


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]

    cases_tsv = Path(args.cases_tsv)
    if not cases_tsv.is_absolute():
        cases_tsv = (repo_root / cases_tsv).resolve()

    registry_root = Path(args.registry_root)
    if not registry_root.is_absolute():
        registry_root = (repo_root / registry_root).resolve()

    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = (repo_root / out_path).resolve()

    cases = _load_cases(cases_tsv)
    if args.limit > 0:
        cases = cases[: args.limit]

    if args.clear_registry and not args.dry_run and registry_root.exists():
        shutil.rmtree(registry_root)
    if not args.dry_run:
        registry_root.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    for idx, row in enumerate(cases, start=1):
        if args.dry_run:
            results.append(
                {
                    "source": row["source"],
                    "case_id": row["case_id"],
                    "case_file": row["case_file"],
                    "ok": True,
                    "dry_run": True,
                    "position": idx,
                }
            )
            continue

        out = _run_create_topology(
            repo_root=repo_root,
            row=row,
            registry_root=str(registry_root),
            description=args.description,
            topology_index=args.topology_index,
        )
        out["position"] = idx
        results.append(out)

        if not out.get("ok", False) and not args.continue_on_error:
            break

    report = {
        "ok": all(r.get("ok", False) for r in results) if results else True,
        "cases_tsv": str(cases_tsv),
        "registry_root": str(registry_root),
        "description": args.description,
        "topology_index": args.topology_index,
        "clear_registry": bool(args.clear_registry),
        "dry_run": bool(args.dry_run),
        "requested_cases": len(cases),
        "processed_cases": len(results),
        "success_count": sum(1 for r in results if r.get("ok", False)),
        "failure_count": sum(1 for r in results if not r.get("ok", False)),
        "results": results,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {
                "ok": report["ok"],
                "requested_cases": report["requested_cases"],
                "processed_cases": report["processed_cases"],
                "success_count": report["success_count"],
                "failure_count": report["failure_count"],
                "report": str(out_path),
                "registry_root": str(registry_root),
            },
            indent=2,
        )
    )

    if not report["ok"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
