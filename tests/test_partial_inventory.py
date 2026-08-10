from __future__ import annotations

import unittest

from _bootstrap import REPO_ROOT  # noqa: F401

from grid_data_factory.campaigns.partial_inventory import (
    BoundedTimingSample,
    CaseInventory,
    SampleSummary,
    case_inventory_dict,
    extract_sample_summary,
    recommend_tier,
)


class SampleExtractionTests(unittest.TestCase):
    def test_extracts_top_level_fields_without_parsing_embedded_case(self) -> None:
        line = '{"case_id":"case14","success":true,"termination_status":"LOCALLY_SOLVED","wallclock_seconds":1.25,"inputs":{"resolved_case":{"success":false}},"runtime_metadata":{"persistent_julia":true}}'
        summary = extract_sample_summary(line)
        self.assertEqual(summary, SampleSummary("case14", True, "LOCALLY_SOLVED", 1.25, True))

    def test_rejects_missing_required_fields(self) -> None:
        self.assertIsNone(extract_sample_summary('{"case_id":"case14"}'))


class InventoryTests(unittest.TestCase):
    def test_bounded_timing_sample_is_deterministic(self) -> None:
        first = BoundedTimingSample(3)
        second = BoundedTimingSample(3)
        for index in range(20):
            first.add(f"id-{index}", float(index))
            second.add(f"id-{index}", float(index))
        self.assertEqual(first.values, second.values)
        self.assertEqual(len(first.values), 3)

    def test_timeout_heavy_case_is_quarantined(self) -> None:
        inventory = CaseInventory(32)
        for index in range(8):
            status = "timeout" if index < 3 else "LOCALLY_SOLVED"
            inventory.add(SampleSummary("case", status != "timeout", status, float(index), True), str(index))
        tier, _ = recommend_tier(inventory, min_records=8)
        self.assertEqual(tier, "quarantine_timeout")

    def test_report_contains_rates_and_quantiles(self) -> None:
        inventory = CaseInventory(32)
        inventory.add(SampleSummary("case", True, "LOCALLY_SOLVED", 1.0, True), "a")
        inventory.add(SampleSummary("case", False, "INVALID_MODEL", 3.0, None), "b")
        row = case_inventory_dict("case", inventory)
        self.assertEqual(row["sampled_records"], 2)
        self.assertEqual(row["success_rate"], 0.5)
        self.assertEqual(row["wallclock_p50_s"], 1.0)
        self.assertEqual(row["execution_modes"], {"legacy": 1, "persistent_julia": 1})


if __name__ == "__main__":
    unittest.main()