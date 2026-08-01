from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import tempfile
from pathlib import Path


class PowerModelsAdapter:
    def __init__(self, julia_project_dir: Path, depot_path: str | None = None):
        self.julia_project_dir = Path(julia_project_dir)
        self.depot_path = depot_path

    def solve_pf(self, case: dict, controls: dict | None = None, options: dict | None = None) -> dict:
        return self._run_julia_script("run_pf.jl", case, {"controls": controls or {}, "options": options or {}})

    def solve_dc_opf(self, case: dict, options: dict | None = None) -> dict:
        return self._run_julia_script("run_opf.jl", case, {"task": "dc_opf", "options": options or {}})

    def solve_relaxed_opf(self, case: dict, formulation: str, options: dict | None = None) -> dict:
        return self._run_julia_script("run_opf.jl", case, {"task": "relaxed_opf", "formulation": formulation, "options": options or {}})

    def solve_ac_opf(self, case: dict, options: dict | None = None) -> dict:
        return self._run_julia_script("run_opf.jl", case, {"task": "ac_opf", "options": options or {}})

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
        preflight_timeout = 60.0 if timeout_s is None else min(timeout_s, 60.0)
        preflight_cmd = [
            "julia",
            f"--project={self.julia_project_dir}",
            "--compiled-modules=no",
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
        script = self.julia_project_dir / script_name
        if not script.exists():
            return {"success": False, "termination_status": f"missing_script:{script_name}", "solver_name": "powermodels"}

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
            return preflight_result

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
                "--compiled-modules=no",
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
                return {
                    "success": False,
                    "termination_status": "timeout",
                    "solver_name": "powermodels",
                    "stdout": exc.stdout,
                    "stderr": exc.stderr,
                }
            if proc.returncode != 0:
                return {
                    "success": False,
                    "termination_status": "process_error",
                    "solver_name": "powermodels",
                    "stdout": proc.stdout,
                    "stderr": proc.stderr,
                }

            try:
                return json.loads(Path(out_path).read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                return {
                    "success": False,
                    "termination_status": f"invalid_output:{type(exc).__name__}",
                    "solver_name": "powermodels",
                }
        finally:
            for p in (case_path, payload_path, out_path):
                if p is None:
                    continue
                try:
                    Path(p).unlink(missing_ok=True)
                except Exception:  # noqa: BLE001
                    pass
