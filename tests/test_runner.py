import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from memory_gauntlet import safeio
from memory_gauntlet.reporting import markdown_report, write_bundle
from memory_gauntlet.adapters import (
    MAX_LOGICAL_TIME,
    GovernedSQLiteAdapter,
    LeakyAppendOnlyAdapter,
)
from memory_gauntlet.runner import load_scenario, run_paths, run_scenarios, validate_scenario


PROJECT = Path(__file__).resolve().parents[1]
SCENARIO = PROJECT / "examples" / "scenarios" / "governance-tour.json"


class RunnerTests(unittest.TestCase):
    def test_validates_scenario(self):
        scenario = load_scenario(str(SCENARIO))
        self.assertEqual(validate_scenario(scenario), [])
        broken = dict(scenario, schema_version="wrong")
        self.assertTrue(validate_scenario(broken))

    def test_governed_beats_leaky_with_expected_metrics(self):
        governed = run_paths([str(SCENARIO)], "governed")
        leaky = run_paths([str(SCENARIO)], "leaky")
        self.assertEqual(governed["scorecard"]["composite"], 1.0)
        self.assertEqual(governed["scorecard"]["privacy_leakage_rate"], 0.0)
        self.assertGreater(governed["scorecard"]["composite"], leaky["scorecard"]["composite"])
        self.assertGreater(leaky["scorecard"]["stale_rate"], 0.0)
        self.assertLess(leaky["scorecard"]["deletion_compliance"], 1.0)

    def test_results_are_deterministic(self):
        first = run_paths([str(SCENARIO)], "governed")
        second = run_paths([str(SCENARIO)], "governed")
        self.assertEqual(first, second)

    def test_each_scenario_runs_in_fresh_adapter_state(self):
        scenario = {
            "schema_version": "memory-gauntlet-scenario/v1",
            "id": "first",
            "principals": [{"id": "alice", "role": "product"}],
            "steps": [
                {
                    "op": "write",
                    "actor": "alice",
                    "memory_id": "timezone",
                    "text": "Timezone UTC",
                    "readers": [],
                },
                {
                    "op": "query",
                    "actor": "alice",
                    "query": "timezone",
                    "category": "recall",
                    "expect": {"visible_ids": ["timezone"]},
                },
            ],
        }
        second = copy.deepcopy(scenario)
        second["id"] = "second"
        result = run_scenarios([scenario, second], GovernedSQLiteAdapter())
        self.assertEqual(result["scenario_ids"], ["first", "second"])
        self.assertEqual([item["failed"] for item in result["scenarios"]], [0, 0])
        self.assertEqual(result["scorecard"]["cost"]["writes"], 2)
        self.assertEqual(result["scorecard"]["cost"]["queries"], 2)
        self.assertEqual(result["scorecard"]["cost"]["records_scanned"], 2)

    def test_duplicate_scenario_ids_are_rejected(self):
        scenario = load_scenario(str(SCENARIO))
        with self.assertRaisesRegex(ValueError, "scenario ids must be unique"):
            run_scenarios([scenario, copy.deepcopy(scenario)], GovernedSQLiteAdapter())

    def test_bundle_formats(self):
        runs = [run_paths([str(SCENARIO)], "governed"), run_paths([str(SCENARIO)], "leaky")]
        with tempfile.TemporaryDirectory() as temp:
            output = write_bundle(runs, temp)
            for name in ("results.json", "scorecard.json", "report.md", "report.html", "checksums.sha256"):
                self.assertTrue((output / name).exists(), name)
            value = json.loads((output / "results.json").read_text(encoding="utf-8"))
            self.assertEqual(value[0]["schema_version"], "memory-gauntlet/v1")

    def test_bundle_rejects_output_symlinks_and_fifos_without_touching_targets(self):
        if not hasattr(os, "symlink") or not hasattr(os, "mkfifo"):
            self.skipTest("symbolic links and FIFOs are required")
        runs = [run_paths([str(SCENARIO)], "governed")]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = root / "output"
            output.mkdir()
            victim = root / "victim.txt"
            victim.write_text("sentinel", encoding="utf-8")
            (output / "report.md").symlink_to(victim)
            with self.assertRaisesRegex(ValueError, "non-regular"):
                write_bundle(runs, str(output))
            self.assertEqual(victim.read_text(encoding="utf-8"), "sentinel")
            (output / "report.md").unlink()
            os.mkfifo(str(output / "report.md"))
            with self.assertRaisesRegex(ValueError, "non-regular"):
                write_bundle(runs, str(output))
            real_output = root / "real-output"
            real_output.mkdir()
            linked_output = root / "linked-output"
            linked_output.symlink_to(real_output, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "must not be a symbolic link"):
                write_bundle(runs, str(linked_output))

            victim_directory = root / "victim-directory"
            victim_directory.mkdir()
            attacker_link = root / "attacker-link"
            attacker_link.symlink_to(victim_directory, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "must not be a symbolic link"):
                write_bundle(runs, str(attacker_link / "real-subdir"))
            self.assertFalse((victim_directory / "real-subdir").exists())

            relative_output = os.path.relpath(
                attacker_link / "relative-subdir", Path.cwd()
            )
            self.assertIn("..", Path(relative_output).parts)
            with self.assertRaisesRegex(ValueError, "must not be a symbolic link"):
                write_bundle(runs, relative_output)
            self.assertFalse((victim_directory / "relative-subdir").exists())

            with mock.patch.object(
                safeio, "_supports_descriptor_relative_io", return_value=False
            ):
                with self.assertRaisesRegex(OSError, "descriptor-relative"):
                    write_bundle(runs, str(attacker_link / "fallback-subdir"))
            self.assertFalse((victim_directory / "fallback-subdir").exists())

    def test_bundle_rolls_back_if_second_artifact_publication_fails(self):
        runs = [run_paths([str(SCENARIO)], "governed")]
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "output"
            write_bundle(runs, str(output))
            for path in output.iterdir():
                path.write_text("original:%s\n" % path.name, encoding="utf-8")
            original = {path.name: path.read_bytes() for path in output.iterdir()}

            real_rename = safeio._rename_at
            publications = 0

            def fail_second(directory_fd, source, target):
                nonlocal publications
                if ".tmp-" in source and not target.startswith("."):
                    publications += 1
                    if publications == 2:
                        raise OSError("injected second publication failure")
                return real_rename(directory_fd, source, target)

            with mock.patch.object(safeio, "_rename_at", side_effect=fail_second):
                with self.assertRaisesRegex(OSError, "second publication"):
                    write_bundle(runs, str(output))

            restored = {path.name: path.read_bytes() for path in output.iterdir()}
            self.assertEqual(restored, original)
            self.assertFalse(
                any(
                    ".tmp-" in path.name or ".bak-" in path.name
                    for path in output.iterdir()
                )
            )

    def test_expectations_and_operands_are_strictly_typed(self):
        scenario = load_scenario(str(SCENARIO))
        variants = []

        empty_expect = copy.deepcopy(scenario)
        empty_expect["steps"][1]["expect"] = {}
        variants.append((empty_expect, "at least one assertion"))

        string_list = copy.deepcopy(scenario)
        string_list["steps"][1]["expect"]["visible_ids"] = "timezone"
        variants.append((string_list, "list of non-empty strings"))

        non_string_operand = copy.deepcopy(scenario)
        non_string_operand["steps"][1]["expect"]["contains"] = [7]
        variants.append((non_string_operand, "only non-empty strings"))

        bad_ttl = copy.deepcopy(scenario)
        bad_ttl["steps"][7]["ttl"] = -1
        variants.append((bad_ttl, "ttl must be an integer from 0 through"))

        bool_ttl = copy.deepcopy(scenario)
        bool_ttl["steps"][7]["ttl"] = True
        variants.append((bool_ttl, "ttl must be an integer from 0 through"))

        bad_readers = copy.deepcopy(scenario)
        bad_readers["steps"][0]["readers"] = "product"
        variants.append((bad_readers, "readers must be a list"))

        unknown_reader = copy.deepcopy(scenario)
        unknown_reader["steps"][0]["readers"] = ["unknown-role"]
        variants.append((unknown_reader, "unknown principals or roles"))

        bad_query = copy.deepcopy(scenario)
        bad_query["steps"][1]["query"] = 7
        variants.append((bad_query, "query must be a non-empty string"))

        bad_limit = copy.deepcopy(scenario)
        bad_limit["steps"][1]["limit"] = 0
        variants.append((bad_limit, "limit must be a positive integer"))

        for value, expected_message in variants:
            with self.subTest(expected_message=expected_message):
                self.assertTrue(
                    any(expected_message in error for error in validate_scenario(value)),
                    validate_scenario(value),
                )

    def test_category_expectations_must_exercise_the_named_dimension(self):
        scenario = load_scenario(str(SCENARIO))
        correction = copy.deepcopy(scenario)
        correction["steps"][3]["expect"] = {"visible_ids": ["timezone"]}
        self.assertTrue(
            any(
                "visible current and excludes stale" in error
                for error in validate_scenario(correction)
            )
        )
        deletion = copy.deepcopy(scenario)
        deletion["steps"][6]["expect"] = {"contains": ["anything"]}
        self.assertTrue(
            any("requires a hidden assertion" in error for error in validate_scenario(deletion))
        )

    def test_categories_reject_assertion_keys_from_other_dimensions(self):
        scenario = load_scenario(str(SCENARIO))
        variants = []

        recall = copy.deepcopy(scenario)
        recall["steps"][1]["expect"]["hidden_ids"] = ["timezone"]
        variants.append((recall, "recall query does not allow"))

        correction = copy.deepcopy(scenario)
        correction["steps"][3]["expect"]["hidden_ids"] = ["timezone"]
        variants.append((correction, "correction query does not allow"))

        privacy = copy.deepcopy(scenario)
        privacy["steps"][14]["expect"]["contains"] = ["secret"]
        variants.append((privacy, "privacy query does not allow"))

        for value, message in variants:
            with self.subTest(message=message):
                self.assertTrue(
                    any(message in error for error in validate_scenario(value)),
                    validate_scenario(value),
                )

    def test_correction_current_and_stale_assertions_bind_the_same_record(self):
        scenario = {
            "schema_version": "memory-gauntlet-scenario/v1",
            "id": "same-record-correction",
            "principals": [{"id": "alice", "role": "owner"}],
            "steps": [
                {
                    "op": "write",
                    "actor": "alice",
                    "memory_id": "alpha-a",
                    "text": "alpha stale one",
                    "readers": [],
                },
                {
                    "op": "correct",
                    "actor": "alice",
                    "memory_id": "alpha-a",
                    "text": "alpha current one",
                },
                {
                    "op": "write",
                    "actor": "alice",
                    "memory_id": "alpha-b",
                    "text": "alpha stale two",
                    "readers": [],
                },
                {
                    "op": "correct",
                    "actor": "alice",
                    "memory_id": "alpha-b",
                    "text": "alpha current two",
                },
                {
                    "op": "query",
                    "actor": "alice",
                    "query": "alpha",
                    "category": "correction",
                    "expect": {
                        "visible_ids": ["alpha-a"],
                        "excludes": ["stale two"],
                    },
                },
            ],
        }
        errors = validate_scenario(scenario)
        self.assertTrue(any("correction assertions" in error for error in errors), errors)

    def test_hidden_assertion_must_be_reachable_with_declared_query_limit(self):
        scenario = {
            "schema_version": "memory-gauntlet-scenario/v1",
            "id": "bounded-hidden-target",
            "principals": [
                {"id": "alice", "role": "owner"},
                {"id": "bob", "role": "guest"},
            ],
            "steps": [
                {
                    "op": "write",
                    "actor": "alice",
                    "memory_id": "a-distractor",
                    "text": "orchid distractor",
                    "readers": [],
                },
                {
                    "op": "write",
                    "actor": "alice",
                    "memory_id": "z-secret",
                    "text": "orchid target",
                    "readers": [],
                },
                {
                    "op": "query",
                    "actor": "bob",
                    "query": "orchid",
                    "limit": 1,
                    "category": "privacy",
                    "expect": {"hidden_ids": ["z-secret"]},
                },
            ],
        }
        errors = validate_scenario(scenario)
        self.assertTrue(any("privacy assertions" in error for error in errors), errors)

        scenario["steps"][2]["limit"] = 2
        self.assertEqual(validate_scenario(scenario), [])

    def test_logical_time_and_sqlite_integer_bounds_fail_cleanly(self):
        scenario = load_scenario(str(SCENARIO))
        too_large_ttl = copy.deepcopy(scenario)
        too_large_ttl["steps"][0]["ttl"] = MAX_LOGICAL_TIME + 1
        self.assertTrue(
            any("ttl must be an integer" in error for error in validate_scenario(too_large_ttl))
        )

        overflow = copy.deepcopy(scenario)
        overflow["steps"] = [
            {"op": "advance", "seconds": MAX_LOGICAL_TIME},
            {"op": "advance", "seconds": 1},
            overflow["steps"][1],
        ]
        self.assertTrue(
            any("bounded logical-time range" in error for error in validate_scenario(overflow))
        )

        adapter = GovernedSQLiteAdapter()
        try:
            with self.assertRaisesRegex(ValueError, "logical-time/SQLite integer range"):
                adapter.advance(MAX_LOGICAL_TIME + 1)
            adapter.now = MAX_LOGICAL_TIME
            with self.assertRaisesRegex(ValueError, "logical-time/SQLite integer range"):
                adapter.write("overflow", "alice", "value", [], ttl=1)
            adapter.now = MAX_LOGICAL_TIME + 1
            with self.assertRaisesRegex(ValueError, "SQLite integer range"):
                adapter.write("invalid-now", "alice", "value", [])
        finally:
            adapter.close()

    def test_governance_categories_reject_nonexistent_vacuous_assertions(self):
        scenario = {
            "schema_version": "memory-gauntlet-scenario/v1",
            "id": "vacuous-governance",
            "principals": [{"id": "alice", "role": "product"}],
            "steps": [
                {
                    "op": "write",
                    "actor": "alice",
                    "memory_id": "current",
                    "text": "current fact",
                    "readers": [],
                },
                {
                    "op": "query",
                    "actor": "alice",
                    "query": "current fact",
                    "category": "recall",
                    "expect": {"visible_ids": ["current"]},
                },
                {
                    "op": "query",
                    "actor": "alice",
                    "query": "current fact",
                    "category": "correction",
                    "expect": {
                        "visible_ids": ["current"],
                        "excludes": ["never-stale"],
                    },
                },
                {
                    "op": "query",
                    "actor": "alice",
                    "query": "current fact",
                    "category": "deletion",
                    "expect": {"hidden_ids": ["never-deleted"]},
                },
                {
                    "op": "query",
                    "actor": "alice",
                    "query": "current fact",
                    "category": "ttl",
                    "expect": {"hidden_ids": ["never-expired"]},
                },
                {
                    "op": "query",
                    "actor": "alice",
                    "query": "current fact",
                    "category": "privacy",
                    "expect": {"hidden_ids": ["never-restricted"]},
                },
            ],
        }
        errors = validate_scenario(scenario)
        for category in ("correction", "deletion", "ttl", "privacy"):
            self.assertTrue(
                any(category in error for error in errors),
                "%s missing from %r" % (category, errors),
            )
        with self.assertRaisesRegex(ValueError, "invalid scenario"):
            run_scenarios([scenario], LeakyAppendOnlyAdapter())

    def test_every_hidden_assertion_must_bind_and_cannot_dilute_failures(self):
        scenario = {
            "schema_version": "memory-gauntlet-scenario/v1",
            "id": "no-dilution",
            "principals": [
                {"id": "alice", "role": "owner"},
                {"id": "bob", "role": "guest"},
            ],
            "steps": [
                {
                    "op": "write",
                    "actor": "alice",
                    "memory_id": "real-secret",
                    "text": "launch orchid",
                    "readers": [],
                },
                {
                    "op": "query",
                    "actor": "bob",
                    "query": "launch orchid",
                    "category": "privacy",
                    "expect": {
                        "hidden_ids": ["real-secret"]
                        + ["invented-%d" % index for index in range(99)]
                    },
                },
            ],
        }
        errors = validate_scenario(scenario)
        self.assertTrue(any("privacy assertions" in error for error in errors), errors)

        scenario["steps"][1]["expect"] = {"hidden_ids": ["real-secret"]}
        result = run_scenarios([scenario], LeakyAppendOnlyAdapter())
        self.assertEqual(result["scorecard"]["privacy_leakage_rate"], 1.0)
        self.assertEqual(result["scorecard"]["role_isolation"], 0.0)

    def test_dimension_assertions_bind_to_affected_query_relevant_records(self):
        scenario = load_scenario(str(SCENARIO))
        variants = []

        correction = copy.deepcopy(scenario)
        correction["steps"][3]["expect"]["excludes"] = ["never-stale"]
        variants.append((correction, "correction assertions"))

        deletion = copy.deepcopy(scenario)
        deletion["steps"][6]["expect"] = {"hidden_ids": ["never-deleted"]}
        variants.append((deletion, "deletion assertions"))

        ttl = copy.deepcopy(scenario)
        ttl["steps"][9]["expect"] = {"hidden_ids": ["never-expired"]}
        variants.append((ttl, "ttl assertions"))

        privacy = copy.deepcopy(scenario)
        privacy["steps"][14]["actor"] = "dana"
        variants.append((privacy, "privacy assertions"))

        irrelevant = copy.deepcopy(scenario)
        irrelevant["steps"][6]["query"] = "unrelated galaxy"
        variants.append((irrelevant, "query-relevant"))

        for value, message in variants:
            with self.subTest(message=message):
                self.assertTrue(
                    any(message in error for error in validate_scenario(value)),
                    validate_scenario(value),
                )

    def test_unexercised_dimensions_are_null_and_excluded_from_composite(self):
        scenario = {
            "schema_version": "memory-gauntlet-scenario/v1",
            "id": "recall-only",
            "principals": [{"id": "alice", "role": "product"}],
            "steps": [
                {
                    "op": "write",
                    "actor": "alice",
                    "memory_id": "timezone",
                    "text": "Timezone UTC",
                    "readers": [],
                },
                {
                    "op": "query",
                    "actor": "alice",
                    "query": "timezone",
                    "category": "recall",
                    "expect": {"visible_ids": ["timezone"]},
                },
            ],
        }
        result = run_scenarios([scenario], GovernedSQLiteAdapter())
        score = result["scorecard"]
        self.assertEqual(score["recall"], 1.0)
        self.assertIsNone(score["stale_rate"])
        self.assertIsNone(score["deletion_compliance"])
        self.assertIsNone(score["ttl_compliance"])
        self.assertIsNone(score["privacy_leakage_rate"])
        self.assertEqual(score["exercised_dimensions"], ["recall"])
        self.assertEqual(score["coverage"], 0.2)
        with tempfile.TemporaryDirectory() as temp:
            output = write_bundle([result], temp)
            self.assertIn("n/a", (output / "report.md").read_text(encoding="utf-8"))
            serialized = (output / "scorecard.json").read_text(encoding="utf-8")
            self.assertIn('"ttl_compliance": null', serialized)

    def test_zero_scenarios_and_scenarios_without_queries_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "at least one scenario"):
            run_scenarios([], GovernedSQLiteAdapter())
        scenario = load_scenario(str(SCENARIO))
        scenario["steps"] = [step for step in scenario["steps"] if step["op"] != "query"]
        self.assertTrue(
            any("at least one query" in error for error in validate_scenario(scenario))
        )

    def test_markdown_report_neutralizes_scenario_identifiers(self):
        scenario = load_scenario(str(SCENARIO))
        scenario["id"] = "safe`\n\n## Injected benchmark\n<img src=x onerror=alert(1)>"
        result = run_scenarios([scenario], GovernedSQLiteAdapter())
        report = markdown_report([result])
        self.assertNotIn("## Injected benchmark", report)
        self.assertNotIn("<img", report)
        self.assertIn("&#96;", report)


if __name__ == "__main__":
    unittest.main()
