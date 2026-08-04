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
from types import SimpleNamespace
from typing import Any

try:
    from grid_data_factory.contingencies.ontology import ONTOLOGY_CLASSES
    from grid_data_factory.sources.registry import bus_count_for
except ModuleNotFoundError:
    _repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(_repo_root / "src"))
    from grid_data_factory.contingencies.ontology import ONTOLOGY_CLASSES
    from grid_data_factory.sources.registry import bus_count_for


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
    p.add_argument("--workers", type=int, default=1, help="Parallel worker processes (>1 enables per-row deterministic seeding; 0=all cores).")
    return p.parse_args()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _case_component_pool(case_id: str, repo_root: Path | None = None) -> dict[str, list[str]]:
    lower = case_id.lower()
    buses = bus_count_for(repo_root, case_id) if repo_root is not None else None
    if buses and buses > 0:
        branches = max(1, int(round(buses * 1.5)))
        gens = max(1, int(round(buses * 0.15)))
    elif "118" in lower:
        buses, branches, gens = 118, 186, 54
    elif "57" in lower:
        buses, branches, gens = 57, 80, 7
    elif "14" in lower:
        buses, branches, gens = 14, 20, 5
    else:
        buses, branches, gens = 80, 120, 20

    return {
        "branch": [f"branch_{i:06d}" for i in range(1, branches + 1)],
        "generator": [f"gen_{i:06d}" for i in range(1, gens + 1)],
        "bus": [f"bus_{i:06d}" for i in range(1, buses + 1)],
    }


def _with_scores(base: dict[str, Any], order_k: int, contingency_class: str, base_severity: float, base_credibility: float) -> dict[str, Any]:
    out = dict(base)
    out["contingency_order"] = order_k
    out["contingency_class"] = contingency_class

    severity_boost = 0.08 * order_k
    if contingency_class in {"electrically_interacting", "common_corridor", "sequential_n1n1", "cut_set"}:
        severity_boost += 0.10

    out["contingency_severity_score"] = min(1.0, float(base_severity) + severity_boost)
    out["security_boundary_score"] = min(1.0, float(out.get("security_boundary_score", 0.0)) + 0.06 * order_k)
    out["active_constraint_score"] = min(1.0, float(out.get("active_constraint_score", 0.0)) + 0.05 * order_k)
    out["physical_credibility_score"] = max(0.0, min(1.0, base_credibility))
    out["estimated_compute_cost"] = float(out.get("estimated_compute_cost", 1.0)) * (1.0 + 0.4 * order_k)
    return out


def _pick_distinct(rng: Random, pool: list[str], count: int) -> list[str]:
    if count <= 0:
        return []
    if count >= len(pool):
        return list(pool)
    return rng.sample(pool, count)


def _unique_components(components: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, str]] = []
    for comp in components:
        key = (str(comp.get("type")), str(comp.get("id")))
        if key in seen:
            continue
        seen.add(key)
        out.append({"type": key[0], "id": key[1]})
    return out


def _build_kplus_components(pool: dict[str, list[str]], k: int, rng: Random) -> list[dict[str, str]]:
    # Branch-led interacting set of k distinct components; terminates even when the
    # generator pool is small by drawing without replacement from the combined pool.
    combined: list[dict[str, str]] = [{"type": "branch", "id": b} for b in pool["branch"]]
    combined += [{"type": "generator", "id": g} for g in pool["generator"]]
    if k >= len(combined):
        return _unique_components(combined)
    chosen = rng.sample(combined, k)
    if pool["branch"] and not any(c["type"] == "branch" for c in chosen):
        chosen[0] = {"type": "branch", "id": rng.choice(pool["branch"])}
    return _unique_components(chosen)


