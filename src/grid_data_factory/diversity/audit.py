from __future__ import annotations

import hashlib
import heapq
import json
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

import numpy as np

from .nearest_neighbors import NUMERIC_FIELDS


ANALYSIS_SCHEMA_VERSION = "1.0"
CATEGORICAL_FIELDS = (
    "case_id",
    "dataset",
    "topology_class",
    "contingency_order",
)
SAMPLED_CATEGORICAL_FIELDS = (
    "active_constraint_signature",
    "near_active_constraint_signature",
)


def _case_id(row: dict[str, Any]) -> str:
    explicit = str(row.get("case_id", "")).strip()
    if explicit:
        return explicit
    return str(row.get("candidate_id", "unknown")).split("::", 1)[0] or "unknown"


def _stable_priority(row: dict[str, Any], seed: int) -> int:
    identity = str(row.get("run_id") or row.get("candidate_id") or json.dumps(row, sort_keys=True))
    digest = hashlib.sha256(f"{seed}:{identity}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def sample_priority(row: dict[str, Any], seed: int) -> int:
    """Return the stable priority used by serial and distributed sampling."""
    return _stable_priority(row, seed)


class DeterministicSample:
    """Keep the rows with the lowest stable hash priorities."""

    def __init__(self, capacity: int, seed: int) -> None:
        if capacity <= 1:
            raise ValueError("sample capacity must be greater than one")
        self.capacity = capacity
        self.seed = seed
        self._heap: list[tuple[int, int, dict[str, Any]]] = []
        self._serial = 0

    def add(self, row: dict[str, Any]) -> None:
        priority = sample_priority(row, self.seed)
        item = (-priority, self._serial, row)
        self._serial += 1
        if len(self._heap) < self.capacity:
            heapq.heappush(self._heap, item)
        elif priority < -self._heap[0][0]:
            heapq.heapreplace(self._heap, item)

    def rows(self) -> list[dict[str, Any]]:
        return [item[2] for item in sorted(self._heap, key=lambda item: (-item[0], item[1]))]


def iter_ledger_rows(path: Path) -> Iterator[dict[str, Any]]:
    if path.name.endswith(".jsonl"):
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    yield row
        return

    try:
        import pyarrow.parquet as pq
    except ModuleNotFoundError as exc:
        raise RuntimeError(f"pyarrow is required to read {path}") from exc
    parquet = pq.ParquetFile(path)
    for batch in parquet.iter_batches(batch_size=4096):
        yield from batch.to_pylist()


def discover_shard_ledgers(
    campaigns_root: Path,
    campaign_id: str,
    rounds: Iterable[int],
) -> tuple[list[Path], list[str]]:
    ledgers: list[Path] = []
    missing: list[str] = []
    campaign_root = campaigns_root / campaign_id
    for round_index in sorted(set(rounds)):
        ids_path = campaign_root / "round_summaries" / f"round_{round_index:03d}_shards" / "shard_campaign_ids.txt"
        if not ids_path.exists():
            missing.append(str(ids_path))
            continue
        for shard_id in ids_path.read_text(encoding="utf-8").splitlines():
            shard_id = shard_id.strip()
            if not shard_id:
                continue
            base = campaigns_root / shard_id / "diversity_ledger.parquet"
            fallback = base.with_suffix(base.suffix + ".jsonl")
            ledger = fallback if fallback.exists() else base
            if ledger.exists():
                ledgers.append(ledger)
            else:
                missing.append(str(ledger))
    return ledgers, missing


def normalized_entropy(counts: Counter[str]) -> float:
    total = sum(counts.values())
    if total <= 0 or len(counts) <= 1:
        return 0.0
    entropy = -sum((count / total) * math.log(count / total) for count in counts.values())
    return entropy / math.log(len(counts))


def hill_number_2(counts: Counter[str]) -> float:
    total = sum(counts.values())
    if total <= 0:
        return 0.0
    concentration = sum((count / total) ** 2 for count in counts.values())
    return 1.0 / concentration


def _quantiles(values: np.ndarray) -> dict[str, float | None]:
    if values.size == 0:
        return {key: None for key in ("p05", "p25", "p50", "p75", "p95")}
    points = np.quantile(values, [0.05, 0.25, 0.5, 0.75, 0.95])
    return {key: float(value) for key, value in zip(("p05", "p25", "p50", "p75", "p95"), points)}


def _numeric_value(row: dict[str, Any], field: str) -> float:
    try:
        value = float(row.get(field, 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return value if math.isfinite(value) else 0.0


class _UnionFind:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, value: int) -> int:
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = self.parent[value]
        return value

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


def compute_sample_metrics(
    rows: list[dict[str, Any]],
    near_duplicate_threshold: float,
    block_size: int = 256,
) -> dict[str, Any]:
    if len(rows) < 2:
        raise ValueError("at least two sampled rows are required")

    matrix = np.asarray([[_numeric_value(row, field) for field in NUMERIC_FIELDS] for row in rows], dtype=np.float64)
    case_ids = np.asarray([_case_id(row) for row in rows], dtype=object)
    normalized = np.zeros_like(matrix)
    normalization: dict[str, dict[str, dict[str, float]]] = {}
    for case_id in sorted(set(case_ids.tolist())):
        indices = np.where(case_ids == case_id)[0]
        case_matrix = matrix[indices]
        median = np.median(case_matrix, axis=0)
        q25, q75 = np.quantile(case_matrix, [0.25, 0.75], axis=0)
        scale = q75 - q25
        scale[scale <= 1e-12] = 1.0
        normalized[indices] = (case_matrix - median) / scale
        normalization[case_id] = {
            field: {"median": float(center), "iqr": float(width)}
            for field, center, width in zip(NUMERIC_FIELDS, median, scale)
        }

    covariance = np.cov(normalized, rowvar=False)
    eigenvalues = np.maximum(np.linalg.eigvalsh(np.atleast_2d(covariance)), 0.0)
    eig_sum = float(eigenvalues.sum())
    intrinsic_dimension = float(eig_sum**2 / np.square(eigenvalues).sum()) if eig_sum > 0 else 0.0

    topology = np.asarray([str(row.get("topology_class", "unknown")) for row in rows], dtype=object)
    contingency = np.asarray([str(row.get("contingency_order", "unknown")) for row in rows], dtype=object)
    nearest = np.full(len(rows), np.inf, dtype=np.float64)
    clusters = _UnionFind(len(rows))
    divisor = math.sqrt(len(NUMERIC_FIELDS))
    squared_norms = np.sum(normalized * normalized, axis=1)

    for start in range(0, len(rows), block_size):
        stop = min(start + block_size, len(rows))
        squared_distances = (
            squared_norms[start:stop, None]
            + squared_norms[None, :]
            - 2.0 * normalized[start:stop] @ normalized.T
        )
        np.maximum(squared_distances, 0.0, out=squared_distances)
        distances = np.sqrt(squared_distances) / divisor
        compatible = (
            (case_ids[start:stop, None] == case_ids[None, :])
            & (topology[start:stop, None] == topology[None, :])
            & (contingency[start:stop, None] == contingency[None, :])
        )
        distances[~compatible] = np.inf
        for local_index, global_index in enumerate(range(start, stop)):
            distances[local_index, global_index] = np.inf
        nearest[start:stop] = np.min(distances, axis=1)

        close_left, close_right = np.where(distances <= near_duplicate_threshold)
        for left, right in zip(close_left.tolist(), close_right.tolist()):
            global_left = start + left
            if global_left < right:
                clusters.union(global_left, right)

    finite = nearest[np.isfinite(nearest)]
    roots = Counter(clusters.find(index) for index in range(len(rows)))
    effective_count = len(rows) ** 2 / sum(size**2 for size in roots.values())
    exact_descriptor_count = len(
        {
            tuple(_numeric_value(row, field) for field in NUMERIC_FIELDS)
            + (_case_id(row), str(row.get("topology_class", "unknown")), str(row.get("contingency_order", "unknown")))
            for row in rows
        }
    )

    return {
        "sample_size": len(rows),
        "distance_definition": "robust-IQR-scaled RMS Euclidean within matching case/topology class/contingency order",
        "near_duplicate_threshold": near_duplicate_threshold,
        "rows_with_comparable_neighbor": int(finite.size),
        "nearest_neighbor_distance": _quantiles(finite),
        "near_duplicate_count": int(np.sum(finite <= near_duplicate_threshold)),
        "near_duplicate_rate": float(np.mean(finite <= near_duplicate_threshold)) if finite.size else 0.0,
        "exact_descriptor_unique_count": exact_descriptor_count,
        "exact_descriptor_duplicate_rate": 1.0 - exact_descriptor_count / len(rows),
        "similarity_cluster_count": len(roots),
        "largest_similarity_cluster": max(roots.values()),
        "effective_sample_count": float(effective_count),
        "effective_sample_ratio": float(effective_count / len(rows)),
        "intrinsic_dimension_participation_ratio": intrinsic_dimension,
        "normalization_by_case": normalization,
        "nearest_neighbor_distances": [float(value) if math.isfinite(value) else None for value in nearest],
        "cluster_sizes": sorted(roots.values(), reverse=True),
    }


@dataclass(frozen=True)
class AuditConfig:
    sample_size: int = 10_000
    seed: int = 20260924
    near_duplicate_threshold: float = 0.02
    block_size: int = 256


@dataclass
class AuditPartial:
    input_ledger_count: int
    row_count: int
    round_counts: Counter[str]
    categorical: dict[str, Counter[str]]
    topology_contingency: Counter[tuple[str, str]]
    sample_rows: list[dict[str, Any]]


def scan_ledgers(paths: Iterable[Path], config: AuditConfig) -> AuditPartial:
    """Scan one partition without computing any global pairwise metrics."""
    sampler = DeterministicSample(config.sample_size, config.seed)
    categorical = {field: Counter() for field in CATEGORICAL_FIELDS}
    topology_contingency: Counter[tuple[str, str]] = Counter()
    round_counts: Counter[str] = Counter()
    row_count = 0

    input_paths = list(paths)
    for path in input_paths:
        for row in iter_ledger_rows(path):
            row = dict(row)
            row["case_id"] = _case_id(row)
            sampler.add(row)
            row_count += 1
            round_counts[str(row.get("round_index", "unknown"))] += 1
            for field in CATEGORICAL_FIELDS:
                categorical[field][str(row.get(field, "unknown"))] += 1
            topology_contingency[
                (
                    str(row.get("topology_class", "unknown")),
                    str(row.get("contingency_order", "unknown")),
                )
            ] += 1

    return AuditPartial(
        input_ledger_count=len(input_paths),
        row_count=row_count,
        round_counts=round_counts,
        categorical=categorical,
        topology_contingency=topology_contingency,
        sample_rows=sampler.rows(),
    )


def merge_audit_partials(
    partials: Iterable[AuditPartial],
    config: AuditConfig,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Merge serial or distributed scans and compute global sampled metrics."""
    merged_sampler = DeterministicSample(config.sample_size, config.seed)
    categorical = {field: Counter() for field in CATEGORICAL_FIELDS}
    topology_contingency: Counter[tuple[str, str]] = Counter()
    round_counts: Counter[str] = Counter()
    row_count = 0
    input_ledger_count = 0

    for partial in partials:
        input_ledger_count += partial.input_ledger_count
        row_count += partial.row_count
        round_counts.update(partial.round_counts)
        topology_contingency.update(partial.topology_contingency)
        for field in CATEGORICAL_FIELDS:
            categorical[field].update(partial.categorical[field])
        for row in partial.sample_rows:
            merged_sampler.add(row)

    sample_rows = merged_sampler.rows()
    if len(sample_rows) < 2:
        raise ValueError("audit found fewer than two diversity rows")
    sample_metrics = compute_sample_metrics(sample_rows, config.near_duplicate_threshold, config.block_size)

    categorical_metrics = {}
    for field, counts in categorical.items():
        categorical_metrics[field] = {
            "basis": "full_stream",
            "category_count": len(counts),
            "normalized_entropy": normalized_entropy(counts),
            "hill_number_2": hill_number_2(counts),
            "top_categories": [{"value": value, "count": count} for value, count in counts.most_common(20)],
        }
    for field in SAMPLED_CATEGORICAL_FIELDS:
        counts = Counter(str(row.get(field, "unknown")) for row in sample_rows)
        categorical_metrics[field] = {
            "basis": "deterministic_sample",
            "category_count": len(counts),
            "normalized_entropy": normalized_entropy(counts),
            "hill_number_2": hill_number_2(counts),
            "top_categories": [{"value": value, "count": count} for value, count in counts.most_common(20)],
        }

    joint_counts = Counter({f"{topology}|{order}": count for (topology, order), count in topology_contingency.items()})
    topology_values = sorted({topology for topology, _order in topology_contingency})
    contingency_values = sorted({order for _topology, order in topology_contingency})
    topology_contingency_metrics = {
        "basis": "full_stream",
        "topology_classes": topology_values,
        "contingency_orders": contingency_values,
        "occupied_cell_count": len(topology_contingency),
        "possible_cell_count": len(topology_values) * len(contingency_values),
        "normalized_entropy": normalized_entropy(joint_counts),
        "hill_number_2": hill_number_2(joint_counts),
        "cells": [
            {
                "topology_class": topology,
                "contingency_order": order,
                "count": topology_contingency.get((topology, order), 0),
                "corpus_fraction": topology_contingency.get((topology, order), 0) / row_count if row_count else 0.0,
            }
            for topology in topology_values
            for order in contingency_values
        ],
    }

    report = {
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "method": "exact streaming low-cardinality coverage counts plus deterministic bounded global sample",
        "row_count": row_count,
        "input_ledger_count": input_ledger_count,
        "round_counts": dict(sorted(round_counts.items())),
        "config": {
            "sample_size": config.sample_size,
            "seed": config.seed,
            "near_duplicate_threshold": config.near_duplicate_threshold,
            "block_size": config.block_size,
            "numeric_fields": list(NUMERIC_FIELDS),
        },
        "categorical_metrics": categorical_metrics,
        "topology_contingency_metrics": topology_contingency_metrics,
        "sample_metrics": sample_metrics,
        "limitations": [
            "Distance and effective-sample metrics are estimates from a deterministic sample.",
            "This descriptor audit measures solved-state diversity, not canonical full-input identity.",
            "A separate grouped split audit is required to quantify train/test leakage.",
        ],
    }
    return report, sample_rows


def audit_ledgers(paths: Iterable[Path], config: AuditConfig) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Reference single-process audit using the same merge path as MPI."""
    return merge_audit_partials([scan_ledgers(paths, config)], config)
