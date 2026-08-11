from __future__ import annotations

from collections import Counter
import hashlib
import heapq
import json
from pathlib import Path
import re
from typing import Callable, Iterator


_CANDIDATE_ID_RE = re.compile(r'"candidate_id"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"')
_CASE_ID_RE = re.compile(r'"case_id"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"')


def _field_value(pattern: re.Pattern[str], line: str) -> str | None:
    match = pattern.search(line)
    if match is None:
        return None
    try:
        return str(json.loads(f'"{match.group(1)}"'))
    except json.JSONDecodeError:
        return None


def candidate_identity(line: str) -> tuple[str, str] | None:
    prefix = line[:16384]
    candidate_id = _field_value(_CANDIDATE_ID_RE, prefix)
    case_id = _field_value(_CASE_ID_RE, prefix)
    if not candidate_id or not case_id:
        return None
    return candidate_id, case_id


def load_completed_candidate_ids(
    runs_tree: Path,
    progress: Callable[[dict[str, int]], None] | None = None,
) -> tuple[set[str], dict[str, int]]:
    completed: set[str] = set()
    files = sorted(runs_tree.glob("**/samples.jsonl"))
    files_scanned = records_seen = malformed_records = bytes_read = 0
    for file_index, path in enumerate(files, start=1):
        bytes_read += path.stat().st_size
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            for line in stream:
                if not line.strip():
                    continue
                records_seen += 1
                candidate_id = _field_value(_CANDIDATE_ID_RE, line[:16384])
                if candidate_id:
                    completed.add(candidate_id)
                else:
                    malformed_records += 1
        if progress is not None and (file_index == 1 or file_index % 25 == 0 or file_index == len(files)):
            progress(
                {
                    "files_scanned": file_index,
                    "total_files": len(files),
                    "records_seen": records_seen,
                    "bytes_read": bytes_read,
                }
            )
    return completed, {
        "files_scanned": len(files),
        "records_seen": records_seen,
        "malformed_records": malformed_records,
        "bytes_read": bytes_read,
    }


def select_unfinished_candidates(
    candidates_jsonl: Path,
    completed_ids: set[str],
    per_case: int,
    seed: int,
) -> tuple[dict[str, list[str]], dict[str, object]]:
    if per_case <= 0:
        raise ValueError("per_case must be > 0")

    reservoirs: dict[str, list[tuple[int, str, str]]] = {}
    available_by_case: Counter[str] = Counter()
    records_seen = malformed_records = completed_records = 0

    with candidates_jsonl.open("r", encoding="utf-8", errors="replace") as stream:
        for line in stream:
            if not line.strip():
                continue
            records_seen += 1
            identity = candidate_identity(line)
            if identity is None:
                malformed_records += 1
                continue
            candidate_id, case_id = identity
            if candidate_id in completed_ids:
                completed_records += 1
                continue

            available_by_case[case_id] += 1
            priority = int.from_bytes(
                hashlib.blake2b(f"{seed}:{candidate_id}".encode("utf-8"), digest_size=8).digest(),
                "big",
            )
            item = (-priority, candidate_id, line.rstrip("\n") + "\n")
            reservoir = reservoirs.setdefault(case_id, [])
            if len(reservoir) < per_case:
                heapq.heappush(reservoir, item)
            elif priority < -reservoir[0][0]:
                heapq.heapreplace(reservoir, item)

    selected = {
        case_id: [item[2] for item in sorted(reservoir, key=lambda item: (-item[0], item[1]))]
        for case_id, reservoir in sorted(reservoirs.items())
    }
    return selected, {
        "records_seen": records_seen,
        "malformed_records": malformed_records,
        "completed_records_skipped": completed_records,
        "available_by_case": dict(sorted(available_by_case.items())),
        "selected_by_case": {case_id: len(lines) for case_id, lines in selected.items()},
    }


def iter_selected_records(selected: dict[str, list[str]]) -> Iterator[tuple[str, str]]:
    for case_id in sorted(selected):
        yield from ((case_id, line) for line in selected[case_id])