#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from random import Random
import sys
from typing import Any

try:
    from grid_data_factory.contingencies.enumeration import expand_chunk, expand_one, split_rows
except ModuleNotFoundError:
    _repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(_repo_root / "src"))
    from grid_data_factory.contingencies.enumeration import expand_chunk, expand_one, split_rows


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Enumerate physically credible contingency candidates.")
    p.add_argument("--input", required=True, help="Input operating-point candidates JSONL.")
    p.add_argument("--out", required=True, help="Output contingency-augmented candidates JSONL.")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--n1-per-operating-point", type=int, default=3)
    p.add_argument("--n2-random-per-operating-point", type=int, default=2)
    p.add_argument("--n2-interacting-per-operating-point", type=int, default=2)
    p.add_argument("--n1n1-per-operating-point", type=int, default=1)
    p.add_argument("--max-k", type=int, default=2, help="Maximum simultaneous contingency order K to generate (K>=2).")
    p.add_argument("--nk-per-operating-point", type=int, default=1, help="Number of simultaneous events to generate per order for K>=3.")
    p.add_argument("--sequential-cascade-per-operating-point", type=int, default=0, help="Number of ordered sequential cascades to generate per depth for depth 3..sequential-max-len (0 disables).")
    p.add_argument("--sequential-max-len", type=int, default=10, help="Maximum sequential cascade depth (stages); capped at --max-k.")
    p.add_argument("--workers", type=int, default=1, help="Parallel worker processes (>1 enables per-row deterministic seeding; 0=all cores).")
    return p.parse_args()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]

    in_path = Path(args.input)
    in_path = in_path if in_path.is_absolute() else (repo_root / in_path).resolve()
    out_path = Path(args.out)
    out_path = out_path if out_path.is_absolute() else (repo_root / out_path).resolve()

    rows = _read_jsonl(in_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    workers = args.workers if args.workers > 0 else (os.cpu_count() or 1)
    workers = max(1, min(workers, len(rows) or 1))

    if workers == 1:
        rng = Random(args.seed)
        expanded_count = 0
        with out_path.open("w", encoding="utf-8") as fh:
            for i, row in enumerate(rows):
                for out in expand_one(row, rng, args, repo_root):
                    fh.write(json.dumps(out, ensure_ascii=True) + "\n")
                    expanded_count += 1
                if (i + 1) % 50000 == 0:
                    print(f"[enumerate] {i + 1}/{len(rows)} operating rows expanded ({expanded_count} candidates)", flush=True)
    else:
        sampling = {
            "seed": args.seed,
            "n1_per_operating_point": args.n1_per_operating_point,
            "n2_random_per_operating_point": args.n2_random_per_operating_point,
            "n2_interacting_per_operating_point": args.n2_interacting_per_operating_point,
            "n1n1_per_operating_point": args.n1n1_per_operating_point,
            "max_k": args.max_k,
            "nk_per_operating_point": args.nk_per_operating_point,
            "sequential_cascade_per_operating_point": args.sequential_cascade_per_operating_point,
            "sequential_max_len": args.sequential_max_len,
        }
        chunks = split_rows(rows, workers)
        shard_paths = [out_path.parent / f".{out_path.name}.part{idx:04d}" for idx in range(len(chunks))]
        counts: dict[int, int] = {}
        done = 0
        print(f"[enumerate] parallel expansion of {len(rows)} operating rows across {len(chunks)} workers", flush=True)
        with ProcessPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(expand_chunk, idx, chunk, sampling, str(repo_root), str(shard_paths[idx])): idx for idx, chunk in enumerate(chunks)}
            for fut in as_completed(futures):
                idx, count = fut.result()
                counts[idx] = count
                done += 1
                print(f"[enumerate] chunk {done}/{len(chunks)} done ({count} candidates)", flush=True)
        expanded_count = 0
        with out_path.open("wb") as out_fh:
            for idx in range(len(chunks)):
                with open(shard_paths[idx], "rb") as sfh:
                    shutil.copyfileobj(sfh, out_fh)
                expanded_count += counts[idx]
                shard_paths[idx].unlink()

    print(
        json.dumps(
            {
                "ok": True,
                "input": str(in_path),
                "out": str(out_path),
                "base_candidates": len(rows),
                "expanded_candidates": expanded_count,
                "workers": workers,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
