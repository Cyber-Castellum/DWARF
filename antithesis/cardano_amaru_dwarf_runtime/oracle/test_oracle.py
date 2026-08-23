import unittest

from oracle import OracleState


NEW = "tip.adopt slot=1353 header_hash=abc123 block_height=77"
OLD = "adopted tip tip.slot=1356 tip.hash=def456 block_height=78"


class OracleStateTests(unittest.TestCase):
    def test_accepts_both_known_amaru_adoption_formats(self):
        state = OracleState()
        state.ingest_control(NEW + "\n" + OLD)
        self.assertEqual(state.control_by_height, {77: "abc123", 78: "def456"})

    def test_progress_is_measured_after_first_observation(self):
        state = OracleState()
        state.ingest_control(NEW)
        self.assertFalse(state.control_advanced)
        state.ingest_control("tip.adopt slot=1357 header_hash=def456 block_height=78")
        self.assertTrue(state.control_advanced)

    def test_transient_equal_height_forks_are_not_called_dwarf_acceptance(self):
        state = OracleState()
        state.ingest_control(NEW)
        state.ingest_target("tip.adopt slot=1353 header_hash=bad999 block_height=77")
        self.assertEqual(state.violations, [])

    def test_log_arrival_order_does_not_create_a_false_violation(self):
        state = OracleState()
        state.ingest_target("tip.adopt slot=1353 header_hash=bad999 block_height=77")
        state.ingest_control(NEW)
        self.assertEqual(state.violations, [])

    def test_decode_rejection_proves_mutation_path_coverage(self):
        state = OracleState()
        state.ingest_target("failed to decode message from network: Invalid CBOR")
        self.assertTrue(state.target_rejected_mutation)

    def test_panic_is_a_robustness_violation(self):
        state = OracleState()
        state.ingest_target("thread panicked at decoder")
        self.assertTrue(state.target_panicked)


if __name__ == "__main__":
    unittest.main()
