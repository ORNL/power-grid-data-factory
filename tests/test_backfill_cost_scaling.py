from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from _bootstrap import REPO_ROOT


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_BACKFILL = _load("backfill_cost_scaling")
_REPAIR = _load("repair_failed_campaign_cases")

BASE = 100.0


def _record(costs, pgs, *, statuses=None, candidate_id="c::1", run_id="r1", success=True, solver="powermodels",
            task="ac_opf", objective=None, convention=None, lam=(2.0, -0.5)):
    statuses = statuses or [1] * len(costs)
    generators = [
        {"gen_id": str(i + 1), "bus_id": "1", "cost": list(cost), "status": status}
        for i, (cost, status) in enumerate(zip(costs, statuses))
    ]
    gen_solution = {}
    legacy = 0.0
    for i, (cost, pg, status) in enumerate(zip(costs, pgs, statuses), start=1):
        value = cost[0] * pg * pg + cost[1] * pg + cost[2]
        gen_solution[str(i)] = {"pg": pg, "qg": 0.1, "pg_cost": value if status else 0.0}
        if status:
            legacy += value
    objective = legacy if objective is None else objective
    result = {
        "success": success,
        "termination_status": "LOCALLY_SOLVED" if success else "LOCALLY_INFEASIBLE",
        "solver_name": solver,
        "task": task,
        "objective": objective,
        "raw_result": {
            "objective": objective,
            "objective_lb": None,
            "solution": {
                "baseMVA": BASE,
                "bus": {"1": {"vm": 1.0, "va": 0.0, "lam_kcl_r": lam[0], "lam_kcl_i": lam[1]}},
                "gen": gen_solution,
            },
        },
    }
    if convention:
        result["cost_convention"] = convention
    return {
        "candidate_id": candidate_id,
        "run_id": run_id,
        "case_id": "case",
        "task": task,
        "success": success,
        "termination_status": result["termination_status"],
        "objective": objective,
        "inputs": {
            "candidate": {"candidate_id": candidate_id, "case_id": "case"},
            "resolved_case": {"base_mva": BASE, "generators": generators},
        },
        "result": result,
    }


