from __future__ import annotations

from typing import Protocol


class PowerSystemSolver(Protocol):
    def solve_pf(self, case: dict, controls: dict | None = None, options: dict | None = None) -> dict:
        ...

    def solve_dc_opf(self, case: dict, options: dict | None = None) -> dict:
        ...

    def solve_relaxed_opf(self, case: dict, formulation: str, options: dict | None = None) -> dict:
        ...

    def solve_ac_opf(self, case: dict, options: dict | None = None) -> dict:
        ...

    def solve_contingency_pf(self, case: dict, contingency: dict, controls: dict | None = None, options: dict | None = None) -> dict:
        ...

    def solve_corrective_ac_opf(self, case: dict, contingency: dict, options: dict | None = None) -> dict:
        ...

    def solve_scopf(self, case: dict, contingencies: list[dict], options: dict | None = None) -> dict:
        ...
