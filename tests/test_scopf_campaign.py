from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from _bootstrap import REPO_ROOT  # noqa: F401  (ensures src on path)

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import scopf_campaign  # noqa: E402


class ScopfCampaignIsolationTests(unittest.TestCase):
    def test_rejects_pf_and_acopf_roots(self) -> None:
        for bad in ("data/outputs/runs/riker_pf_severity_25", "data/outputs/runs_3b", "data/outputs/runs_scopf"):
            with self.assertRaises(SystemExit):
                scopf_campaign.check_isolated(REPO_ROOT / bad)

    def test_accepts_scopf_roots(self) -> None:
        scopf_campaign.check_isolated(REPO_ROOT / "data/outputs/runs_scopf/x")
        scopf_campaign.check_isolated(REPO_ROOT / "data/outputs/campaigns_scopf/x")

    def test_plan_ids_are_unique_and_tagged(self) -> None:
        config = {
            "defaults": {"seeds": [0, 1], "sets": [{"branches": 2, "generators": 1}]},
            "cases": [{"case_file": "a/case_x.m"}, {"case_file": "a/case_y.m", "seeds": [0], "sets": [{"branches": -1, "generators": -1}]}],
        }
        ids = [t["task_id"] for t in scopf_campaign.plan_tasks(config)]
        self.assertEqual(ids, ["case_x__n1_b2_g1_s0", "case_x__n1_b2_g1_s1", "case_y__n1_ball_gall_s0"])

    def test_claim_is_exclusive_and_skips_done(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for sub in ("claims", "done", "failed"):
                (root / "tasks" / sub).mkdir(parents=True)
            task = {"task_id": "t1"}
            claim = scopf_campaign._claim(root, task, 2)
            self.assertIsNotNone(claim)
            self.assertIsNone(scopf_campaign._claim(root, task, 2))
            scopf_campaign._release(claim)
            (root / "tasks" / "done" / "t1.json").write_text("{}", encoding="utf-8")
            self.assertIsNone(scopf_campaign._claim(root, task, 2))

    def test_clear_stale_claims_counts_interrupted_attempts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for sub in ("claims", "done", "failed"):
                (root / "tasks" / sub).mkdir(parents=True)
            for task_id in ("t1", "t2"):
                self.assertIsNotNone(scopf_campaign._claim(root, {"task_id": task_id}, 2))
            (root / "tasks" / "done" / "t2.json").write_text("{}", encoding="utf-8")

            out = scopf_campaign.clear_stale_claims({"_campaign_root": root})
            self.assertEqual(out, {"cleared": 2, "interrupted_attempts_recorded": 1})
            self.assertEqual(list((root / "tasks" / "claims").iterdir()), [])
            self.assertEqual(scopf_campaign._failure_count(root, "t1"), 1)
            self.assertEqual(scopf_campaign._failure_count(root, "t2"), 0)

            self.assertIsNotNone(scopf_campaign._claim(root, {"task_id": "t1"}, 2))
            scopf_campaign.clear_stale_claims({"_campaign_root": root})
            self.assertIsNone(scopf_campaign._claim(root, {"task_id": "t1"}, 2))

    def test_run_scopf_refuses_shared_runs_root(self) -> None:
        import run_scopf

        with TemporaryDirectory() as tmp:
            (Path(tmp) / "pf").mkdir()
            ctg = Path(tmp) / "c.json"
            ctg.write_text("{}", encoding="utf-8")
            argv = ["run_scopf.py", "--case-file", "x.m", "--contingency-set", str(ctg), "--runs-root", tmp]
            with patch.object(sys, "argv", argv), patch.object(run_scopf.ac_workflow, "_build_case_plan", return_value=[("x", Path("x.m"))]):
                with self.assertRaises(SystemExit) as ctx:
                    run_scopf.main()
            self.assertIn("non-SCOPF", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
