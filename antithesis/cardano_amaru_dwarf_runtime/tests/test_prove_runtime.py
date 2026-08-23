import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path


TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))

import prove_runtime


class ProofRunnerTests(unittest.TestCase):
    def test_compose_json_accepts_array_and_json_lines(self):
        rows = [{"Service": "p1", "State": "running"}, {"Service": "p2", "State": "exited"}]
        self.assertEqual(prove_runtime.parse_json_rows(json.dumps(rows)), rows)
        self.assertEqual(
            prove_runtime.parse_json_rows("\n".join(json.dumps(row) for row in rows)),
            rows,
        )

    def test_tip_json_requires_block_hash_and_slot(self):
        tip = prove_runtime.parse_tip(
            json.dumps({"block": 291, "hash": "a" * 64, "slot": 1515, "syncProgress": "100.00"})
        )
        self.assertEqual(tip.block, 291)
        self.assertEqual(tip.block_hash, "a" * 64)
        self.assertEqual(tip.slot, 1515)
        with self.assertRaises(ValueError):
            prove_runtime.parse_tip('{"block": 1, "slot": 2}')

    def test_relay_logs_capture_adoption_rejection_epoch_and_panics(self):
        healthy = "\n".join(
            (
                "adopted tip tip.slot=1598 tip.hash=" + "1" * 64 + " tip.block_height=300",
                "failed to decode message from network err=unexpected type array",
                "adopted tip tip.slot=1604 tip.hash=" + "2" * 64 + " tip.block_height=303",
            )
        )
        observation = prove_runtime.parse_relay_log(healthy)
        self.assertEqual(observation.first_height, 300)
        self.assertEqual(observation.max_height, 303)
        self.assertEqual(observation.first_slot, 1598)
        self.assertEqual(observation.max_slot, 1604)
        self.assertEqual(observation.decoder_rejections, 1)
        self.assertFalse(observation.fatal_signatures)

        bad = healthy + "\nthread panicked: discrepancy between expected total rewards and actual total rewards"
        self.assertIn("reward discrepancy", prove_runtime.parse_relay_log(bad).fatal_signatures)

        invalid_header = healthy + "\nheader validation failed: Invalid VRF proof: VerificationFailed"
        self.assertEqual(prove_runtime.parse_relay_log(invalid_header).consensus_rejections, 1)

    def test_condition_wait_polls_until_success_without_fixed_delay_assumptions(self):
        values = iter((0, 1, 2, 3))
        clock_values = iter((0.0, 0.1, 0.2, 0.3, 0.4))
        sleeps = []

        result = prove_runtime.wait_until(
            lambda: next(values),
            lambda value: value == 3,
            timeout=1,
            interval=0.01,
            description="three",
            clock=lambda: next(clock_values),
            sleeper=sleeps.append,
        )

        self.assertEqual(result, 3)
        self.assertEqual(sleeps, [0.01, 0.01, 0.01])

    def _healthy_samples(self):
        baseline = prove_runtime.ProofSample(
            bootstrap_exit=0,
            target=prove_runtime.RelayObservation(300, 300, 1598, 1598, 0, ()),
            control=prove_runtime.RelayObservation(300, 300, 1598, 1598, 0, ()),
            consumer=prove_runtime.Tip(300, "a" * 64, 1598),
            producers=(prove_runtime.Tip(300, "a" * 64, 1598),),
            restarts={"amaru-relay-1": 0, "amaru-relay-2": 0},
        )
        current = prove_runtime.ProofSample(
            bootstrap_exit=0,
            target=prove_runtime.RelayObservation(300, 305, 1598, 1604, 4, ()),
            control=prove_runtime.RelayObservation(300, 305, 1598, 1604, 0, ()),
            consumer=prove_runtime.Tip(304, "b" * 64, 1602),
            producers=(prove_runtime.Tip(304, "b" * 64, 1602),),
            restarts={"amaru-relay-1": 0, "amaru-relay-2": 0},
        )
        return baseline, current

    def test_proof_conditions_cover_every_required_runtime_signal(self):
        baseline, current = self._healthy_samples()
        conditions = prove_runtime.proof_conditions(baseline, current, epoch_length=400)

        self.assertTrue(all(conditions.values()), conditions)
        self.assertEqual(
            set(conditions),
            {
                "bootstrap_exit_zero",
                "target_advanced",
                "control_advanced",
                "epoch_crossed",
                "decoder_rejection_observed",
                "consumer_advanced",
                "consumer_matches_producer",
                "no_fatal_signatures",
                "control_consensus_clean",
                "relay_restart_counts_stable",
            },
        )

    def test_each_missing_signal_keeps_proof_unsatisfied(self):
        baseline, healthy = self._healthy_samples()
        cases = {
            "bootstrap_exit_zero": replace(healthy, bootstrap_exit=1),
            "target_advanced": replace(healthy, target=replace(healthy.target, max_height=300)),
            "control_advanced": replace(healthy, control=replace(healthy.control, max_height=300)),
            "epoch_crossed": replace(
                healthy,
                target=replace(healthy.target, max_slot=1599),
                control=replace(healthy.control, max_slot=1599),
            ),
            "decoder_rejection_observed": replace(
                healthy, target=replace(healthy.target, decoder_rejections=0)
            ),
            "consumer_advanced": replace(healthy, consumer=baseline.consumer),
            "consumer_matches_producer": replace(
                healthy, producers=(prove_runtime.Tip(304, "c" * 64, 1602),)
            ),
            "no_fatal_signatures": replace(
                healthy, control=replace(healthy.control, fatal_signatures=("panic",))
            ),
            "control_consensus_clean": replace(
                healthy, control=replace(healthy.control, consensus_rejections=1)
            ),
            "relay_restart_counts_stable": replace(
                healthy, restarts={"amaru-relay-1": 1, "amaru-relay-2": 0}
            ),
        }
        for condition, sample in cases.items():
            with self.subTest(condition=condition):
                self.assertFalse(
                    prove_runtime.proof_conditions(baseline, sample, epoch_length=400)[condition]
                )


if __name__ == "__main__":
    unittest.main()
