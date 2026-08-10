#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

try:
    from grid_data_factory.campaigns.partial_inventory import CaseInventory, case_inventory_dict, extract_sample_summary
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from grid_data_factory.campaigns.partial_inventory import CaseInventory, case_inventory_dict, extract_sample_summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stream a partial campaign runs tree into a compact per-grid inventory.")
    parser.add_argument("--runs-tree", required=True, help="Map/reduce round directory containing shard samples.jsonl files.")
    parser.add_argument("--out", required=True, help="Output JSON report.")
    parser.add_argument("--sample-every", type=int, default=1, help="Parse every Nth record in each shard; 1 performs a full scan.")
    parser.add_argument("--timing-sample-size", type=int, default=4096, help="Maximum deterministic timing observations retained per grid.")
    parser.add_argument("--max-files", type=int, default=0, help="Optional file cap for smoke tests; 0 scans all files.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.sample_every <= 0 or args.timing_sample_size <= 0 or args.max_files < 0:
        raise SystemExit("--sample-every and --timing-sample-size must be > 0; --max-files must be >= 0")

    runs_tree = Path(args.runs_tree).resolve()
    files = sorted(runs_tree.glob("**/samples.jsonl"))
    if args.max_files:
        files = files[: args.max_files]
    if not files:
        raise SystemExit(f"No samples.jsonl files found under {runs_tree}")

    cases: dict[str, CaseInventory] = {}
    total_lines = sampled_lines = malformed = 0
    total_bytes = 0
    global_statuses: Counter[str] = Counter()
    started = time.monotonic()
    for file_index, path in enumerate(files, start=1):
        total_bytes += path.stat().st_size
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            for line_index, line in enumerate(stream):
                total_lines += 1
                if line_index % args.sample_every:
                    continue
                sampled_lines += 1
                summary = extract_sample_summary(line)
                if summary is None:
                    malformed += 1
                    continue
                inventory = cases.setdefault(summary.case_id, CaseInventory(args.timing_sample_size))
                inventory.add(summary, f"{path}:{line_index}")
                global_statuses[summary.termination_status] += 1
        if file_index == 1 or file_index % 25 == 0 or file_index == len(files):
            elapsed = max(time.monotonic() - started, 1e-9)
            print(
                f"inventory progress files={file_index}/{len(files)} lines={total_lines} "
                f"read_gib={total_bytes / 2**30:.1f} rate_mib_s={total_bytes / 2**20 / elapsed:.1f}",
                file=sys.stderr,
                flush=True,
            )

    case_rows = [case_inventory_dict(case_id, cases[case_id]) for case_id in sorted(cases)]
    elapsed_seconds = time.monotonic() - started
    report = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runs_tree": str(runs_tree),
        "files_scanned": len(files),
        "sample_every": args.sample_every,
        "bytes_read": total_bytes,
        "elapsed_seconds": round(elapsed_seconds, 3),
        "read_mib_per_second": round(total_bytes / 2**20 / elapsed_seconds, 3) if elapsed_seconds > 0 else None,
        "total_lines_seen": total_lines,
        "sampled_lines": sampled_lines,
        "parsed_records": sum(row["sampled_records"] for row in case_rows),
        "malformed_sampled_lines": malformed,
        "sampling_note": "Counts and rates are exact only when sample_every=1; timing quantiles use bounded deterministic samples.",
        "termination_statuses": dict(sorted(global_statuses.items())),
        "cases": case_rows,
    }
    out = Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "report": str(out), "files": len(files), "parsed_records": report["parsed_records"]}, indent=2))


if __name__ == "__main__":
    main()