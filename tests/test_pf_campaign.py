from __future__ import annotations

import json
import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
from pydantic import ValidationError

from _bootstrap import REPO_ROOT  # noqa: F401

from grid_data_factory.campaigns.round_runner import SampleSink, _sample_record
from grid_data_factory.pf.anchors import build_anchor_index, topology_hash
from grid_data_factory.pf.candidates import _sample_controls, _sample_stratified_contingency, allocate_quota
from grid_data_factory.pf.controls import (
    apply_response_policy,
    balanced_redispatch,
    controls_from_case,
    normalized_control_distance,
)
from grid_data_factory.pf.schemas import PFCandidate
from grid_data_factory.pf.validation import validate_anchor_consistency, validate_pf_result
from grid_data_factory.solvers.powermodels_adapter import PersistentPowerModelsSession


def _case() -> dict:
    return {
        "case_id": "case_two",
        "base_mva": 100.0,
        "buses": [
            {"bus_id": "1", "type": 3, "vm": 1.01, "va": 0.0, "vmin": 0.9, "vmax": 1.1},
            {"bus_id": "2", "type": 2, "vm": 1.0, "va": 0.0, "vmin": 0.9, "vmax": 1.1},
        ],
        "loads": [{"load_id": "l1", "bus_id": "2", "pd": 90.0, "qd": 20.0}],
        "generators": [
            {"gen_id": "g1", "bus_id": "1", "pg": 50.0, "qg": 10.0, "vg": 1.01, "status": 1, "pmin": 0.0, "pmax": 100.0, "qmin": -50.0, "qmax": 50.0},
            {"gen_id": "g2", "bus_id": "2", "pg": 40.0, "qg": 10.0, "vg": 1.0, "status": 1, "pmin": 0.0, "pmax": 100.0, "qmin": -50.0, "qmax": 50.0},
        ],
        "branches": [{"branch_id": "b1", "from": "1", "to": "2", "r": 0.01, "x": 0.1, "b": 0.0, "rate_a": 100.0, "status": 1}],
    }


def _candidate() -> dict:
    case = _case()
    return {
        "candidate_id": "pf-1",
        "case_id": "case_two",
        "task": "pf",
        "source_ac_opf_run_id": "opf-run-1",
        "parent_network_id": "case_two",
        "parent_topology_id": "topology_base",
        "parent_topology_hash": topology_hash(case),
        "topology_id": "topology_base",
        "topology_hash": topology_hash(case),
        "dataset_split": "train",
        "resolved_case": case,
        "controls": controls_from_case(case).model_dump(),
        "control_sampling_method": "pairwise_transfer",
        "control_distance": 0.1,
        "control_distance_stratum": "intermediate",
        "response_policy": {"policy_id": "reserve_participation"},
    }


class PFControlTests(unittest.TestCase):
    def test_adaptive_quota_favors_underfilled_stratum(self):
        quota = allocate_quota(10, {"near": 0.5, "large": 0.5}, {"near": 10, "large": 0})
        self.assertEqual(quota, {"near": 0, "large": 10})

    def test_projected_redispatch_is_balanced_and_bounded(self):
        case = _case()
        anchor = controls_from_case(case)
        controls, metadata = balanced_redispatch(case, {"g1": 80.0, "g2": -80.0})
        self.assertAlmostEqual(sum(c.pg for c in controls.generators.values()), 90.0)
        self.assertEqual(controls.generators["g1"].pg, 90.0)
        self.assertEqual(controls.generators["g2"].pg, 0.0)
        self.assertAlmostEqual(metadata["residual_mw"], 0.0)
        self.assertGreater(normalized_control_distance(case, anchor, controls), 0.0)

    def test_sampled_voltage_is_consistent_for_generators_on_same_bus(self):
        case = _case()
        case["generators"][1]["bus_id"] = "1"
        controls, _, _ = _sample_controls(
            case,
            controls_from_case(case),
            0.03,
            0.10,
            random.Random(1),
            max_attempts=10,
        )
        self.assertEqual(controls.generators["g1"].vg, controls.generators["g2"].vg)

    def test_generator_outage_uses_reserve_policy(self):
        case = _case()
        contingency = {"event_type": "simultaneous", "components": [{"type": "generator", "id": "g1"}]}
        post_case, controls, metadata = apply_response_policy(
            case, contingency, controls_from_case(case), {"policy_id": "reserve_participation"}
        )
        self.assertEqual([gen["gen_id"] for gen in post_case["generators"]], ["g2"])
        self.assertEqual(controls.generators["g2"].pg, 90.0)
        self.assertEqual(metadata["lost_pg_mw"], 50.0)

    def test_candidate_rejects_unknown_control(self):
        candidate = _candidate()
        candidate["controls"]["generators"]["missing"] = {"pg": 1.0, "vg": 1.0}
        with self.assertRaises(ValidationError):
            PFCandidate.model_validate(candidate)

    def test_fixed_participation_requires_weights(self):
        candidate = _candidate()
        candidate["response_policy"] = {"policy_id": "fixed_participation"}
        with self.assertRaises(ValidationError):
            PFCandidate.model_validate(candidate)

    def test_generator_criticality_falls_back_to_non_reference_pool(self):
        case = _case()
        contingency, policy_id = _sample_stratified_contingency(
            case,
            {"gen": {"1": {"pg": 0.9}, "2": {"pg": 0.1}}},
            "n1_generator",
            random.Random(1),
            "critical",
            "reserve_participation",
        )
        self.assertEqual(contingency["components"], [{"type": "generator", "id": "g2"}])
        self.assertEqual(policy_id, "reserve_participation")


