import unittest

from memory_gauntlet.adapters import GovernedSQLiteAdapter, LeakyAppendOnlyAdapter


class GovernedAdapterContractTests(unittest.TestCase):
    def setUp(self):
        self.adapter = GovernedSQLiteAdapter()

    def tearDown(self):
        self.adapter.close()

    def test_role_correction_deletion_and_ttl(self):
        self.adapter.write("m1", "alice", "timezone UTC", ["finance"])
        self.assertEqual([item.memory_id for item in self.adapter.query("bob", "finance", "timezone")], ["m1"])
        self.assertEqual(self.adapter.query("carol", "engineering", "timezone"), [])

        self.adapter.correct("m1", "alice", "timezone GST")
        result = self.adapter.query("alice", "product", "timezone")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].version, 2)
        self.assertIn("GST", result[0].text)

        self.adapter.delete("m1", "alice")
        self.assertEqual(self.adapter.query("alice", "product", "timezone"), [])

        self.adapter.write("ttl", "alice", "temporary code", [], ttl=10)
        self.adapter.advance(10)
        self.assertEqual(self.adapter.query("alice", "product", "temporary"), [])

    def test_non_owner_cannot_mutate(self):
        self.adapter.write("m1", "alice", "private plan", ["finance"])
        with self.assertRaises(ValueError):
            self.adapter.correct("m1", "bob", "tampered")
        with self.assertRaises(ValueError):
            self.adapter.delete("m1", "bob")


class LeakyAdapterTests(unittest.TestCase):
    def test_baseline_intentionally_leaks_and_keeps_stale_versions(self):
        adapter = LeakyAppendOnlyAdapter()
        adapter.write("m1", "alice", "timezone UTC", ["product"], ttl=1)
        adapter.correct("m1", "alice", "timezone GST")
        adapter.delete("m1", "alice")
        adapter.advance(100)
        result = adapter.query("eve", "visitor", "timezone")
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].version, 1)
        self.assertIn("UTC", result[0].text)


if __name__ == "__main__":
    unittest.main()