def _expand_one(base: dict[str, Any], rng: Random, args: argparse.Namespace, repo_root: Path | None = None) -> list[dict[str, Any]]:
    case_id = str(base.get("case_id"))
    cid = str(base.get("candidate_id"))
    pool = _case_component_pool(case_id, repo_root)
    base_sev = float(base.get("contingency_severity_score", 0.0))
    base_cred = float(base.get("physical_credibility_score", 0.9))

    out: list[dict[str, Any]] = []
    event_index = 0

    for branch in _pick_distinct(rng, pool["branch"], args.n1_per_operating_point):
        event_index += 1
        row = _with_scores(base, order_k=1, contingency_class="independent_random", base_severity=base_sev, base_credibility=base_cred)
        row["candidate_id"] = f"{cid}::ctg::{event_index:03d}"
        row["contingency"] = {
            "contingency_id": f"ctg_{event_index:06d}",
            "order": 1,
            "event_type": "simultaneous",
            "components": [{"type": "branch", "id": branch}],
            "ontology_labels": ["independent_random"],
            "credibility_source": "electrically_inferred",
        }
        out.append(row)

    branch_pairs = _pick_distinct(rng, pool["branch"], 2 * args.n2_random_per_operating_point)
    for i in range(0, len(branch_pairs), 2):
        if i + 1 >= len(branch_pairs):
            break
        event_index += 1
        row = _with_scores(base, order_k=2, contingency_class="common_corridor", base_severity=base_sev, base_credibility=max(0.0, base_cred - 0.05))
        row["candidate_id"] = f"{cid}::ctg::{event_index:03d}"
        row["contingency"] = {
            "contingency_id": f"ctg_{event_index:06d}",
            "order": 2,
            "event_type": "simultaneous",
            "components": [{"type": "branch", "id": branch_pairs[i]}, {"type": "branch", "id": branch_pairs[i + 1]}],
            "ontology_labels": ["common_corridor", "independent_random"],
            "credibility_source": "topologically_inferred",
        }
        out.append(row)

    for _ in range(args.n2_interacting_per_operating_point):
        event_index += 1
        b = rng.choice(pool["branch"])
        g = rng.choice(pool["generator"])
        row = _with_scores(base, order_k=2, contingency_class="electrically_interacting", base_severity=base_sev, base_credibility=max(0.0, base_cred - 0.02))
        row["candidate_id"] = f"{cid}::ctg::{event_index:03d}"
        row["contingency"] = {
            "contingency_id": f"ctg_{event_index:06d}",
            "order": 2,
            "event_type": "simultaneous",
            "components": [{"type": "branch", "id": b}, {"type": "generator", "id": g}],
            "ontology_labels": ["electrically_interacting", "generator_export_path"],
            "credibility_source": "electrically_inferred",
        }
        out.append(row)

    for _ in range(args.n1n1_per_operating_point):
        event_index += 1
        first = rng.choice(pool["branch"])
        second = rng.choice(pool["generator"])
        row = _with_scores(base, order_k=2, contingency_class="sequential_n1n1", base_severity=base_sev, base_credibility=base_cred)
        row["candidate_id"] = f"{cid}::ctg::{event_index:03d}"
        row["contingency"] = {
            "contingency_id": f"ctg_{event_index:06d}",
            "order": 2,
            "event_type": "sequential_n1n1",
            "first_outage": {"type": "branch", "id": first},
            "second_outage": {"type": "generator", "id": second},
            "allowed_corrective_action": "redispatch_and_voltage_control",
            "ontology_labels": ["sequential_n1n1"],
            "credibility_source": "synthetic_scenario_assumption",
        }
        out.append(row)

    max_k = max(2, int(args.max_k))
    nk_count = max(0, int(args.nk_per_operating_point))
    for k in range(3, max_k + 1):
        for _ in range(nk_count):
            event_index += 1
            components = _build_kplus_components(pool, k, rng)
            # As K grows, degrade credibility mildly unless interactions are clear.
            cred = max(0.0, base_cred - 0.03 * (k - 2))
            row = _with_scores(
                base,
                order_k=k,
                contingency_class="cut_set",
                base_severity=base_sev,
                base_credibility=cred,
            )
            row["candidate_id"] = f"{cid}::ctg::{event_index:03d}"
            row["contingency"] = {
                "contingency_id": f"ctg_{event_index:06d}",
                "order": k,
                "event_type": "simultaneous",
                "components": components,
                "ontology_labels": ["cut_set", "electrically_interacting"],
                "credibility_source": "synthetic_scenario_assumption",
            }
            out.append(row)

    for row in out:
        labels = row.get("contingency", {}).get("ontology_labels", [])
        unknown = [x for x in labels if x not in ONTOLOGY_CLASSES]
        if unknown:
            raise ValueError(f"Unknown ontology labels: {unknown}")

    return out


def _split(rows: list[Any], n: int) -> list[list[Any]]:
    # Contiguous chunks preserving input order.
    k, m = divmod(len(rows), n)
    chunks: list[list[Any]] = []
    start = 0
    for i in range(n):
        size = k + (1 if i < m else 0)
        chunks.append(rows[start:start + size])
        start += size
    return [c for c in chunks if c]


def _expand_chunk(chunk_index: int, rows: list[dict[str, Any]], sampling: dict[str, Any], repo_root_str: str, shard_path_str: str) -> tuple[int, int]:
    # Per-row deterministic seeding makes output independent of worker count and chunk boundaries.
    repo_root = Path(repo_root_str)
    args = SimpleNamespace(**sampling)
    seed = sampling["seed"]
    count = 0
    with open(shard_path_str, "w", encoding="utf-8") as fh:
        for row in rows:
            rng = Random(f"{seed}::{row.get('candidate_id')}")
            for out in _expand_one(row, rng, args, repo_root):
                fh.write(json.dumps(out, ensure_ascii=True) + "\n")
                count += 1
    return chunk_index, count


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
                for out in _expand_one(row, rng, args, repo_root):
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
        }
        chunks = _split(rows, workers)
        shard_paths = [out_path.parent / f".{out_path.name}.part{idx:04d}" for idx in range(len(chunks))]
        counts: dict[int, int] = {}
        done = 0
        print(f"[enumerate] parallel expansion of {len(rows)} operating rows across {len(chunks)} workers", flush=True)
        with ProcessPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(_expand_chunk, idx, chunk, sampling, str(repo_root), str(shard_paths[idx])): idx for idx, chunk in enumerate(chunks)}
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