class PFSolverFallbackTests(unittest.TestCase):
    def test_stops_after_ma27_converges(self):
        session = Mock()
        session.solve_pf.side_effect = [
            {"success": False, "termination_status": "ITERATION_LIMIT"},
            {"success": True, "termination_status": "LOCALLY_SOLVED"},
        ]

        result = PersistentPowerModelsSession.solve_pf_with_fallback(
            session, _case(), controls={}, hsl_library="/private/libcoinhsl.so"
        )

        attempted = [call.kwargs["options"]["linear_solver"] for call in session.solve_pf.call_args_list]
        self.assertEqual(attempted, ["", "ma27"])
        self.assertEqual(result["linear_solver"], "ma27")
        self.assertEqual(result["solver_attempt_count"], 2)

    def test_tries_ma57_after_two_nonconvergent_results(self):
        session = Mock()
        session.solve_pf.side_effect = [
            {"success": False, "termination_status": "ITERATION_LIMIT"},
            {"success": False, "termination_status": "LOCALLY_INFEASIBLE"},
            {"success": False, "termination_status": "SLOW_PROGRESS"},
        ]

        result = PersistentPowerModelsSession.solve_pf_with_fallback(
            session, _case(), controls={}, hsl_library="/private/libcoinhsl.so"
        )

        attempted = [call.kwargs["options"]["linear_solver"] for call in session.solve_pf.call_args_list]
        self.assertEqual(attempted, ["", "ma27", "ma57"])
        self.assertEqual(result["linear_solver"], "ma57")
        self.assertEqual(result["solver_attempt_count"], 3)


class PFAnchorTests(unittest.TestCase):
    def test_successful_opf_sample_indexes_controls_and_provenance(self):
        case = _case()
        sample = {
            "run_id": "opf-run-1",
            "candidate_id": "opf-candidate-1",
            "task": "ac_opf",
            "case_id": "case_two",
            "topology_id": "topology_base",
            "success": True,
            "objective": 12.0,
            "inputs": {"resolved_case": case, "candidate": {}},
            "result": {"raw_result": {"solution": {
                "gen": {"1": {"pg": 0.55, "qg": 0.12}, "2": {"pg": 0.35, "qg": 0.08}},
                "bus": {"1": {"vm": 1.02}, "2": {"vm": 1.01}},
            }}},
        }
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "samples.jsonl"
            output = Path(tmp) / "anchors.parquet"
            source.write_text(json.dumps(sample) + "\n", encoding="utf-8")
            manifest = build_anchor_index([source], output)
            row = pd.read_parquet(output).iloc[0]
            split_row = pd.read_parquet(Path(tmp) / "parent_split_registry.parquet").iloc[0]
        controls = json.loads(row["controls_json"])
        self.assertEqual(manifest["anchor_count"], 1)
        self.assertAlmostEqual(controls["generators"]["g1"]["pg"], 55.0)
        self.assertEqual(len(row["source_file_sha256"]), 64)
        self.assertEqual(json.loads(row["solution_json"])["bus"]["1"]["vm"], 1.02)
        self.assertEqual(split_row["parent_network_id"], "case_two")
        self.assertEqual(len(manifest["parent_split_registry_sha256"]), 64)


class PFStorageTests(unittest.TestCase):
    def test_pf_sink_and_record_are_task_specific(self):
        candidate = _candidate()
        result = {"success": True, "termination_status": "LOCALLY_SOLVED", "task": "pf"}
        record = _sample_record(candidate, _case(), result, "solver", "run")
        self.assertEqual(record["task"], "pf")
        self.assertEqual(record["outcome_class"], "converged_valid")
        self.assertEqual(record["response_policy_id"], "reserve_participation")
        with tempfile.TemporaryDirectory() as tmp:
            with SampleSink(Path(tmp), "solver", task="pf") as sink:
                path, _ = sink.append(candidate, _case(), result)
            self.assertEqual(path.relative_to(tmp), Path("pf/samples.jsonl"))
            self.assertTrue((Path(tmp) / "pf/outcomes/converged_valid/samples.jsonl").exists())


class PFValidationTests(unittest.TestCase):
    def test_validates_balanced_solution(self):
        case = _case()
        result = {
            "success": True,
            "termination_status": "LOCALLY_SOLVED",
            "raw_result": {"solution": {
                "gen": {"1": {"pg": 0.5, "qg": 0.1}, "2": {"pg": 0.4, "qg": 0.1}},
                "bus": {"1": {"vm": 1.01}, "2": {"vm": 1.0}},
                "branch": {"1": {"pf": 0.4, "pt": -0.4, "qf": 0.2, "qt": -0.2}},
            }},
        }
        validation = validate_pf_result(case, controls_from_case(case), result)
        self.assertTrue(validation["validation_passed"], validation)
        self.assertAlmostEqual(validation["active_power_residual_mw"], 0.0)

    def test_anchor_consistency_detects_state_drift(self):
        source = {"bus": {"1": {"vm": 1.0, "va": 0.0}}}
        matching = {"raw_result": {"solution": {"bus": {"1": {"vm": 1.00001, "va": 0.0001}}}}}
        drifted = {"raw_result": {"solution": {"bus": {"1": {"vm": 1.01, "va": 0.0}}}}}
        self.assertTrue(validate_anchor_consistency(source, matching)["passed"])
        self.assertFalse(validate_anchor_consistency(source, drifted)["passed"])


if __name__ == "__main__":
    unittest.main()