import unittest
from pathlib import Path

from resolve_snapshot_points import parse_rows, select_points


FIXTURE = Path(__file__).with_name("fixtures") / "db-analyser-slots.txt"


class SnapshotPointResolverTests(unittest.TestCase):
    def test_selects_three_epoch_ends_with_immediate_block_parents(self):
        rows = parse_rows(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(
            select_points(rows, epoch_length=400),
            [
                "398.144e9761e8dc552f1ac74183d39e9c8b015e6e02efa19aa4323120010e7e6d8d::394.ab8a3dc911f63e56d09898528301d6c789127bacaa7dd016d477d2603d525600",
                "793.4c15ed9202c1341723b14b5d03c9e2223823af15c37d46af2c8c4559b6a3234d::790.d6396572f93ab1f3c1afdabe1eb16103dbc9f2cd06105f7eb4edbb60b84947c1",
                "1193.9aade6b65a323af6c456f1e292c27b501fa109a0a4db5e2f88dea72c34962500::1170.241b110715cdb4d1ce1fd588b3fa264dc8a514584ea7ff3089b903f5fb84148a",
            ],
        )

    def test_rejects_missing_immediate_parent_block(self):
        rows = [row for row in parse_rows(FIXTURE.read_text()) if row.block_no != 83]
        with self.assertRaisesRegex(ValueError, "parent"):
            select_points(rows, epoch_length=400)

    def test_rejects_conflicting_duplicate_block_numbers(self):
        text = FIXTURE.read_text() + "\nBlockNo 84 SlotNo 399 " + "f" * 64
        with self.assertRaisesRegex(ValueError, "conflicting"):
            parse_rows(text)

    def test_rejects_malformed_hashes(self):
        text = FIXTURE.read_text().replace("144e9761", "notahash")
        with self.assertRaisesRegex(ValueError, "malformed"):
            parse_rows(text)

    def test_rejects_fewer_than_three_completed_epochs(self):
        rows = [row for row in parse_rows(FIXTURE.read_text()) if row.slot < 800]
        with self.assertRaisesRegex(ValueError, "three completed epochs"):
            select_points(rows, epoch_length=400)

    def test_rejects_origin_only_output(self):
        with self.assertRaisesRegex(ValueError, "no block rows"):
            parse_rows("Started ShowSlotBlockNo\nOrigin")


if __name__ == "__main__":
    unittest.main()
