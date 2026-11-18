import importlib.util
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from _bootstrap import REPO_ROOT


def _load_script(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


prepare = _load_script(
    "prepare_powermodels_restart_campaign",
    REPO_ROOT / "scripts" / "prepare_powermodels_restart_campaign.py",
)
run_campaign = _load_script(
    "run_campaign_ac_opf_round",
    REPO_ROOT / "scripts" / "run_campaign_ac_opf_round.py",
)


class PowerModelsRestartCampaignTests(unittest.TestCase):
    def test_extracts_almost_solved_record_and_loads_solution_by_offset(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            samples = root / "runs" / "shard_00000" / "ac_opf" / "samples.jsonl"
            samples.parent.mkdir(parents=True)
            records = [
                {
                    "candidate_id": "solved",
                    "termination_status": "LOCALLY_SOLVED",
                    "inputs": {"candidate": {"candidate_id": "solved"}},
                    "result": {"raw_result": {"solution": {"bus": {"1": {"vm": 1.0}}}}},
                },
                {
                    "candidate_id": "almost",
                    "termination_status": "ALMOST_LOCALLY_SOLVED",
                    "inputs": {"candidate": {"candidate_id": "almost", "case_id": "case"}},
                    "result": {"raw_result": {"solution": {"bus": {"1": {"vm": 1.02}}}}},
                },
            ]
            samples.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")

            candidates = prepare.collect_restart_candidates(
                root,
                root / "runs",
                {"ALMOST_LOCALLY_SOLVED"},
            )

            self.assertEqual([candidate["candidate_id"] for candidate in candidates], ["almost"])
            solution = run_campaign._load_warm_start_solution(root, candidates[0])
            self.assertEqual(solution["bus"]["1"]["vm"], 1.02)

    def test_loader_rejects_candidate_mismatch(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            samples = root / "samples.jsonl"
            line = json.dumps({
                "candidate_id": "source",
                "result": {"raw_result": {"solution": {"bus": {}}}},
            }) + "\n"
            samples.write_text(line, encoding="utf-8")
            candidate = {
                "candidate_id": "other",
                "warm_start_source": {
                    "samples_path": str(samples),
                    "byte_offset": 0,
                    "byte_length": len(line.encode()),
                },
            }

            with self.assertRaisesRegex(ValueError, "warm-start candidate mismatch"):
                run_campaign._load_warm_start_solution(root, candidate)

    def test_filters_by_case_id_samples_path_and_limit(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs = root / "runs"
            paths = []
            for shard in ("shard_00000", "shard_00001"):
                samples = runs / shard / "ac_opf" / "samples.jsonl"
                samples.parent.mkdir(parents=True)
                records = [
                    {
                        "candidate_id": f"{shard}_{case}",
                        "case_id": case,
                        "termination_status": "ITERATION_LIMIT",
                        "inputs": {"candidate": {"candidate_id": f"{shard}_{case}"}},
                        "result": {"raw_result": {"solution": {"bus": {}}}},
                    }
                    for case in ("big", "small", "big2")
                ]
                samples.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
                paths.append(samples)

            by_case = prepare.collect_restart_candidates(root, runs, {"ITERATION_LIMIT"}, case_ids={"big"})
            self.assertEqual([c["candidate_id"] for c in by_case], ["shard_00000_big", "shard_00001_big"])

            only_second = prepare.collect_restart_candidates(
                root, runs, {"ITERATION_LIMIT"}, case_ids={"big"}, sample_paths=[paths[1]]
            )
            self.assertEqual([c["candidate_id"] for c in only_second], ["shard_00001_big"])

            limited = prepare.collect_restart_candidates(root, runs, {"ITERATION_LIMIT"}, limit=2)
            self.assertEqual(len(limited), 2)

    def test_parse_ipopt_option_casts_values(self) -> None:
        self.assertEqual(run_campaign._parse_ipopt_option("max_cpu_time=100"), ("max_cpu_time", 100))
        self.assertEqual(run_campaign._parse_ipopt_option("mu_init=1e-4"), ("mu_init", 1e-4))
        self.assertEqual(run_campaign._parse_ipopt_option("mu_strategy=adaptive"), ("mu_strategy", "adaptive"))
        for bad in ("mu_init", "=1"):
            with self.assertRaisesRegex(ValueError, "KEY=VALUE"):
                run_campaign._parse_ipopt_option(bad)


if __name__ == "__main__":
    unittest.main()
