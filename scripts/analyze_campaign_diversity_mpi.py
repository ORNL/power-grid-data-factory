#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

_script_dir = Path(__file__).resolve().parent
_repo_root = _script_dir.parent
sys.path.insert(0, str(_repo_root / "src"))
sys.path.insert(0, str(_script_dir))

import analyze_campaign_diversity as serial_cli  # noqa: E402
from grid_data_factory.diversity.audit import (  # noqa: E402
    AuditPartial,
    discover_shard_ledgers,
    merge_audit_partials,
    sample_priority,
    scan_ledgers,
)
from grid_data_factory.storage import paths  # noqa: E402


def partition_paths(items: list[Path], rank: int, size: int) -> list[Path]:
    if size <= 0 or rank < 0 or rank >= size:
        raise ValueError("invalid MPI rank or size")
    return items[rank::size]


def global_priority_threshold(priority_lists: list[list[int]], sample_size: int) -> int | None:
    priorities = sorted(priority for values in priority_lists for priority in values)
    if not priorities:
        return None
    return priorities[min(sample_size, len(priorities)) - 1]


def _without_sample(partial: AuditPartial, sample_rows: list[dict[str, Any]]) -> AuditPartial:
    return AuditPartial(
        input_ledger_count=partial.input_ledger_count,
        row_count=partial.row_count,
        round_counts=partial.round_counts,
        categorical=partial.categorical,
        topology_contingency=partial.topology_contingency,
        sample_rows=sample_rows,
    )


def main() -> None:
    try:
        from mpi4py import MPI
    except ModuleNotFoundError as exc:
        raise SystemExit("mpi4py is required; install the project with the 'analysis-mpi' extra") from exc

    comm = MPI.COMM_WORLD
    rank = comm.Get_rank()
    size = comm.Get_size()
    args = serial_cli.parse_args()
    config = serial_cli.analysis_config(args, _repo_root)
    started = time.monotonic()

    discovery: dict[str, Any] | None = None
    if rank == 0:
        ledgers, missing = discover_shard_ledgers(paths.campaigns_root(_repo_root), args.campaign_id, args.rounds)
        error = None
        if missing and not args.allow_missing_ledgers:
            error = f"Missing {len(missing)} required ledgers; first missing path: {missing[0]}"
        elif not ledgers:
            error = "No diversity ledgers found"
        discovery = {
            "ledgers": [str(path) for path in ledgers],
            "missing": missing,
            "error": error,
        }
    discovery = comm.bcast(discovery, root=0)
    if discovery["error"]:
        raise SystemExit(discovery["error"])

    all_ledgers = [Path(value) for value in discovery["ledgers"]]
    local_ledgers = partition_paths(all_ledgers, rank, size)
    local_partial: AuditPartial | None = None
    local_error: str | None = None
    try:
        local_partial = scan_ledgers(local_ledgers, config)
    except Exception as exc:  # noqa: BLE001
        local_error = f"rank {rank}: {type(exc).__name__}: {exc}"

    errors = comm.gather(local_error, root=0)
    has_errors = comm.bcast(any(errors) if rank == 0 else None, root=0)
    if has_errors:
        if rank == 0:
            print(json.dumps({"ok": False, "errors": [error for error in errors if error]}, indent=2), file=sys.stderr)
        comm.Abort(2)
        return

    assert local_partial is not None
    local_priorities = [sample_priority(row, config.seed) for row in local_partial.sample_rows]
    priority_lists = comm.gather(local_priorities, root=0)
    threshold = comm.bcast(global_priority_threshold(priority_lists, config.sample_size) if rank == 0 else None, root=0)
    selected_rows = [
        row for row in local_partial.sample_rows
        if threshold is not None and sample_priority(row, config.seed) <= threshold
    ]
    partials = comm.gather(_without_sample(local_partial, selected_rows), root=0)

    if rank != 0:
        return

    report, sample_rows = merge_audit_partials(partials, config)
    report.update(
        {
            "campaign_id": args.campaign_id,
            "rounds": args.rounds,
            "git_commit": serial_cli._git_head(_repo_root),
            "missing_ledger_count": len(discovery["missing"]),
            "missing_ledgers": discovery["missing"],
            "execution": {
                "mode": "mpi",
                "mpi_ranks": size,
                "ledger_counts_by_rank": [partial.input_ledger_count for partial in partials],
                "elapsed_seconds": time.monotonic() - started,
            },
        }
    )
    round_slug = f"rounds_{args.rounds[0]:03d}-{args.rounds[-1]:03d}"
    output_dir = Path(args.output_dir).resolve() if args.output_dir else paths.reports_dir(_repo_root) / "diversity" / args.campaign_id / round_slug
    outputs = serial_cli.write_outputs(output_dir, report, sample_rows)
    print(json.dumps({"ok": True, **outputs, "mpi_ranks": size}, indent=2))


if __name__ == "__main__":
    main()
