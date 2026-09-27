from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from grid_data_factory.campaigns.round_runner import _read_existing_diversity


class PFCampaignResilienceTests(unittest.TestCase):
    def test_corrupt_diversity_parquet_is_treated_as_empty(self):
        with tempfile.TemporaryDirectory() as temporary:
            campaign_root = Path(temporary)
            (campaign_root / "diversity_ledger.parquet").touch()

            self.assertEqual(_read_existing_diversity(campaign_root), [])


if __name__ == "__main__":
    unittest.main()