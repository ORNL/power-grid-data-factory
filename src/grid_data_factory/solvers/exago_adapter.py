from __future__ import annotations


class ExaGOAdapter:
    def solve_pf(self, case: dict, controls: dict | None = None, options: dict | None = None) -> dict:
        return {"success": False, "termination_status": "not_implemented", "solver_name": "exago"}

    def solve_dc_opf(self, case: dict, options: dict | None = None) -> dict:
        return {"success": False, "termination_status": "not_implemented", "solver_name": "exago"}

    def solve_relaxed_opf(self, case: dict, formulation: str, options: dict | None = None) -> dict:
        return {"success": False, "termination_status": "not_implemented", "solver_name": "exago"}

    def solve_ac_opf(self, case: dict, options: dict | None = None) -> dict:
        return {"success": False, "termination_status": "not_implemented", "solver_name": "exago"}

    def solve_contingency_pf(self, case: dict, contingency: dict, controls: dict | None = None, options: dict | None = None) -> dict:
        return {"success": False, "termination_status": "not_implemented", "solver_name": "exago"}

    def solve_corrective_ac_opf(self, case: dict, contingency: dict, options: dict | None = None) -> dict:
        return {"success": False, "termination_status": "not_implemented", "solver_name": "exago"}

    def solve_scopf(self, case: dict, contingencies: list[dict], options: dict | None = None) -> dict:
        return {"success": False, "termination_status": "not_implemented", "solver_name": "exago"}
