from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

import pyarrow as pa
import pyarrow.parquet as pq


ANCHOR_SCHEMA_VERSION = "1.0"
_ANCHOR_SCHEMA = pa.schema(
    [
        ("schema_version", pa.string()),
        ("source_ac_opf_run_id", pa.string()),
        ("candidate_id", pa.string()),
        ("case_id", pa.string()),
        ("parent_network_id", pa.string()),
        ("parent_topology_id", pa.string()),
        ("topology_id", pa.string()),
        ("topology_hash", pa.string()),
        ("dataset_split", pa.string()),
        ("objective", pa.float64()),
        ("source_file", pa.string()),
        ("source_file_sha256", pa.string()),
        ("controls_json", pa.string()),
        ("resolved_case_json", pa.string()),
        ("candidate_json", pa.string()),
        ("solution_json", pa.string()),
    ]
)


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def topology_hash(case_data: dict[str, Any]) -> str:
    topology = {
        "buses": sorted(str(bus["bus_id"]) for bus in case_data.get("buses", [])),
        "branches": sorted(
            (
                str(branch["branch_id"]),
                str(branch["from"]),
                str(branch["to"]),
                int(branch.get("status", 1)),
                float(branch.get("tap", 0.0)),
                float(branch.get("shift", 0.0)),
            )
            for branch in case_data.get("branches", [])
        ),
        "generators": sorted(
            (str(gen["gen_id"]), str(gen["bus_id"]), int(gen.get("status", 1)))
            for gen in case_data.get("generators", [])
        ),
    }
    return _canonical_hash(topology)


def dataset_split(parent_network_id: str) -> str:
    bucket = int(hashlib.sha256(parent_network_id.encode("utf-8")).hexdigest()[:8], 16) % 100
    if bucket < 70:
        return "train"
    if bucket < 80:
        return "validation"
    if bucket < 90:
        return "test"
    return "ood"


def _anchor_from_sample(sample: dict[str, Any], source_file: Path, source_sha256: str) -> dict[str, Any] | None:
    if sample.get("task") != "ac_opf" or not sample.get("success"):
        return None
    inputs = sample.get("inputs") or {}
    case_data = inputs.get("resolved_case") or {}
    candidate = inputs.get("candidate") or {}
    solution = (((sample.get("result") or {}).get("raw_result") or {}).get("solution") or {})
    gen_solution = solution.get("gen") or {}
    bus_solution = solution.get("bus") or {}
    base_mva = float(case_data.get("base_mva", 100.0))
    controls: dict[str, dict[str, float]] = {}
    for index, gen in enumerate(case_data.get("generators", []), start=1):
        solved = gen_solution.get(str(index)) or {}
        bus_index = next(
            (i for i, bus in enumerate(case_data.get("buses", []), start=1) if str(bus["bus_id"]) == str(gen["bus_id"])),
            None,
        )
        solved_bus = bus_solution.get(str(bus_index)) if bus_index is not None else {}
        controls[str(gen["gen_id"])] = {
            "pg": float(solved.get("pg", gen.get("pg", 0.0))) * (base_mva if "pg" in solved else 1.0),
            "qg": float(solved.get("qg", gen.get("qg", 0.0))) * (base_mva if "qg" in solved else 1.0),
            "vg": float(solved.get("vg", (solved_bus or {}).get("vm", gen.get("vg", 1.0)))),
        }
    parent_network_id = str(candidate.get("parent_network_id") or sample.get("case_id"))
    parent_topology_id = str(candidate.get("parent_topology_id") or sample.get("topology_id"))
    return {
        "schema_version": ANCHOR_SCHEMA_VERSION,
        "source_ac_opf_run_id": str(sample["run_id"]),
        "candidate_id": str(sample.get("candidate_id", "")),
        "case_id": str(sample["case_id"]),
        "parent_network_id": parent_network_id,
        "parent_topology_id": parent_topology_id,
        "topology_id": str(sample.get("topology_id", parent_topology_id)),
        "topology_hash": topology_hash(case_data),
        "dataset_split": dataset_split(parent_network_id),
        "objective": float(sample["objective"]) if sample.get("objective") is not None else None,
        "source_file": str(source_file),
        "source_file_sha256": source_sha256,
        "controls_json": json.dumps({"generators": controls}, sort_keys=True),
        "resolved_case_json": json.dumps(case_data, sort_keys=True),
        "candidate_json": json.dumps(candidate, sort_keys=True),
        "solution_json": json.dumps(solution, sort_keys=True),
    }


def iter_samples(paths: Iterable[Path]) -> Iterable[tuple[dict[str, Any], Path]]:
    for path in paths:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    yield json.loads(line), path
                except json.JSONDecodeError:
                    continue


def build_anchor_index(sample_paths: Iterable[Path], output_path: Path, batch_size: int = 10_000) -> dict[str, Any]:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    paths = sorted({Path(path).resolve() for path in sample_paths})
    source_hashes = {path: _file_sha256(path) for path in paths}
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(output_path.suffix + ".in_progress")
    writer = pq.ParquetWriter(temporary_path, _ANCHOR_SCHEMA, compression="zstd")
    rows: list[dict[str, Any]] = []
    anchor_count = 0
    try:
        for sample, path in iter_samples(paths):
            anchor = _anchor_from_sample(sample, path, source_hashes[path])
            if anchor is None:
                continue
            rows.append(anchor)
            if len(rows) >= batch_size:
                writer.write_table(pa.Table.from_pylist(rows, schema=_ANCHOR_SCHEMA))
                anchor_count += len(rows)
                rows.clear()
        if rows:
            writer.write_table(pa.Table.from_pylist(rows, schema=_ANCHOR_SCHEMA))
            anchor_count += len(rows)
    finally:
        writer.close()
    temporary_path.replace(output_path)
    split_rows: dict[str, dict[str, str]] = {}
    for batch in pq.ParquetFile(output_path).iter_batches(columns=["parent_network_id", "dataset_split"]):
        for row in batch.to_pylist():
            split_rows[str(row["parent_network_id"])] = {
                "parent_network_id": str(row["parent_network_id"]),
                "dataset_split": str(row["dataset_split"]),
            }
    split_path = output_path.with_name("parent_split_registry.parquet")
    split_temp = split_path.with_suffix(split_path.suffix + ".in_progress")
    pq.write_table(pa.Table.from_pylist([split_rows[key] for key in sorted(split_rows)]), split_temp, compression="zstd")
    split_temp.replace(split_path)
    manifest = {
        "schema_version": ANCHOR_SCHEMA_VERSION,
        "anchor_count": anchor_count,
        "source_files": [str(path) for path in paths],
        "source_checksums": {str(path): source_hashes[path] for path in paths},
        "index_sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
        "parent_split_registry": str(split_path),
        "parent_split_registry_sha256": _file_sha256(split_path),
    }
    output_path.with_suffix(output_path.suffix + ".manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest