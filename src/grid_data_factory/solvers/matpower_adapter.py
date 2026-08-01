from __future__ import annotations


class MatpowerAdapter:
    def __init__(self, octave_executable: str = "octave"):
        self.octave_executable = octave_executable

    def solve_pf(self, case: dict, controls: dict | None = None, options: dict | None = None) -> dict:
        return {
            "success": False,
            "termination_status": "not_implemented",
            "solver_name": "matpower",
            "note": "Install GNU Octave + MATPOWER and implement runner wrapper.",
        }

    def solve_dc_opf(self, case: dict, options: dict | None = None) -> dict:
        return {"success": False, "termination_status": "not_implemented", "solver_name": "matpower"}

    def solve_relaxed_opf(self, case: dict, formulation: str, options: dict | None = None) -> dict:
        return {"success": False, "termination_status": "not_implemented", "solver_name": "matpower"}

    def solve_ac_opf(self, case: dict, options: dict | None = None) -> dict:
        return {"success": False, "termination_status": "not_implemented", "solver_name": "matpower"}

    def solve_contingency_pf(self, case: dict, contingency: dict, controls: dict | None = None, options: dict | None = None) -> dict:
        return {"success": False, "termination_status": "not_implemented", "solver_name": "matpower"}

    def solve_corrective_ac_opf(self, case: dict, contingency: dict, options: dict | None = None) -> dict:
        return {"success": False, "termination_status": "not_implemented", "solver_name": "matpower"}

    def solve_scopf(self, case: dict, contingencies: list[dict], options: dict | None = None) -> dict:
        return {"success": False, "termination_status": "not_implemented", "solver_name": "matpower"}