class BackfillCostScalingTests(unittest.TestCase):
    def test_linear_rescale_with_c0_and_inactive_generator(self):
        record = _record([[0, 20.0, 5.0], [0, 30.0, 7.0], [0, 99.0, 11.0]], [0.5, 1.2, 0.3], statuses=[1, 1, 0])
        self.assertEqual(_BACKFILL.classify(record), "linear")
        self.assertEqual(_BACKFILL.rescale_linear(record, "t"), "exact_linear")
        expected = (20 * BASE * 0.5 + 5) + (30 * BASE * 1.2 + 7)
        self.assertAlmostEqual(record["objective"], expected)
        self.assertAlmostEqual(record["result"]["objective"], expected)
        self.assertAlmostEqual(record["result"]["raw_result"]["objective"], expected)
        gens = record["result"]["raw_result"]["solution"]["gen"]
        self.assertAlmostEqual(gens["1"]["pg_cost"], 20 * BASE * 0.5 + 5)
        self.assertAlmostEqual(gens["3"]["pg_cost"], 0.0)
        bus = record["result"]["raw_result"]["solution"]["bus"]["1"]
        self.assertAlmostEqual(bus["lam_kcl_r"], 2.0 * BASE)
        self.assertAlmostEqual(bus["lam_kcl_i"], -0.5 * BASE)
        self.assertEqual(gens["1"]["pg"], 0.5)
        self.assertEqual(record["result"]["cost_convention"], "per_unit_scaled_v2")
        self.assertEqual(record["result"]["cost_rescale"]["old_objective"], 20 * 0.5 + 5 + 30 * 1.2 + 7)
        self.assertEqual(_BACKFILL.classify(record), "skipped_v2")

    def test_classification_skips(self):
        self.assertEqual(_BACKFILL.classify(_record([[0.1, 20.0, 0]], [1.0])), "quadratic")
        self.assertEqual(_BACKFILL.classify(_record([[0.1, 20.0, 0], [0, 1, 0]], [1.0, 1.0], statuses=[0, 1])), "linear")
        self.assertEqual(_BACKFILL.classify(_record([[0, 20.0, 0]], [1.0], solver="exago")), "skipped_other")
        self.assertEqual(_BACKFILL.classify(_record([[0, 20.0, 0]], [1.0], task="ac_pf")), "skipped_other")
        self.assertEqual(
            _BACKFILL.classify(_record([[0, 20.0, 0]], [1.0], convention="per_unit_scaled_v2")), "skipped_v2"
        )

    def test_mismatch_is_unverified_and_unchanged(self):
        record = _record([[0, 20.0, 0]], [1.0], objective=999.0)
        before = json.dumps(record, sort_keys=True)
        self.assertEqual(_BACKFILL.rescale_linear(record, "t"), "unverified")
        self.assertEqual(json.dumps(record, sort_keys=True), before)

    def test_unmarked_per_unit_records_are_marked_already_v2(self):
        linear = _record([[0, 20.0, 5.0]], [0.5], objective=20.0 * BASE * 0.5 + 5.0)
        quadratic = _record([[0.1, 20.0, 5.0]], [0.5], objective=0.1 * BASE**2 * 0.25 + 20.0 * BASE * 0.5 + 5.0)
        for record in (linear, quadratic):
            values = json.dumps(record["result"]["raw_result"], sort_keys=True)
            self.assertEqual(_BACKFILL.mark_already_v2(record, "t", "fallback"), "already_v2")
            self.assertEqual(json.dumps(record["result"]["raw_result"], sort_keys=True), values)
            self.assertEqual(record["result"]["cost_convention"], "per_unit_scaled_v2")
            self.assertEqual(_BACKFILL.classify(record), "skipped_v2")
        self.assertEqual(_BACKFILL.rescale_linear(_record([[0, 20.0, 5.0]], [0.5], objective=1005.0), "t"), "already_v2")
        legacy_quadratic = _record([[0.1, 20.0, 5.0]], [0.5])
        self.assertEqual(_BACKFILL.mark_already_v2(legacy_quadratic, "t", "quadratic"), "quadratic")
        self.assertNotIn("cost_convention", legacy_quadratic["result"])

    def test_no_solution_marks_convention_only(self):
        record = _record([[0, 20.0, 0]], [1.0], success=False, objective=0.0)
        del record["result"]["raw_result"]["solution"]["gen"]
        self.assertEqual(_BACKFILL.rescale_linear(record, "t"), "no_solution")
        self.assertEqual(record["objective"], 0.0)
        self.assertEqual(record["result"]["raw_result"]["solution"]["bus"]["1"]["lam_kcl_r"], 2.0)
        self.assertEqual(record["result"]["cost_convention"], "per_unit_scaled_v2")

    def test_process_file_updates_report_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            samples = root / "samples.jsonl"
            linear = _record([[0, 20.0, 0]], [1.0], candidate_id="lin", run_id="r1")
            quad = _record([[0.5, 20.0, 0]], [1.0], candidate_id="quad", run_id="r2")
            exago = _record([[0.5, 20.0, 0]], [1.0], candidate_id="ex", run_id="r3", solver="exago")
            quad_line = json.dumps(quad) + "\n"
            samples.write_text(json.dumps(linear) + "\n" + quad_line + json.dumps(exago) + "\n", encoding="utf-8")
            report = root / "report.json"
            report.write_text(
                json.dumps({"solved": [
                    {"candidate_id": "lin", "run_id": "r1", "objective": 20.0},
                    {"candidate_id": "quad", "run_id": "r2", "objective": 20.5},
                ]}),
                encoding="utf-8",
            )
            dry = _BACKFILL.process_file(samples, report, dry_run=True)
            self.assertEqual(dry["counts"], {"exact_linear": 1, "quadratic": 1, "skipped_other": 1})
            self.assertEqual(json.loads(samples.read_text().splitlines()[0])["objective"], 20.0)

            first = _BACKFILL.process_file(samples, report, dry_run=False)
            self.assertEqual(first["rewritten"], 1)
            self.assertEqual(first["report_rows_updated"], 1)
            lines = samples.read_text(encoding="utf-8").splitlines(keepends=True)
            self.assertAlmostEqual(json.loads(lines[0])["objective"], 20.0 * BASE)
            self.assertEqual(lines[1], quad_line)
            rows = json.loads(report.read_text())["solved"]
            self.assertAlmostEqual(rows[0]["objective"], 2000.0)
            self.assertEqual(rows[1]["objective"], 20.5)

            content = samples.read_text(encoding="utf-8")
            second = _BACKFILL.process_file(samples, report, dry_run=False)
            self.assertEqual(second["rewritten"], 0)
            self.assertEqual(second["counts"]["skipped_v2"], 1)
            self.assertEqual(samples.read_text(encoding="utf-8"), content)


