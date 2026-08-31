"""Backfill ``feasibility_label`` and upgrade schema_version on existing samples.

Walks every ``samples.jsonl`` under data/outputs/ and:
  - adds ``feasibility_label`` to schema-1.0 records that lack it
  - re-classifies any ``INVALID_MODEL`` + ``primal_status=FEASIBLE_POINT`` records
    from ``error`` → ``infeasible`` (contingency-induced islanding)
  - bumps ``schema_version`` from "1.0" → "1.1" on touched records

Writes are atomic: each file is rewritten to a sibling .tmp then renamed.
Pass --dry-run to preview counts without touching files.
"""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Feasibility classification (mirrors round_runner.classify_feasibility)
# ---------------------------------------------------------------------------

_FEASIBLE = frozenset({"LOCALLY_SOLVED", "OPTIMAL", "ALMOST_LOCALLY_SOLVED", "ALMOST_OPTIMAL", "GLOBALLY_SOLVED"})
_AMBIGUOUS_INFEASIBLE = frozenset({"DUAL_INFEASIBLE", "INFEASIBLE_OR_UNBOUNDED"})
_INDETERMINATE = frozenset({
    "TIMEOUT", "TIME_LIMIT", "ITERATION_LIMIT", "NODE_LIMIT", "SOLUTION_LIMIT",
    "MEMORY_LIMIT", "OBJECTIVE_LIMIT", "NORM_LIMIT", "SLOW_PROGRESS",
    "INTERRUPTED", "OPTIMIZE_NOT_CALLED", "UNKNOWN",
})
_ERROR = frozenset({
    "EXCEPTION", "SERVER_EXCEPTION", "PROCESS_ERROR", "NOT_IMPLEMENTED",
    "INVALID_MODEL", "INVALID_OPTION", "NUMERICAL_ERROR", "OTHER_ERROR",
})


def _classify(result):  # type: ignore[override]
    if bool(result.get("success", False)):
        return "feasible"
    upper = str(result.get("termination_status", "unknown")).strip().upper()
    if upper in _FEASIBLE:
        return "feasible"
    if upper in _AMBIGUOUS_INFEASIBLE:
        return "indeterminate"
    if "INFEASIBLE" in upper:
        return "infeasible"
    if upper in _INDETERMINATE:
        return "indeterminate"
    if upper == "INVALID_MODEL":
        raw = result.get("raw_result") or {}
        if str(raw.get("primal_status", "")).upper() == "FEASIBLE_POINT":
            return "infeasible"
        return "error"
    if upper in _ERROR:
        return "error"
    return "indeterminate"


# ---------------------------------------------------------------------------
# Per-file processing
# ---------------------------------------------------------------------------

def _process_file(path, dry_run):
    """Return (total_records, updated_records)."""
    lines = path.read_text(encoding="utf-8").splitlines()
    out_lines: list[str] = []
    updated = 0

    for raw_line in lines:
        raw_line = raw_line.strip()
        if not raw_line:
            out_lines.append(raw_line)
            continue
        try:
            rec = json.loads(raw_line)
        except json.JSONDecodeError:
            out_lines.append(raw_line)
            continue

        result_sub = rec.get("result") or {}
        correct_label = _classify(result_sub)
        existing_label = rec.get("feasibility_label")
        existing_schema = rec.get("schema_version", "1.0")

        needs_update = existing_label != correct_label or existing_schema == "1.0"

        if needs_update:
            rec["feasibility_label"] = correct_label
            if existing_schema == "1.0":
                rec["schema_version"] = "1.1"
            updated += 1
            out_lines.append(json.dumps(rec, separators=(",", ":")))
        else:
            out_lines.append(raw_line)

    if updated > 0 and not dry_run:
        tmp = path.with_suffix(".jsonl.tmp")
        tmp.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
        tmp.replace(path)

    return len([l for l in lines if l.strip()]), updated


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(ROOT / "data" / "outputs"), help="Root to scan (default: data/outputs/)")
    parser.add_argument("--dry-run", action="store_true", help="Report counts without modifying files")
    args = parser.parse_args()

    scan_root = Path(args.root)
    if not scan_root.exists():
        sys.exit(f"Not found: {scan_root}")

    total_files = total_records = total_updated = 0
    for dirpath, _dirs, files in os.walk(scan_root):
        if "samples.jsonl" not in files:
            continue
        path = Path(dirpath) / "samples.jsonl"
        records, updated = _process_file(path, dry_run=args.dry_run)
        if records:
            total_files += 1
            total_records += records
            total_updated += updated
            if updated:
                rel = path.relative_to(ROOT)
                print(f"  {'(dry) ' if args.dry_run else ''}updated {updated}/{records}  {rel}")

    print(f"\n{'[dry-run] ' if args.dry_run else ''}files scanned: {total_files}  "
          f"records: {total_records}  updated: {total_updated}")


if __name__ == "__main__":
    main()
