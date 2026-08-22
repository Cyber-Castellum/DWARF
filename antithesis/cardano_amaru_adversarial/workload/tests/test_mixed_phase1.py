import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


WORKLOAD_DIR = Path(__file__).resolve().parents[1]
if str(WORKLOAD_DIR) not in sys.path:
    sys.path.insert(0, str(WORKLOAD_DIR))


def load_subject(testcase):
    try:
        return importlib.import_module("mixed_phase1")
    except ModuleNotFoundError:
        testcase.fail("mixed_phase1 workload module has not been implemented")


class RecordingTransport:
    def __init__(self, observation):
        self.observation = observation
        self.payloads = []

    def send(self, payload):
        self.payloads.append(payload)
        return dict(self.observation)


class ResponseClassificationTests(unittest.TestCase):
    def test_success_is_accepted(self):
        subject = load_subject(self)
        self.assertEqual(subject.classify_response(202, ""), "accepted")

    def test_cardano_fee_too_small_is_phase1_rejection(self):
        subject = load_subject(self)
        body = "ShelleyTxValidationError ConwayMempoolFailure (FeeTooSmallUTxO 170000 169999)"
        self.assertEqual(subject.classify_response(400, body), "phase1_reject")

    def test_amaru_minimum_fee_failure_is_phase1_rejection(self):
        subject = load_subject(self)
        body = "failed to prepare transaction for validation: transaction fee below minimum"
        self.assertEqual(subject.classify_response(400, body), "phase1_reject")

    def test_amaru_validation_layer_rejection_is_phase1_rejection(self):
        subject = load_subject(self)
        body = "transaction " + ("40" * 32) + " is invalid"
        self.assertEqual(subject.classify_response(400, body), "phase1_reject")

    def test_amaru_preparation_failure_is_not_misclassified(self):
        subject = load_subject(self)
        body = "failed to prepare transaction deadbeef for validation"
        self.assertEqual(subject.classify_response(400, body), "unknown")

    def test_decoder_failure_is_decode_rejection(self):
        subject = load_subject(self)
        self.assertEqual(
            subject.classify_response(400, "Invalid CBOR transaction: unexpected break"),
            "decode_reject",
        )

    def test_unrecognized_http_error_stays_unknown(self):
        subject = load_subject(self)
        self.assertEqual(subject.classify_response(503, "upstream failed"), "unknown")

    def test_transport_error_is_unavailable(self):
        subject = load_subject(self)
        self.assertEqual(
            subject.classify_response(None, "", transport_error="TimeoutError"),
            "unavailable",
        )


class FixtureTests(unittest.TestCase):
    def test_load_fixture_decodes_text_envelope_and_metadata(self):
        subject = load_subject(self)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "underfee.tx").write_text(
                json.dumps({"type": "Tx ConwayEra", "cborHex": "84010203"}),
                encoding="utf-8",
            )
            (root / "metadata.json").write_text(
                json.dumps(
                    {
                        "minimum_fee": 170000,
                        "actual_fee": 169999,
                        "tx_id": "ab" * 32,
                    }
                ),
                encoding="utf-8",
            )

            fixture = subject.load_fixture(root)

        self.assertEqual(fixture.payload, bytes.fromhex("84010203"))
        self.assertEqual(fixture.minimum_fee, 170000)
        self.assertEqual(fixture.actual_fee, 169999)
        self.assertEqual(fixture.tx_id, "ab" * 32)

    def test_load_fixture_rejects_wrong_fee_boundary(self):
        subject = load_subject(self)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "underfee.tx").write_text(
                json.dumps({"type": "Tx ConwayEra", "cborHex": "84010203"}),
                encoding="utf-8",
            )
            (root / "metadata.json").write_text(
                json.dumps(
                    {
                        "minimum_fee": 170000,
                        "actual_fee": 169998,
                        "tx_id": "ab" * 32,
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "minimum fee minus one"):
                subject.load_fixture(root)


class DifferentialObservationTests(unittest.TestCase):
    def test_identical_bytes_are_submitted_and_phase1_rejections_agree(self):
        subject = load_subject(self)
        payload = bytes.fromhex("84010203")
        amaru = RecordingTransport(
            {"classification": "phase1_reject", "status": 400, "reason": "fee"}
        )
        cardano = RecordingTransport(
            {"classification": "phase1_reject", "status": 400, "reason": "fee"}
        )

        result = subject.observe_differential(
            payload, {"amaru": amaru, "cardano": cardano}
        )

        self.assertEqual(amaru.payloads, [payload])
        self.assertEqual(cardano.payloads, [payload])
        self.assertTrue(result["both_classifiable"])
        self.assertTrue(result["phase1_agreement"])
        self.assertFalse(result["any_accepted"])

    def test_unavailable_endpoint_is_inconclusive_not_disagreement(self):
        subject = load_subject(self)
        amaru = RecordingTransport(
            {"classification": "unavailable", "status": None, "reason": "timeout"}
        )
        cardano = RecordingTransport(
            {"classification": "phase1_reject", "status": 400, "reason": "fee"}
        )

        result = subject.observe_differential(
            b"same", {"amaru": amaru, "cardano": cardano}
        )

        self.assertFalse(result["both_classifiable"])
        self.assertIsNone(result["phase1_agreement"])
        self.assertFalse(result["any_accepted"])

    def test_acceptance_is_never_hidden_by_other_endpoint_failure(self):
        subject = load_subject(self)
        amaru = RecordingTransport(
            {"classification": "accepted", "status": 202, "reason": ""}
        )
        cardano = RecordingTransport(
            {"classification": "unavailable", "status": None, "reason": "reset"}
        )

        result = subject.observe_differential(
            b"same", {"amaru": amaru, "cardano": cardano}
        )

        self.assertTrue(result["any_accepted"])
        self.assertIsNone(result["phase1_agreement"])


if __name__ == "__main__":
    unittest.main()
