from __future__ import annotations

from pathlib import Path

TASKS = {"pf", "dc_opf", "ac_opf", "scopf"}


def _task_root(runs_root: Path, task: str) -> Path:
    if task not in TASKS:
        raise ValueError(f"Unsupported task: {task}")
    return runs_root / task


def get_case_directory(runs_root: Path, task: str, case_id: str) -> Path:
    return _task_root(runs_root, task) / case_id


def get_topology_directory(runs_root: Path, task: str, case_id: str, topology_id: str) -> Path:
    return get_case_directory(runs_root, task, case_id) / topology_id


def get_operating_point_directory(
    runs_root: Path,
    task: str,
    case_id: str,
    topology_id: str,
    operating_point_id: str,
) -> Path:
    return get_topology_directory(runs_root, task, case_id, topology_id) / operating_point_id


def get_contingency_set_directory(
    runs_root: Path,
    case_id: str,
    topology_id: str,
    operating_point_id: str,
    contingency_set_id: str,
) -> Path:
    return get_operating_point_directory(runs_root, "scopf", case_id, topology_id, operating_point_id) / contingency_set_id


def get_solver_directory(
    runs_root: Path,
    task: str,
    case_id: str,
    topology_id: str,
    operating_point_id: str,
    solver_id: str,
    contingency_set_id: str | None = None,
) -> Path:
    if task == "scopf":
        if not contingency_set_id:
            raise ValueError("contingency_set_id is required for scopf")
        base = get_contingency_set_directory(runs_root, case_id, topology_id, operating_point_id, contingency_set_id)
    else:
        base = get_operating_point_directory(runs_root, task, case_id, topology_id, operating_point_id)
    return base / solver_id


def create_attempt_directory(solver_directory: Path, attempt_id: str) -> Path:
    attempts = solver_directory / "attempts"
    in_progress = attempts / f".{attempt_id}.in_progress"
    if in_progress.exists():
        raise FileExistsError(f"Attempt already in progress: {in_progress}")
    in_progress.mkdir(parents=True, exist_ok=False)

    for rel in [
        "environment",
        "inputs",
        "logs",
        "raw_outputs/solver_result",
        "raw_outputs/solver_native_files",
        "raw_outputs/solution_files",
        "raw_outputs/diagnostics",
        "raw_outputs/checkpoints",
        "raw_outputs/traces",
        "raw_outputs/contingencies",
        "raw_outputs/other",
        "intermediate/parsed_case",
        "intermediate/model_build",
        "intermediate/warm_start",
        "intermediate/screening",
        "intermediate/matrix_exports",
        "intermediate/contingency_results",
        "intermediate/temporary_preserved",
        "normalized",
        "validation",
        "timing",
        "manifests",
        "derived",
        "work/tmp",
        "work/home",
        "work/cache",
    ]:
        (in_progress / rel).mkdir(parents=True, exist_ok=True)
    return in_progress


def finalize_attempt_directory(in_progress_dir: Path) -> Path:
    if not in_progress_dir.name.endswith(".in_progress"):
        raise ValueError("Expected in-progress attempt directory")
    final_name = in_progress_dir.name.removeprefix(".").removesuffix(".in_progress")
    final_dir = in_progress_dir.parent / final_name
    in_progress_dir.rename(final_dir)
    return final_dir
