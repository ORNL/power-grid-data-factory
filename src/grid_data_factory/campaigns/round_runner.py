from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from grid_data_factory.contingencies.apply import contingency_slug
from grid_data_factory.diversity.clustering import adaptive_bin_id
from grid_data_factory.diversity.descriptors import SolvedStateDescriptor
from grid_data_factory.preservation.artifacts import build_artifacts_manifest
from grid_data_factory.preservation.checksums import verify_checksums, write_checksums
from grid_data_factory.sources.registry import resolve_case_file
from grid_data_factory.storage.layout import (
    create_next_attempt_directory,
    finalize_attempt_directory,
    get_solver_directory,
)


def _require_yaml():
    try:
        import yaml  # type: ignore
    except ModuleNotFoundError:
        return None
    return yaml


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _normalize(text: str) -> str:
    t = text.strip().lower().replace(" ", "_")
    return re.sub(r"[^a-z0-9_\-]", "", t)


def _resolve_case_file(repo_root: Path, case_id: str) -> Path:
    return resolve_case_file(repo_root, case_id)


def _quantiles(values: list[float]) -> tuple[float, float, float]:
    if not values:
        return (0.0, 0.0, 0.0)
    vals = sorted(values)

    def pick(q: float) -> float:
        idx = int(round((len(vals) - 1) * q))
        return float(vals[max(0, min(len(vals) - 1, idx))])

    return pick(0.1), pick(0.5), pick(0.9)


def _build_margins(case_data: dict[str, Any], raw_result: dict[str, Any]) -> dict[str, float]:
    margins: dict[str, float] = {}
    solution = ((raw_result.get("raw_result") or {}).get("solution") or {})

    gen_sol = solution.get("gen") or {}
    for i, gen in enumerate(case_data.get("generators", []), start=1):
        gid = str(gen.get("gen_id", f"gen_{i:06d}"))
        s = gen_sol.get(str(i), {})
        pg = float(s.get("pg", 0.0)) * float(case_data.get("base_mva", 100.0))
        qg = float(s.get("qg", 0.0)) * float(case_data.get("base_mva", 100.0))
        pmin, pmax = float(gen["pmin"]), float(gen["pmax"])
        qmin, qmax = float(gen["qmin"]), float(gen["qmax"])
        margins[f"generator_pmin:{gid}"] = (pg - pmin) / max(abs(pmax), 1.0)
        margins[f"generator_pmax:{gid}"] = (pmax - pg) / max(abs(pmax), 1.0)
        margins[f"generator_qmin:{gid}"] = (qg - qmin) / max(abs(qmax), 1.0)
        margins[f"generator_qmax:{gid}"] = (qmax - qg) / max(abs(qmax), 1.0)

    bus_sol = solution.get("bus") or {}
    for i, bus in enumerate(case_data.get("buses", []), start=1):
        bid = str(bus.get("bus_id", i))
        s = bus_sol.get(str(i), {})
        vm = float(s.get("vm", bus.get("vm", 1.0)))
        vmin = float(bus.get("vmin", 0.95))
        vmax = float(bus.get("vmax", 1.05))
        margins[f"voltage_min:{bid}"] = vm - vmin
        margins[f"voltage_max:{bid}"] = vmax - vm

    branch_sol = solution.get("branch") or {}
    for i, br in enumerate(case_data.get("branches", []), start=1):
        brid = str(br.get("branch_id", f"branch_{i:06d}"))
        s = branch_sol.get(str(i), {})
        pf = abs(float(s.get("pf", 0.0))) * float(case_data.get("base_mva", 100.0))
        pt = abs(float(s.get("pt", 0.0))) * float(case_data.get("base_mva", 100.0))
        flow = max(pf, pt)
        rate = max(float(br.get("rate_a", 1.0)), 1.0)
        margins[f"branch_thermal:{brid}"] = (rate - flow) / rate

    return margins


