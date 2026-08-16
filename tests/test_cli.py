import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch

from memory_gauntlet.cli import _demo_paths, main


class CliTests(unittest.TestCase):
    def test_demo_writes_comparison(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(main(["demo", "--out", temp]), 0)
            self.assertTrue((Path(temp) / "report.html").exists())

    def test_invalid_scenario_returns_two(self):
        self.assertEqual(main(["validate", "missing.json"]), 2)

    def test_validate_rejects_duplicate_scenario_ids(self):
        with tempfile.TemporaryDirectory() as temp:
            scenario = {
                "schema_version": "memory-gauntlet-scenario/v1",
                "id": "duplicate",
                "principals": [{"id": "alice", "role": "owner"}],
                "steps": [
                    {"op": "write", "actor": "alice", "memory_id": "m", "text": "alpha"},
                    {
                        "op": "query",
                        "actor": "alice",
                        "query": "alpha",
                        "category": "recall",
                        "expect": {"visible_ids": ["m"]},
                    },
                ],
            }
            path = Path(temp) / "scenario.json"
            path.write_text(json.dumps(scenario), encoding="utf-8")
            self.assertEqual(main(["validate", str(path), str(path)]), 2)

    def test_demo_uses_packaged_scenarios(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "demo"
            self.assertEqual(main(["demo", "--out", str(output)]), 0)
            results = (output / "results.json").read_text(encoding="utf-8")
            self.assertIn('"governance-tour"', results)
            self.assertIn('"coverage": 1.0', results)

    def test_empty_demo_bundle_is_rejected(self):
        with patch("memory_gauntlet.cli.Path.glob", return_value=[]):
            with self.assertRaisesRegex(ValueError, "no scenarios"):
                _demo_paths()


if __name__ == "__main__":
    unittest.main()
