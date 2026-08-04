from __future__ import annotations

import shutil
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


def scan_max_attempt_index(solver_directory: Path) -> int:
    attempts = solver_directory / "attempts"
    if not attempts.exists():
        return 0
    max_idx = 0
    for p in attempts.iterdir():
        name = p.name
        if name.startswith(".attempt_") and name.endswith(".in_progress"):
            core = name[len(".attempt_") : -len(".in_progress")]
        elif name.startswith("attempt_"):
            core = name[len("attempt_") :]
        else:
            continue
        if core.isdigit():
            max_idx = max(max_idx, int(core))
    return max_idx


def create_next_attempt_directory(solver_directory: Path, max_attempts: int = 100000) -> tuple[Path, str]:
    """Atomically claim the next free attempt directory, retrying on races.

    Safe when multiple processes share a runs-root: the atomic ``mkdir`` of the
    in-progress marker breaks ties, and a lost race simply advances to the next index.
    Returns the in-progress directory and its attempt id.
    """
    from .naming import format_attempt_id

    attempts = solver_directory / "attempts"
    index = scan_max_attempt_index(solver_directory) + 1
    last_exc: Exception | None = None
    for _ in range(max_attempts):
        attempt_id = format_attempt_id(index)
        final_dir = attempts / attempt_id
        if final_dir.exists() or (attempts / f".{attempt_id}.in_progress").exists():
            index += 1
            continue
        try:
            in_progress = create_attempt_directory(solver_directory, attempt_id)
        except FileExistsError as exc:
            last_exc = exc
            index += 1
            continue
        # Close the finalize-name race: another process may have finalized this
        # index in the window between our existence check and claiming the marker.
        # Only the marker holder can create the finalized dir, so once we hold the
        # marker and observe no finalized dir, the index is exclusively ours.
        if final_dir.exists():
            shutil.rmtree(in_progress, ignore_errors=True)
            index += 1
            continue
        return in_progress, attempt_id
    raise RuntimeError(f"Could not allocate an attempt directory under {attempts} after {max_attempts} tries") from last_exc