class LegacyQuadraticResolveTests(unittest.TestCase):
    def test_prepare_and_merge_replaces_success_and_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runs_root = root / "data/outputs/runs_3b"
            source = runs_root / "mapreduce_round_006/shard_00002/ac_opf/samples.jsonl"
            source.parent.mkdir(parents=True)
            quad_ok = _record([[0.5, 20.0, 0]], [1.0], candidate_id="q1", run_id="r1")
            quad_ok["inputs"]["candidate"]["warm_start_source"] = {"samples_path": "x", "byte_offset": 0}
            quad_fail = _record([[0.5, 20.0, 0]], [1.0], candidate_id="q2", run_id="r2")
            linear = _record([[0, 20.0, 0]], [1.0], candidate_id="l1", run_id="r3")
            linear_line = json.dumps(linear) + "\n"
            source.write_text(json.dumps(quad_ok) + "\n" + json.dumps(quad_fail) + "\n" + linear_line, encoding="utf-8")
            report = root / _REPAIR._report_relpath("ultrascale_3b__r006__s00002", 6)
            report.parent.mkdir(parents=True)
            report.write_text(json.dumps({"solved": [
                {"candidate_id": "q1", "run_id": "r1", "success": True, "objective": 20.5},
                {"candidate_id": "q2", "run_id": "r2", "success": True, "objective": 20.5},
            ]}), encoding="utf-8")

            out = root / "repair/candidates.jsonl"
            prepared = _REPAIR.prepare(
                root, runs_root, [6], None, out, root / "repair/prepare.json", shard_count=4, select="legacy-quadratic"
            )
            self.assertEqual(prepared["extracted_failures"], 2)
            candidates = [json.loads(line) for line in out.read_text().splitlines()]
            self.assertNotIn("warm_start_source", candidates[0])
            self.assertEqual(candidates[0]["_repair_select"], "legacy-quadratic")

            retry_ok = _record([[0.5, 20.0, 0]], [0.8], candidate_id=candidates[0]["candidate_id"], run_id="x1",
                               convention="per_unit_scaled_v2")
            retry_ok["objective"] = 5000.0
            retry_ok["inputs"]["candidate"] = candidates[0]
            retry_fail = {
                "candidate_id": candidates[1]["candidate_id"], "run_id": "x2", "task": "ac_opf",
                "success": False, "termination_status": "timeout", "objective": None,
                "inputs": {"candidate": candidates[1]},
                "result": {"success": False, "termination_status": "timeout", "solver_name": "powermodels"},
            }
            retry_path = root / "repair/runs/shard_00000/ac_opf/samples.jsonl"
            retry_path.parent.mkdir(parents=True)
            retry_path.write_text(json.dumps(retry_ok) + "\n" + json.dumps(retry_fail) + "\n", encoding="utf-8")

            merged = _REPAIR.merge(
                root, root / "repair/runs", root / "repair/work", root / "repair/merge.json", True,
                select="legacy-quadratic",
            )
            self.assertEqual(merged["rows_replaced"], 2)
            lines = source.read_text(encoding="utf-8").splitlines(keepends=True)
            first, second = json.loads(lines[0]), json.loads(lines[1])
            self.assertEqual(first["candidate_id"], "q1")
            self.assertEqual(first["objective"], 5000.0)
            self.assertEqual(first["repair_provenance"]["reason"], "cost_convention_quadratic_resolve")
            self.assertEqual(first["repair_provenance"]["previous_objective_legacy"], 20.5)
            self.assertIn("warm_start_source", first["inputs"]["candidate"])
            self.assertFalse(second["success"])
            self.assertEqual(second["result"]["cost_convention"], "per_unit_scaled_v2")
            self.assertEqual(lines[2], linear_line)
            rows = json.loads(report.read_text())["solved"]
            self.assertEqual(rows[0]["objective"], 5000.0)
            self.assertFalse(rows[1]["success"])
            self.assertEqual(rows[1]["feasibility_label"], "error")

            again = _REPAIR.merge(
                root, root / "repair/runs", root / "repair/work", root / "repair/merge2.json", True,
                select="legacy-quadratic",
            )
            self.assertEqual(again["rows_replaced"], 0)

    def test_old_code_converged_retry_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runs_root = root / "data/outputs/runs_3b"
            source = runs_root / "mapreduce_round_000/shard_00000/ac_opf/samples.jsonl"
            source.parent.mkdir(parents=True)
            text = json.dumps(_record([[0.5, 20.0, 0]], [1.0], candidate_id="q1")) + "\n"
            source.write_text(text, encoding="utf-8")
            out = root / "repair/candidates.jsonl"
            _REPAIR.prepare(root, runs_root, [0], None, out, root / "repair/p.json", shard_count=1,
                            select="legacy-quadratic")
            candidate = json.loads(out.read_text())
            retry = _record([[0.5, 20.0, 0]], [1.0], candidate_id=candidate["candidate_id"])
            retry["inputs"]["candidate"] = candidate
            retry_path = root / "repair/runs/shard_00000/ac_opf/samples.jsonl"
            retry_path.parent.mkdir(parents=True)
            retry_path.write_text(json.dumps(retry) + "\n", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                _REPAIR.merge(root, root / "repair/runs", root / "repair/work", root / "repair/m.json", True,
                              select="legacy-quadratic")
            self.assertEqual(source.read_text(encoding="utf-8"), text)

    def test_tsv_source_list_for_auxiliary_roots(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with_report = root / "data/outputs/runs_x/mapreduce_round_000/shard_00003/ac_opf/samples.jsonl"
            bench = root / "data/outputs/benchmarks/b/runs/item_00001/ac_opf/samples.jsonl"
            for path, cid in ((with_report, "q1"), (bench, "q2")):
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps(_record([[0.5, 20.0, 0]], [1.0], candidate_id=cid)) + "\n", encoding="utf-8")
            report = root / "data/outputs/campaigns/x__r000__s00003/round_summaries/round_000_ac_execution_report.json"
            source_list = root / "sources.tsv"
            source_list.write_text(f"{with_report}\t{report}\n{bench}\t\n", encoding="utf-8")
            out = root / "repair/part.jsonl"
            result = _REPAIR.prepare_part(root, source_list, 0, 1, None, out, root / "repair/part.json",
                                          select="legacy-quadratic")
            self.assertEqual(result["extracted_failures"], 2)
            first, second = [json.loads(line) for line in out.read_text().splitlines()]
            self.assertEqual(first["_repair_source_report_relpath"], str(report.relative_to(root)))
            self.assertEqual(second["_repair_source_report_relpath"], "")
            self.assertEqual(second["_repair_source_round"], 0)

    def test_partitioned_merge_without_backup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runs_root = root / "data/outputs/runs_3b"
            sources = []
            for shard in range(2):
                source = runs_root / f"mapreduce_round_000/shard_{shard:05d}/ac_opf/samples.jsonl"
                source.parent.mkdir(parents=True)
                source.write_text(json.dumps(_record([[0.5, 20.0, 0]], [1.0], candidate_id=f"q{shard}")) + "\n")
                sources.append(source)
            out = root / "repair/candidates.jsonl"
            _REPAIR.prepare(root, runs_root, [0], None, out, root / "repair/p.json", shard_count=2,
                            select="legacy-quadratic")
            retry_path = root / "repair/runs/shard_00000/ac_opf/samples.jsonl"
            retry_path.parent.mkdir(parents=True)
            with retry_path.open("w") as handle:
                for candidate in (json.loads(line) for line in out.read_text().splitlines()):
                    retry = _record([[0.5, 20.0, 0]], [0.8], candidate_id=candidate["candidate_id"],
                                    convention="per_unit_scaled_v2")
                    retry["inputs"]["candidate"] = candidate
                    handle.write(json.dumps(retry) + "\n")
            work = root / "repair/work"
            _REPAIR.merge(root, root / "repair/runs", work, root / "repair/dry.json", False, select="legacy-quadratic")
            replaced = sum(
                _REPAIR.merge(root, root / "repair/runs", work, root / f"repair/m{i}.json", True,
                              select="legacy-quadratic", backup=False, part_index=i, part_count=2)["rows_replaced"]
                for i in range(2)
            )
            self.assertEqual(replaced, 2)
            self.assertFalse((work / "backups").exists())
            for source in sources:
                record = json.loads(source.read_text())
                self.assertEqual(record["result"]["cost_convention"], "per_unit_scaled_v2")
            self.assertFalse(list(runs_root.rglob("*.repair_in_progress")))

    def test_index_merge_matches_spool_and_resumes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runs_root = root / "data/outputs/runs_3b"
            sources = []
            for shard in range(3):
                source = runs_root / f"mapreduce_round_000/shard_{shard:05d}/ac_opf/samples.jsonl"
                source.parent.mkdir(parents=True)
                rows = [_record([[0.5, 20.0, 0]], [1.0], candidate_id=f"q{shard}_{i}", run_id=f"r{shard}_{i}")
                        for i in range(2)]
                linear = _record([[0, 20.0, 0]], [1.0], candidate_id=f"l{shard}", run_id=f"l{shard}")
                source.write_text("".join(json.dumps(row) + "\n" for row in [*rows, linear]))
                sources.append(source)
            out = root / "repair/candidates.jsonl"
            _REPAIR.prepare(root, runs_root, [0], None, out, root / "repair/p.json", shard_count=3,
                            select="legacy-quadratic")
            candidates = [json.loads(line) for line in out.read_text().splitlines()]
            self.assertEqual(len(candidates), 6)
            # Retries for one source split across two retry files, plus one failed retry.
            for index, candidate in enumerate(candidates):
                retry_path = root / f"repair/runs/shard_{index % 2:05d}/ac_opf/samples.jsonl"
                retry_path.parent.mkdir(parents=True, exist_ok=True)
                retry = _record([[0.5, 20.0, 0]], [0.8], candidate_id=candidate["candidate_id"],
                                convention="per_unit_scaled_v2", success=index != 5)
                retry["objective"] = 1000.0 + index
                retry["inputs"]["candidate"] = candidate
                with retry_path.open("a") as handle:
                    handle.write(json.dumps(retry) + "\n")
            with (root / "repair/runs/shard_00000/ac_opf/samples.jsonl").open("a") as handle:
                handle.write("{truncated\n")

            work = root / "repair/work"
            for part in range(3):
                index = _REPAIR.build_merge_index(root / "repair/runs", work, part, 3, select="legacy-quadratic")
                self.assertEqual(index["part_count"], 3)
            with self.assertRaises(RuntimeError):
                _REPAIR._index_parts(root / "repair/missing")
            parts = work / "merge_parts"
            replaced = 0
            for part in range(2):
                manifest = _REPAIR.merge_from_index(root, root / "repair/runs", work, parts / f"part_{part:05d}.json",
                                                    select="legacy-quadratic", backup=False,
                                                    part_index=part, part_count=2)
                replaced += manifest["rows_replaced"]
            self.assertEqual(replaced, 6)
            self.assertFalse(_REPAIR.merge_finalize(parts, 3, work / "merge_manifest.json")["complete"])
            self.assertFalse((work / "merge_manifest.json").exists())
            final = _REPAIR.merge_finalize(parts, 2, work / "merge_manifest.json")
            self.assertTrue(final["complete"])
            self.assertEqual(final["rows_replaced"], 6)

            objectives = {}
            for source in sources:
                lines = source.read_text().splitlines()
                self.assertEqual(len(lines), 3)
                for record in map(json.loads, lines):
                    if record["candidate_id"].startswith("q"):
                        self.assertEqual(record["result"]["cost_convention"], "per_unit_scaled_v2")
                        objectives[record["candidate_id"]] = record["objective"]
            self.assertEqual(sorted(objectives.values())[:5], [1000.0, 1001.0, 1002.0, 1003.0, 1004.0])

            snapshot = [source.read_text() for source in sources]
            again = _REPAIR.merge_from_index(root, root / "repair/runs", work, parts / "part_00000.json",
                                             select="legacy-quadratic", backup=False, part_index=0, part_count=2)
            self.assertTrue(again["applied"])
            (parts / "part_00001.json").unlink()
            rerun = _REPAIR.merge_from_index(root, root / "repair/runs", work, parts / "part_00001.json",
                                             select="legacy-quadratic", backup=False, part_index=1, part_count=2)
            self.assertEqual(rerun["rows_replaced"], 0)
            self.assertEqual([source.read_text() for source in sources], snapshot)


if __name__ == "__main__":
    unittest.main()
