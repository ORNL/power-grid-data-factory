from __future__ import annotations

import os
import socket
import subprocess
from typing import Mapping


def _first_nonempty_env(env: Mapping[str, str], keys: list[str]) -> str:
    for key in keys:
        value = str(env.get(key, "")).strip()
        if value:
            return value
    return ""


def _safe_int(value: str) -> int | None:
    try:
        out = int(value)
    except Exception:  # noqa: BLE001
        return None
    return out if out > 0 else None


def detect_mpi_processes(env: Mapping[str, str] | None = None) -> int:
    context = dict(os.environ) if env is None else dict(env)
    raw = _first_nonempty_env(
        context,
        [
            "OMPI_COMM_WORLD_SIZE",
            "PMI_SIZE",
            "SLURM_NTASKS",
            "MPI_LOCALNRANKS",
            "WORLD_SIZE",
            "MPIRUN_NP",
        ],
    )
    parsed = _safe_int(raw)
    return parsed if parsed is not None else 1


def _probe_gpu_type() -> tuple[str | None, str | None]:
    probes = [
        (["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], "cuda"),
        (["rocm-smi", "--showproductname"], "rocm"),
    ]
    for cmd, backend in probes:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=2.0)
        except Exception:  # noqa: BLE001
            continue
        if proc.returncode != 0:
            continue
        lines = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
        if not lines:
            continue
        if backend == "cuda":
            return lines[0], backend
        for line in lines:
            low = line.lower()
            if "card series" in low or "gpu" in low:
                return line, backend
        return lines[0], backend
    return None, None


def detect_gpu_context(env: Mapping[str, str] | None = None) -> dict[str, object]:
    context = dict(os.environ) if env is None else dict(env)

    visible_devices = _first_nonempty_env(context, ["CUDA_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES", "HIP_VISIBLE_DEVICES"])
    forced_type = _first_nonempty_env(context, ["PGDF_GPU_TYPE", "CRAY_ACCEL_TARGET", "GPU_TYPE"])

    gpu_enabled = bool(visible_devices) and visible_devices.lower() not in {"-1", "none", ""}
    gpu_type: str | None = forced_type or None
    gpu_backend: str | None = None

    if gpu_type is None and gpu_enabled:
        probed_type, probed_backend = _probe_gpu_type()
        gpu_type = probed_type
        gpu_backend = probed_backend

    if gpu_backend is None:
        if _first_nonempty_env(context, ["ROCR_VISIBLE_DEVICES", "HIP_VISIBLE_DEVICES", "HSA_OVERRIDE_GFX_VERSION"]):
            gpu_backend = "rocm"
        elif _first_nonempty_env(context, ["CUDA_VISIBLE_DEVICES", "NVIDIA_VISIBLE_DEVICES"]):
            gpu_backend = "cuda"

    return {
        "gpu_enabled": gpu_enabled,
        "gpu_type": gpu_type,
        "gpu_backend": gpu_backend,
        "gpu_visible_devices": visible_devices or None,
    }


def collect_execution_context(env: Mapping[str, str] | None = None) -> dict[str, object]:
    context = dict(os.environ) if env is None else dict(env)
    gpu = detect_gpu_context(context)
    return {
        "hostname": socket.gethostname(),
        "mpi_processes": detect_mpi_processes(context),
        **gpu,
    }
