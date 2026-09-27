from __future__ import annotations

import unittest
from unittest.mock import Mock

from grid_data_factory.solvers.powermodels_adapter import PersistentPowerModelsSession


class PersistentAcOpfSolverSequenceTests(unittest.TestCase):
    def test_uses_exact_configured_solver_sequence(self):
        session = object.__new__(PersistentPowerModelsSession)
        session.options = {"linear_solvers": ["ma27", "ma57"]}
        session._solve = Mock(
            side_effect=[
                {"success": False, "termination_status": "LOCALLY_INFEASIBLE", "solve_time": 1.0},
                {"success": True, "termination_status": "LOCALLY_SOLVED", "solve_time": 2.0},
            ]
        )

        result = session.solve_ac_opf({"base_mva": 100.0}, timeout_s=30.0)

        self.assertEqual(
            [call.args[3]["options"]["linear_solver"] for call in session._solve.call_args_list],
            ["ma27", "ma57"],
        )
        self.assertEqual(result["linear_solver"], "ma57")
        self.assertEqual(result["solver_attempt_count"], 2)
        self.assertEqual([attempt["linear_solver"] for attempt in result["solver_attempts"]], ["ma27", "ma57"])

    def test_prepends_default_to_fallbacks(self):
        session = object.__new__(PersistentPowerModelsSession)
        session.options = {"linear_solver_fallbacks": ["ma27", "ma57"]}
        session._solve = Mock(return_value={"success": True, "termination_status": "LOCALLY_SOLVED"})

        result = session.solve_ac_opf({}, timeout_s=30.0)

        self.assertEqual(session._solve.call_args.args[3]["options"]["linear_solver"], "")
        self.assertEqual(result["linear_solver"], "default")
        self.assertEqual(result["solver_attempt_count"], 1)


if __name__ == "__main__":
    unittest.main()