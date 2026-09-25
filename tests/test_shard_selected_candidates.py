from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _bootstrap import REPO_ROOT


class StreamingShardTests(unittest.TestCase):
    def test_contiguous_assignment_preserves_balanced_input_ranges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "candidates.jsonl"
            output = root / "shards"
            source.write_text(
                "".join(json.dumps({"candidate_id": f"c{index}"}) + "\n" for index in range(10)),
                encoding="utf-8",
            )
            subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "scripts" / "shard_selected_candidates.py"),
                    "--input",
                    str(source),
                    "--num-shards",
                    "3",
                    "--out-dir",
                    str(output),
                    "--coverage-keys",
                    "",
                    "--stream",
                    "--assignment",
                    "contiguous",
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            shards = [
                [json.loads(line)["candidate_id"] for line in path.read_text(encoding="utf-8").splitlines()]
                for path in sorted(output.glob("shard_*.jsonl"))
            ]
            manifest = json.loads((output / "shard_manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(shards, [["c0", "c1", "c2", "c3"], ["c4", "c5", "c6"], ["c7", "c8", "c9"]])
        self.assertEqual(manifest["assignment"], "contiguous")
        self.assertEqual(manifest["shard_counts"], [4, 3, 3])


if __name__ == "__main__":
    unittest.main()