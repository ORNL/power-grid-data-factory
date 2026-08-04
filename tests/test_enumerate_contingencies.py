"""Regression tests for high-order contingency enumeration (K>=3)."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from random import Random

from _bootstrap import REPO_ROOT  # noqa: F401  (ensures src on path)

from grid_data_factory.contingencies import enumeration as _ENUM


class BuildKPlusComponentsTests(unittest.TestCase):
    def test_terminates_and_is_distinct_with_few_generators(self):
        # Regression: the old builder looped forever when generators were scarce and K>2.
        pool = {
            "branch": [f"branch_{i:06d}" for i in range(1, 22)],
            "generator": ["gen_000001", "gen_000002"],
            "bus": [f"bus_{i:06d}" for i in range(1, 15)],
        }
        for k in range(3, 11):
            comps = _ENUM.build_kplus_components(pool, k, Random(k))
            self.assertEqual(len(comps), k)
            keys = {(c["type"], c["id"]) for c in comps}
            self.assertEqual(len(keys), k)
            self.assertTrue(any(c["type"] == "branch" for c in comps))

    def test_k_exceeding_pool_returns_all_distinct(self):
        pool = {"branch": ["branch_000001", "branch_000002"], "generator": ["gen_000001"], "bus": ["bus_000001"]}
        comps = _ENUM.build_kplus_components(pool, 10, Random(1))
        self.assertEqual(len(comps), 3)
        keys = {(c["type"], c["id"]) for c in comps}
        self.assertEqual(len(keys), 3)

    def test_expand_one_reaches_requested_order(self):
        class _Args:
            n1_per_operating_point = 3
            n2_random_per_operating_point = 2
            n2_interacting_per_operating_point = 2
            n1n1_per_operating_point = 1
            max_k = 10
            nk_per_operating_point = 1

        base = {"case_id": "pglib_opf_case14_ieee", "candidate_id": "op1", "physical_credibility_score": 0.9}
        rows = _ENUM.expand_one(base, Random(1), _Args())
        orders = {r["contingency"]["order"] for r in rows}
        self.assertEqual(max(orders), 10)
        self.assertTrue({1, 2}.issubset(orders))


class ParallelDeterminismTests(unittest.TestCase):
    def _run(self, in_path: Path, out_path: Path, workers: int) -> None:
        script = REPO_ROOT / "scripts" / "enumerate_contingencies.py"
        subprocess.run(
            [sys.executable, str(script), "--input", str(in_path), "--out", str(out_path),
             "--max-k", "6", "--seed", "11", "--workers", str(workers)],
            cwd=str(REPO_ROOT), check=True, capture_output=True, text=True,
        )

    def test_parallel_output_independent_of_worker_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            in_path = tmp_path / "ops.jsonl"
            with in_path.open("w", encoding="utf-8") as fh:
                for i in range(40):
                    case = ["pglib_opf_case14_ieee", "pglib_opf_case57_ieee", "pglib_opf_case118_ieee"][i % 3]
                    fh.write(json.dumps({"case_id": case, "candidate_id": f"op{i:04d}", "physical_credibility_score": 0.9}) + "\n")
            out2 = tmp_path / "w2.jsonl"
            out5 = tmp_path / "w5.jsonl"
            self._run(in_path, out2, 2)
            self._run(in_path, out5, 5)
            self.assertEqual(out2.read_text(encoding="utf-8"), out5.read_text(encoding="utf-8"))
            self.assertTrue(out2.read_text(encoding="utf-8").strip())


if __name__ == "__main__":
    unittest.main()