def _descriptor_from_result(candidate: dict[str, Any], case_data: dict[str, Any], result: dict[str, Any], security_margin: float, active_sig: dict[str, int]) -> dict[str, Any]:
    base_mva = float(case_data.get("base_mva", 100.0))
    solution = ((result.get("raw_result") or {}).get("solution") or {})

    total_p = sum(float(x.get("pd", 0.0)) for x in case_data.get("loads", []))
    total_q = sum(float(x.get("qd", 0.0)) for x in case_data.get("loads", []))

    bus_vals = [float(v.get("vm", 1.0)) for v in (solution.get("bus") or {}).values()]
    branch_vals = []
    for v in (solution.get("branch") or {}).values():
        pf = abs(float(v.get("pf", 0.0))) * base_mva
        pt = abs(float(v.get("pt", 0.0))) * base_mva
        branch_vals.append(max(pf, pt))

    gen_p = [abs(float(v.get("pg", 0.0)) * base_mva) for v in (solution.get("gen") or {}).values()]
    gen_q = [abs(float(v.get("qg", 0.0)) * base_mva) for v in (solution.get("gen") or {}).values()]

    v10, v50, v90 = _quantiles(bus_vals)
    b10, b50, b90 = _quantiles(branch_vals)
    p10, p50, p90 = _quantiles(gen_p)
    q10, q50, q90 = _quantiles(gen_q)

    active_keys = sorted([k for k, s in active_sig.items() if s == 2])
    near_keys = sorted([k for k, s in active_sig.items() if s == 1])

    desc = SolvedStateDescriptor(
        candidate_id=str(candidate.get("candidate_id")),
        total_active_load=total_p,
        total_reactive_load=total_q,
        renewable_penetration=float(candidate.get("operating_point_parameters", {}).get("renewable_scale", 1.0)),
        reserve_margin=float(candidate.get("operating_point_parameters", {}).get("reserve_margin", 0.15)),
        voltage_p10=v10,
        voltage_p50=v50,
        voltage_p90=v90,
        branch_loading_p90=b90,
        generator_p_p90=p90,
        generator_q_p90=q90,
        network_losses=0.0,
        active_constraint_signature=";".join(active_keys),
        near_active_constraint_signature=";".join(near_keys),
        active_constraint_count=len(active_keys),
        near_active_constraint_count=len(near_keys),
        contingency_order=int(candidate.get("contingency_order", 0)),
        topology_class=str(candidate.get("topology_class", "baseline")),
        security_margin=security_margin,
    ).to_record()

    desc["descriptor_cluster"] = adaptive_bin_id(desc)
    desc["candidate_generation_mechanism"] = candidate.get("candidate_generation_mechanism")
    desc["run_id"] = result.get("_run_id")
    return desc


def _read_existing_diversity(campaign_root: Path) -> list[dict[str, Any]]:
    parquet = campaign_root / "diversity_ledger.parquet"
    fallback = campaign_root / "diversity_ledger.parquet.jsonl"
    if fallback.exists():
        return _read_jsonl(fallback)
    try:
        import pandas as pd  # type: ignore
    except ModuleNotFoundError:
        return []
    if parquet.exists():
        return pd.read_parquet(parquet).to_dict(orient="records")
    return []


def _candidate_identity(candidate: dict[str, Any]) -> tuple[str, str, str, str]:
    case_id = str(candidate.get("case_id"))
    regime = _normalize(str(candidate.get("operating_regime", "baseline")))
    cid = str(candidate.get("candidate_id", "c0"))
    m = re.search(r"::op::(\d+)", cid)
    op_index = int(m.group(1)) if m else 0
    operating_point_id = f"op_{op_index:06d}_{regime}"
    topology_id = str(candidate.get("topology_id") or "topology_000000_baseline")
    contingency_id = contingency_slug(candidate.get("contingency"))
    return case_id, topology_id, operating_point_id, contingency_id


def _candidate_solver_dir(runs_root: Path, candidate: dict[str, Any], solver_id: str) -> Path:
    case_id, topology_id, operating_point_id, contingency_id = _candidate_identity(candidate)
    return get_solver_directory(
        runs_root=runs_root,
        task="ac_opf",
        case_id=case_id,
        topology_id=topology_id,
        operating_point_id=operating_point_id,
        solver_id=solver_id,
        contingency_set_id=contingency_id,
    )


