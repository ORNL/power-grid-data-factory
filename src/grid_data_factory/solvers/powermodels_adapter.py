from __future__ import annotations

import json
import os
import re
import select
import shlex
import socket
import subprocess
import tempfile
import time
from pathlib import Path

from grid_data_factory.runtime_metadata import collect_execution_context


class PersistentPowerModelsSession:
    def __init__(self, adapter: "PowerModelsAdapter", options: dict | None = None):
        self.adapter = adapter
        self.options = dict(options or {})
        self.timeout_s = float(self.options.get("timeout_s", 1200.0))
        self.process: subprocess.Popen[str] | None = None
        self._stderr = None

    def _environment(self) -> dict[str, str]:
        env = os.environ.copy()
        if self.adapter.depot_path:
            env["JULIA_DEPOT_PATH"] = self.adapter.depot_path
        env.setdefault("OPENBLAS_NUM_THREADS", "1")
        env.setdefault("JULIA_NUM_THREADS", "1")
        env.setdefault("JULIA_PKG_PRECOMPILE_AUTO", "0")
        return env

    def _start(self) -> None:
        script_name = str(self.options.get("julia_script") or os.environ.get("PGDF_OPF_SCRIPT", "").strip() or "run_opf.jl")
        script = self.adapter.julia_scripts_dir / script_name
        if script_name != "run_opf.jl":
            raise ValueError(f"persistent PowerModels mode does not support {script_name}")
        if not script.exists():
            raise FileNotFoundError(script)

        cmd = [
            "julia",
            f"--project={self.adapter.julia_project_dir}",
            *self.adapter._julia_mode_flags(),
            str(script),
            "--server",
        ]
        shell_cmd = f"module load julia 2>/dev/null || true; exec {' '.join(shlex.quote(c) for c in cmd)}"
        self._stderr = tempfile.TemporaryFile(mode="w+t", encoding="utf-8")
        self.process = subprocess.Popen(
            ["bash", "-lc", shell_cmd],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr,
            text=True,
            bufsize=1,
            env=self._environment(),
        )

    def _error_text(self) -> str:
        if self._stderr is None:
            return ""
        self._stderr.flush()
        self._stderr.seek(0)
        return self._stderr.read()

    def solve_ac_opf(self, case: dict) -> dict:
        start_t = time.perf_counter()
        exec_ctx = collect_execution_context()

        def finalize(result: dict) -> dict:
            out = dict(result)
            runtime = {
                "wallclock_seconds": round(time.perf_counter() - start_t, 6),
                "execution_context": exec_ctx,
                "persistent_julia": True,
            }
            out["runtime_metadata"] = runtime
            out.setdefault("runtime", runtime["wallclock_seconds"])
            out.setdefault("solve_time", runtime["wallclock_seconds"])
            return out

        if self.process is None or self.process.poll() is not None:
            self.close()
            try:
                self._start()
            except Exception as exc:  # noqa: BLE001
                return finalize({"success": False, "termination_status": "process_error", "solver_name": "powermodels", "stderr": str(exc)})

        assert self.process is not None and self.process.stdin is not None and self.process.stdout is not None
        request = {"case": case, "payload": {"task": "ac_opf", "options": self.options}}
        try:
            self.process.stdin.write(json.dumps(request) + "\n")
            self.process.stdin.flush()
            deadline = time.monotonic() + self.timeout_s
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self.close()
                    return finalize({"success": False, "termination_status": "timeout", "solver_name": "powermodels"})
                ready, _, _ = select.select([self.process.stdout], [], [], remaining)
                if not ready:
                    self.close()
                    return finalize({"success": False, "termination_status": "timeout", "solver_name": "powermodels"})
                line = self.process.stdout.readline()
                if not line:
                    stderr = self._error_text()
                    self.close()
                    return finalize({"success": False, "termination_status": "process_error", "solver_name": "powermodels", "stderr": stderr})
                if line.startswith("PGDF_RESULT\t"):
                    return finalize(json.loads(line.removeprefix("PGDF_RESULT\t")))
        except Exception as exc:  # noqa: BLE001
            stderr = self._error_text()
            self.close()
            return finalize({"success": False, "termination_status": "process_error", "solver_name": "powermodels", "stderr": f"{exc}\n{stderr}"})

    def close(self) -> None:
        if self.process is not None:
            if self.process.stdin is not None:
                try:
                    self.process.stdin.close()
                except OSError:
                    pass
            if self.process.poll() is None:
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait()
            if self.process.stdout is not None:
                self.process.stdout.close()
            self.process = None
        if self._stderr is not None:
            self._stderr.close()
            self._stderr = None

    def __enter__(self) -> "PersistentPowerModelsSession":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class PowerModelsAdapter:
    def __init__(
        self,
        julia_project_dir: Path | None = None,
        depot_path: str | None = None,
        repo_root: Path | None = None,
        julia_scripts_dir: Path | None = None,
    ):
        if julia_project_dir is None:
            if repo_root is None:
                raise ValueError("repo_root is required when julia_project_dir is not provided")
            julia_project_dir = self.resolve_julia_project_dir(Path(repo_root))
        self.julia_project_dir = Path(julia_project_dir)
        if julia_scripts_dir is not None:
            self.julia_scripts_dir = Path(julia_scripts_dir)
        elif repo_root is not None:
            self.julia_scripts_dir = Path(repo_root) / "julia"
        else:
            self.julia_scripts_dir = self.julia_project_dir
        self.depot_path = depot_path
        self.repo_root = Path(repo_root) if repo_root is not None else None
        self.sysimage_path = self.resolve_julia_sysimage(self.repo_root)

    @staticmethod
    def resolve_julia_project_dir(repo_root: Path) -> Path:
        # Explicit override always wins.
        override = os.environ.get("PGDF_JULIA_PROJECT_DIR", "").strip()
        if override:
            p = Path(override)
            return p if p.is_absolute() else (repo_root / p).resolve()

        host = socket.gethostname().lower()
        candidates: list[Path] = []
        if "andes" in host:
            candidates.append(repo_root / "julia" / "lockfiles" / "andes")
        elif "frontier" in host:
            candidates.append(repo_root / "julia" / "lockfiles" / "frontier")

        candidates.append(repo_root / "julia" / "lockfiles" / "local")
        candidates.append(repo_root / "julia")

        for candidate in candidates:
            if (candidate / "Project.toml").exists():
                return candidate

        return repo_root / "julia"

    @staticmethod
    def resolve_julia_sysimage(repo_root: Path | None) -> Path | None:
        # Explicit override always wins; ignored if the file is missing.
        override = os.environ.get("PGDF_JULIA_SYSIMAGE", "").strip()
        if override:
            p = Path(override)
            return p if p.exists() else None
        if repo_root is None:
            return None

        host = socket.gethostname().lower()
        names: list[str] = []
        if "andes" in host:
            names.append("andes")
        elif "frontier" in host:
            names.append("frontier")
        names.append("local")

        for name in names:
            candidate = Path(repo_root) / "julia" / "sysimages" / name / "pgdf_sysimage.so"
            if candidate.exists():
                return candidate
        return None

    def _julia_mode_flags(self) -> list[str]:
        # A prebuilt sysimage already contains the compiled solve path, so it
        # replaces the (slow) --compiled-modules=no recompile-every-run mode.
        if self.sysimage_path is not None:
            return [f"--sysimage={self.sysimage_path}"]
        return ["--compiled-modules=no"]

    def solve_pf(self, case: dict, controls: dict | None = None, options: dict | None = None) -> dict:
        return self._run_julia_script("run_pf.jl", case, {"controls": controls or {}, "options": options or {}})

    def solve_dc_opf(self, case: dict, options: dict | None = None) -> dict:
        return self._run_julia_script("run_opf.jl", case, {"task": "dc_opf", "options": options or {}})

    def solve_relaxed_opf(self, case: dict, formulation: str, options: dict | None = None) -> dict:
        return self._run_julia_script("run_opf.jl", case, {"task": "relaxed_opf", "formulation": formulation, "options": options or {}})

    def solve_ac_opf(self, case: dict, options: dict | None = None) -> dict:
        # Default runner is run_opf.jl. Network-expansion campaigns can opt into
        # the transformer-aware fork by exporting PGDF_OPF_SCRIPT=run_opf_expansion.jl
        # (or by passing options["julia_script"]); the default is unchanged so
        # existing campaigns are unaffected.
        opts = options or {}
        script = str(opts.get("julia_script") or os.environ.get("PGDF_OPF_SCRIPT", "").strip() or "run_opf.jl")
        return self._run_julia_script(script, case, {"task": "ac_opf", "options": opts})

    def persistent_ac_opf_session(self, options: dict | None = None) -> PersistentPowerModelsSession:
        return PersistentPowerModelsSession(self, options)

    def solve_contingency_pf(self, case: dict, contingency: dict, controls: dict | None = None, options: dict | None = None) -> dict:
        return self._run_julia_script(
            "run_pf.jl",
            case,
            {"contingency": contingency, "controls": controls or {}, "options": options or {}},
        )

    def solve_corrective_ac_opf(self, case: dict, contingency: dict, options: dict | None = None) -> dict:
        return self._run_julia_script("run_opf.jl", case, {"task": "corrective_ac_opf", "contingency": contingency, "options": options or {}})

    def solve_scopf(self, case: dict, contingencies: list[dict], options: dict | None = None) -> dict:
        return self._run_julia_script("run_batch.jl", case, {"task": "scopf", "contingencies": contingencies, "options": options or {}})

    @staticmethod
    def _extract_missing_packages(text: str) -> list[str]:
        patterns = [
            r"Package\s+([A-Za-z0-9_]+)\s+\[[^\]]+\]\s+is required but does not seem to be installed",
            r"expected package\s+`([A-Za-z0-9_]+)\s+\[[^\]]+\]`\s+to be registered",
        ]
        pkgs: set[str] = set()
        for pattern in patterns:
            for match in re.findall(pattern, text):
                pkgs.add(match)
        return sorted(pkgs)

    def _run_preflight(self, env: dict[str, str], timeout_s: float | None) -> dict | None:
        preflight_timeout = 180.0 if timeout_s is None else max(60.0, min(timeout_s, 300.0))
        preflight_cmd = [
            "julia",
            f"--project={self.julia_project_dir}",
            *self._julia_mode_flags(),
            "-e",
            "import JSON3, PowerModels, Ipopt; println(\"PREFLIGHT_OK\")",
        ]
        cmd_display = " ".join(shlex.quote(c) for c in preflight_cmd)
        shell_cmd = f"module load julia 2>/dev/null || true; {cmd_display}"

        try:
            proc = subprocess.run(
                ["bash", "-lc", shell_cmd],
                capture_output=True,
                text=True,
                env=env,
                timeout=preflight_timeout,
            )
        except subprocess.TimeoutExpired as exc:
            return {
                "success": False,
                "termination_status": "preflight_timeout",
                "solver_name": "powermodels",
                "stdout": exc.stdout,
                "stderr": exc.stderr,
                "note": "Julia dependency preflight timed out before solve attempt.",
            }

        if proc.returncode == 0:
            return None

        combined = f"{proc.stdout or ''}\n{proc.stderr or ''}"
        missing_packages = self._extract_missing_packages(combined)
        if missing_packages:
            return {
                "success": False,
                "termination_status": "missing_deps",
                "solver_name": "powermodels",
                "missing_packages": missing_packages,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "note": "Run `julia --project=julia julia/setup_environment.jl` to install dependencies.",
            }

        return {
            "success": False,
            "termination_status": "preflight_failed",
            "solver_name": "powermodels",
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "note": "Julia preflight failed before solve attempt.",
        }

    def _run_julia_script(self, script_name: str, case: dict, payload: dict) -> dict:
        start_t = time.perf_counter()
        script = self.julia_scripts_dir / script_name
        exec_ctx = collect_execution_context()

        def _finalize(result: dict) -> dict:
            out = dict(result)
            runtime = {
                "wallclock_seconds": round(time.perf_counter() - start_t, 6),
                "execution_context": exec_ctx,
            }
            out["runtime_metadata"] = runtime
            out.setdefault("runtime", runtime["wallclock_seconds"])
            out.setdefault("solve_time", runtime["wallclock_seconds"])
            return out

        if not script.exists():
            return _finalize({"success": False, "termination_status": f"missing_script:{script_name}", "solver_name": "powermodels"})

        env = os.environ.copy()
        if self.depot_path:
            env["JULIA_DEPOT_PATH"] = self.depot_path
        env.setdefault("OPENBLAS_NUM_THREADS", "1")
        env.setdefault("JULIA_NUM_THREADS", "1")
        env.setdefault("JULIA_PKG_PRECOMPILE_AUTO", "0")

        timeout_s = None
        options = payload.get("options") if isinstance(payload, dict) else None
        if isinstance(options, dict) and options.get("timeout_s") is not None:
            timeout_s = float(options["timeout_s"])

        preflight_result = self._run_preflight(env=env, timeout_s=timeout_s)
        if preflight_result is not None:
            # In unstable HPC environments preflight can timeout even when the solver
            # run itself succeeds; continue to the solve path in that specific case.
            if preflight_result.get("termination_status") != "preflight_timeout":
                return _finalize(preflight_result)

        case_path = None
        payload_path = None
        out_path = None

        try:
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f_case:
                json.dump(case, f_case)
                case_path = f_case.name
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f_payload:
                json.dump(payload, f_payload)
                payload_path = f_payload.name
            with tempfile.NamedTemporaryFile("r", suffix=".json", delete=False) as f_out:
                out_path = f_out.name

            cmd = [
                "julia",
                f"--project={self.julia_project_dir}",
                *self._julia_mode_flags(),
                str(script),
                case_path,
                payload_path,
                out_path,
            ]
            cmd_display = " ".join(shlex.quote(c) for c in cmd)
            shell_cmd = f"module load julia 2>/dev/null || true; {cmd_display}"

            try:
                proc = subprocess.run(["bash", "-lc", shell_cmd], capture_output=True, text=True, env=env, timeout=timeout_s)
            except subprocess.TimeoutExpired as exc:
                return _finalize({
                    "success": False,
                    "termination_status": "timeout",
                    "solver_name": "powermodels",
                    "stdout": exc.stdout,
                    "stderr": exc.stderr,
                })
            if proc.returncode != 0:
                return _finalize({
                    "success": False,
                    "termination_status": "process_error",
                    "solver_name": "powermodels",
                    "stdout": proc.stdout,
                    "stderr": proc.stderr,
                })

            try:
                parsed = json.loads(Path(out_path).read_text(encoding="utf-8"))
                return _finalize(parsed)
            except Exception as exc:  # noqa: BLE001
                return _finalize({
                    "success": False,
                    "termination_status": f"invalid_output:{type(exc).__name__}",
                    "solver_name": "powermodels",
                })
        finally:
            for p in (case_path, payload_path, out_path):
                if p is None:
                    continue
                try:
                    Path(p).unlink(missing_ok=True)
                except Exception:  # noqa: BLE001
                    pass