def _write_attempt(
    repo_root: Path,
    runs_root: Path,
    candidate: dict[str, Any],
    case_data: dict[str, Any],
    result: dict[str, Any],
    solver_id: str,
) -> tuple[Path, str]:
    case_id, topology_id, operating_point_id, contingency_id = _candidate_identity(candidate)

    solver_dir = get_solver_directory(
        runs_root=runs_root,
        task="ac_opf",
        case_id=case_id,
        topology_id=topology_id,
        operating_point_id=operating_point_id,
        solver_id=solver_id,
        contingency_set_id=contingency_id,
    )
    in_progress, attempt_id = create_next_attempt_directory(solver_dir)

    run_id = f"{case_id}-{topology_id}-{operating_point_id}-{contingency_id}-{solver_id}-{attempt_id}"
    run_yaml = {
        "run_id": run_id,
        "task": "ac_opf",
        "case_id": case_id,
        "topology_id": topology_id,
        "operating_point_id": operating_point_id,
        "contingency_set_id": contingency_id,
        "solver_id": solver_id,
        "attempt_id": attempt_id,
        "numerical_status": str(result.get("termination_status", "unknown")),
        "preservation_status": "in_progress",
    }

    runtime_meta = result.get("runtime_metadata") or {}
    exec_ctx = runtime_meta.get("execution_context") or {}
    wallclock_seconds = runtime_meta.get("wallclock_seconds", result.get("solve_time", result.get("runtime")))

    (in_progress / "run.yaml").write_text("\n".join(f"{k}: {v}" for k, v in run_yaml.items()) + "\n", encoding="utf-8")
    (in_progress / "inputs" / "resolved_case.json").write_text(json.dumps(case_data, indent=2), encoding="utf-8")
    (in_progress / "inputs" / "candidate.json").write_text(json.dumps(candidate, indent=2), encoding="utf-8")
    (in_progress / "raw_outputs" / "solver_result" / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    (in_progress / "timing" / "runtime_metadata.json").write_text(json.dumps(runtime_meta, indent=2), encoding="utf-8")
    (in_progress / "logs" / "stdout.log").write_text(str(result.get("stdout", "")), encoding="utf-8")
    (in_progress / "logs" / "stderr.log").write_text(str(result.get("stderr", "")), encoding="utf-8")
    (in_progress / "logs" / "combined.log").write_text(
        f"termination_status={result.get('termination_status')}\n"
        f"success={result.get('success')}\n"
        f"objective={result.get('objective')}\n",
        encoding="utf-8",
    )
    (in_progress / "logs" / "combined.log").write_text(
        (in_progress / "logs" / "combined.log").read_text(encoding="utf-8")
        + f"wallclock_seconds={wallclock_seconds}\n"
        + f"mpi_processes={exec_ctx.get('mpi_processes')}\n"
        + f"gpu_enabled={exec_ctx.get('gpu_enabled')}\n"
        + f"gpu_type={exec_ctx.get('gpu_type')}\n",
        encoding="utf-8",
    )

    build_artifacts_manifest(in_progress)
    write_checksums(in_progress)
    marker = "SUCCESS" if bool(result.get("success", False)) else "NONCONVERGENT"
    (in_progress / marker).write_text("", encoding="utf-8")
    final_dir = finalize_attempt_directory(in_progress)

    ok, errors = verify_checksums(final_dir)
    if not ok:
        raise RuntimeError(f"Checksum verification failed for {final_dir}: {errors}")

    return final_dir, run_id


# ---------------------------------------------------------------------------
# Lean shard-aggregated writer (Lustre-friendly training output).
#
# The per-attempt directory tree above creates ~40 inodes per candidate (~28
# dirs + ~11 files), which overwhelms the Lustre metadata server at campaign
# scale. The functions below instead append one self-contained, training-ready
# JSON record per solve to a single per-shard ``samples.jsonl`` (~1 file per
# shard). This folds Tier 1 (no empty dirs), Tier 2 (one aggregated record) and
# Tier 3 (shard-level container) into a single path used by the campaign map
# scripts. Appends are line-flushed so a walltime-killed job leaves at most one
# truncated trailing line, which readers skip; resume is by candidate id.
# ---------------------------------------------------------------------------

SAMPLE_SCHEMA_VERSION = "1.0"


def _shard_samples_path(runs_root: Path) -> Path:
    return runs_root / "ac_opf" / "samples.jsonl"


def _lean_result(result: dict[str, Any]) -> dict[str, Any]:
    trimmed = dict(result)
    trimmed.pop("stdout", None)
    trimmed.pop("stderr", None)
    return trimmed


def _sample_record(
    candidate: dict[str, Any],
    case_data: dict[str, Any],
    result: dict[str, Any],
    solver_id: str,
    run_id: str,
) -> dict[str, Any]:
    case_id, topology_id, operating_point_id, contingency_id = _candidate_identity(candidate)
    runtime_meta = result.get("runtime_metadata") or {}
    wallclock_seconds = runtime_meta.get("wallclock_seconds", result.get("solve_time", result.get("runtime")))
    return {
        "schema_version": SAMPLE_SCHEMA_VERSION,
        "run_id": run_id,
        "candidate_id": str(candidate.get("candidate_id")),
        "task": "ac_opf",
        "case_id": case_id,
        "topology_id": topology_id,
        "operating_point_id": operating_point_id,
        "contingency_set_id": contingency_id,
        "solver_id": solver_id,
        "success": bool(result.get("success", False)),
        "termination_status": str(result.get("termination_status", "unknown")),
        "objective": result.get("objective"),
        "solve_time": result.get("solve_time", result.get("runtime")),
        "wallclock_seconds": wallclock_seconds,
        "inputs": {"resolved_case": case_data, "candidate": candidate},
        "result": _lean_result(result),
        "runtime_metadata": runtime_meta,
    }


def _append_sample(
    repo_root: Path,
    runs_root: Path,
    candidate: dict[str, Any],
    case_data: dict[str, Any],
    result: dict[str, Any],
    solver_id: str,
) -> tuple[Path, str]:
    case_id, topology_id, operating_point_id, contingency_id = _candidate_identity(candidate)
    run_id = f"{case_id}-{topology_id}-{operating_point_id}-{contingency_id}-{solver_id}"
    record = _sample_record(candidate, case_data, result, solver_id, run_id)
    samples_path = _shard_samples_path(runs_root)
    samples_path.parent.mkdir(parents=True, exist_ok=True)
    with samples_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")
        fh.flush()
    return samples_path, run_id


class SampleSink:
    """Keep a shard's ``samples.jsonl`` handle open for the shard's lifetime.

    ``_append_sample`` reopens the file (``open(append)+write+close``) once per
    solved case, i.e. ~15M MDS round-trips/round on Lustre. Holding a single
    handle collapses that to one ``open`` per shard. We flush every
    ``flush_every`` records so a walltime-killed job loses at most that many
    un-flushed lines; ``_loaded_sample_ids`` already tolerates a truncated
    trailing line, so resume stays correct.
    """

    def __init__(self, runs_root: Path, solver_id: str, flush_every: int = 200) -> None:
        self.path = _shard_samples_path(runs_root)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._solver_id = solver_id
        self._flush_every = max(1, int(flush_every))
        self._since_flush = 0
        self._fh = self.path.open("a", encoding="utf-8")

    def append(self, candidate: dict[str, Any], case_data: dict[str, Any], result: dict[str, Any]) -> tuple[Path, str]:
        case_id, topology_id, operating_point_id, contingency_id = _candidate_identity(candidate)
        run_id = f"{case_id}-{topology_id}-{operating_point_id}-{contingency_id}-{self._solver_id}"
        record = _sample_record(candidate, case_data, result, self._solver_id, run_id)
        self._fh.write(json.dumps(record) + "\n")
        self._since_flush += 1
        if self._since_flush >= self._flush_every:
            self._fh.flush()
            self._since_flush = 0
        return self.path, run_id

    def close(self) -> None:
        if self._fh is not None and not self._fh.closed:
            self._fh.flush()
            self._fh.close()

    def __enter__(self) -> "SampleSink":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def _loaded_sample_ids(runs_root: Path) -> set[str]:
    samples_path = _shard_samples_path(runs_root)
    done: set[str] = set()
    if not samples_path.exists():
        return done
    with samples_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue  # tolerate a truncated trailing line from a walltime-killed job
            cid = rec.get("candidate_id")
            if cid is not None:
                done.add(str(cid))
    return done


def _count_samples(samples_path: Path) -> int:
    if not samples_path.exists():
        return 0
    count = 0
    with samples_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                count += 1
    return count


def _write_shard_manifest(runs_root: Path, report: dict[str, Any]) -> Path:
    samples_path = _shard_samples_path(runs_root)
    manifest = {
        "schema_version": SAMPLE_SCHEMA_VERSION,
        "samples_file": samples_path.name,
        "sample_count": _count_samples(samples_path),
        **report,
    }
    out = runs_root / "ac_opf" / "shard_manifest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return out


def _load_bands(repo_root: Path, config_path: str) -> dict[str, dict[str, float]]:
    p = (repo_root / config_path).resolve()
    yaml = _require_yaml()
    if yaml is None:
        return {
            "comfortably_secure": {"minimum_margin": 0.10},
            "moderately_secure": {"minimum_margin": 0.03, "maximum_margin": 0.10},
            "near_boundary_secure": {"minimum_margin": 0.0, "maximum_margin": 0.03},
            "near_boundary_insecure": {"minimum_margin": -0.03, "maximum_margin": 0.0},
            "severely_insecure": {"maximum_margin": -0.03},
        }
    cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return cfg.get("security_margin_bands") or {}
