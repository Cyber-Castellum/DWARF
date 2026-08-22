import importlib
<<<<<<< HEAD
import hashlib
=======
>>>>>>> 6082f0eedabe478801051a661435a5ffb3424f47
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


<<<<<<< HEAD
class PayloadTransport:
    def __init__(self, observations):
        self.observations = observations
        self.payloads = []

    def send(self, payload):
        self.payloads.append(payload)
        return dict(self.observations[payload])


=======
>>>>>>> 6082f0eedabe478801051a661435a5ffb3424f47
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


<<<<<<< HEAD
class CorpusTests(unittest.TestCase):
    def write_case(self, root, case_id, *, fee_delta=-1, expected="phase1_reject", tx_file=None, tx_id=None):
        tx_file = tx_file or f"{case_id}.tx"
        tx_id = tx_id or ("ab" * 32)
        payload = bytes.fromhex("84010203")
        (root / tx_file).write_text(
            json.dumps({"type": "Tx ConwayEra", "cborHex": payload.hex()}),
            encoding="utf-8",
        )
        return {
            "case_id": case_id,
            "tx_file": tx_file,
            "minimum_fee": 170000,
            "actual_fee": 170000 + fee_delta,
            "fee_delta": fee_delta,
            "tx_id": tx_id,
            "cbor_sha256": hashlib.sha256(payload).hexdigest(),
            "cbor_size": len(payload),
            "expected": expected,
            "input": f"{case_id}-input#0",
        }

    def write_manifest(self, root, cases):
        (root / "corpus.json").write_text(
            json.dumps({"cases": cases}), encoding="utf-8"
        )

    def test_load_corpus_loads_unique_cases_and_expected_outcomes(self):
        subject = load_subject(self)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cases = [
                self.write_case(root, "minus-one", fee_delta=-1),
                self.write_case(
                    root, "exact", fee_delta=0, expected="accepted", tx_id="cd" * 32
                ),
            ]
            self.write_manifest(root, cases)

            corpus = subject.load_corpus(root)

        self.assertEqual([case.case_id for case in corpus], ["minus-one", "exact"])
        self.assertEqual([case.fee_delta for case in corpus], [-1, 0])
        self.assertEqual(
            [case.expected for case in corpus], ["phase1_reject", "accepted"]
        )

    def test_load_corpus_rejects_fee_delta_mismatch(self):
        subject = load_subject(self)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            case = self.write_case(root, "bad-delta", fee_delta=-1)
            case["fee_delta"] = -2
            self.write_manifest(root, [case])

            with self.assertRaisesRegex(ValueError, "fee delta"):
                subject.load_corpus(root)

    def test_load_corpus_rejects_unknown_expected_outcome(self):
        subject = load_subject(self)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            case = self.write_case(root, "bad-outcome", expected="unknown")
            self.write_manifest(root, [case])

            with self.assertRaisesRegex(ValueError, "expected outcome"):
                subject.load_corpus(root)

    def test_load_corpus_rejects_duplicate_case_ids(self):
        subject = load_subject(self)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = self.write_case(root, "duplicate", tx_file="first.tx")
            second = self.write_case(root, "duplicate", tx_file="second.tx")
            self.write_manifest(root, [first, second])

            with self.assertRaisesRegex(ValueError, "duplicate case id"):
                subject.load_corpus(root)

    def test_load_corpus_rejects_transaction_path_traversal(self):
        subject = load_subject(self)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root.parent / "outside.tx"
            outside.write_text(
                json.dumps({"type": "Tx ConwayEra", "cborHex": "84010203"}),
                encoding="utf-8",
            )
            case = self.write_case(root, "traversal")
            case["tx_file"] = "../outside.tx"
            self.write_manifest(root, [case])

            with self.assertRaisesRegex(ValueError, "inside fixture root"):
                subject.load_corpus(root)
            outside.unlink()

    def test_load_corpus_rejects_malformed_transaction_id(self):
        subject = load_subject(self)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            case = self.write_case(root, "bad-txid", tx_id="not-a-txid")
            self.write_manifest(root, [case])

            with self.assertRaisesRegex(ValueError, "transaction id"):
                subject.load_corpus(root)

    def test_load_corpus_rejects_missing_transaction_file(self):
        subject = load_subject(self)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            case = self.write_case(root, "missing")
            (root / case["tx_file"]).unlink()
            self.write_manifest(root, [case])

            with self.assertRaisesRegex((FileNotFoundError, ValueError), "missing"):
                subject.load_corpus(root)

    def test_load_corpus_rejects_payload_hash_mismatch(self):
        subject = load_subject(self)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            case = self.write_case(root, "hash-mismatch")
            case["cbor_sha256"] = "00" * 32
            self.write_manifest(root, [case])

            with self.assertRaisesRegex(ValueError, "CBOR SHA-256"):
                subject.load_corpus(root)

    def test_load_corpus_rejects_payload_size_mismatch(self):
        subject = load_subject(self)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            case = self.write_case(root, "size-mismatch")
            case["cbor_size"] += 1
            self.write_manifest(root, [case])

            with self.assertRaisesRegex(ValueError, "CBOR size"):
                subject.load_corpus(root)


class CorpusExecutionTests(unittest.TestCase):
    def fixture(self, subject, case_id, payload, fee_delta, expected):
        return subject.Fixture(
            payload=payload,
            minimum_fee=170000,
            actual_fee=170000 + fee_delta,
            tx_id=(payload.hex() * 64)[:64],
            case_id=case_id,
            fee_delta=fee_delta,
            expected=expected,
            input=f"{case_id}#0",
        )

    def corpus(self, subject):
        return [
            self.fixture(subject, "minus-100", b"a", -100, "phase1_reject"),
            self.fixture(subject, "underfee-minus-1", b"b", -1, "phase1_reject"),
            self.fixture(subject, "exact", b"c", 0, "accepted"),
            self.fixture(subject, "plus-one", b"d", 1, "accepted"),
        ]

    def test_random_selection_is_offered_only_negative_cases(self):
        subject = load_subject(self)
        offered = []

        def chooser(cases):
            offered.extend(cases)
            return cases[0]

        selected = subject.select_negative_case(self.corpus(subject), chooser)

        self.assertEqual(selected.case_id, "minus-100")
        self.assertEqual(
            [case.case_id for case in offered], ["minus-100", "underfee-minus-1"]
        )

    def test_valid_cases_run_once_and_only_after_readiness_probe(self):
        subject = load_subject(self)
        corpus = self.corpus(subject)
        observations = {
            b"b": {"classification": "phase1_reject", "status": 400, "reason": "fee"},
            b"c": {"classification": "accepted", "status": 202, "reason": ""},
            b"d": {"classification": "accepted", "status": 202, "reason": ""},
        }
        amaru = PayloadTransport(observations)
        cardano = PayloadTransport(observations)

        execution = subject.probe_valid_boundaries(
            corpus,
            {"amaru": amaru, "cardano": cardano},
            readiness_attempts=3,
            pause=lambda _seconds: None,
        )

        self.assertTrue(execution["ready"])
        self.assertEqual(
            [fixture.case_id for fixture, _result in execution["valid"]],
            ["exact", "plus-one"],
        )
        self.assertEqual(amaru.payloads, [b"b", b"c", b"d"])
        self.assertEqual(cardano.payloads, [b"b", b"c", b"d"])

    def test_valid_cases_are_not_sent_when_readiness_never_succeeds(self):
        subject = load_subject(self)
        corpus = self.corpus(subject)
        unavailable = {
            b"b": {"classification": "unavailable", "status": None, "reason": "down"}
        }
        amaru = PayloadTransport(unavailable)
        cardano = PayloadTransport(unavailable)

        execution = subject.probe_valid_boundaries(
            corpus,
            {"amaru": amaru, "cardano": cardano},
            readiness_attempts=2,
            pause=lambda _seconds: None,
        )

        self.assertFalse(execution["ready"])
        self.assertEqual(execution["valid"], [])
        self.assertEqual(amaru.payloads, [b"b", b"b"])
        self.assertEqual(cardano.payloads, [b"b", b"b"])

    def test_assertion_details_identify_corpus_case_and_expected_outcome(self):
        subject = load_subject(self)
        fixture = self.fixture(subject, "exact", b"c", 0, "accepted")
        result = {
            "both_classifiable": True,
            "phase1_agreement": False,
            "any_accepted": True,
            "observations": {
                "amaru": {"classification": "accepted", "status": 202, "reason": ""},
                "cardano": {"classification": "accepted", "status": 202, "reason": ""},
            },
        }
        calls = []
        originals = subject.reachable, subject.sometimes, subject.always
        subject.reachable = lambda message, details: calls.append((message, details))
        subject.sometimes = lambda condition, message, details: calls.append((message, details))
        subject.always = lambda condition, message, details: calls.append((message, details))
        try:
            subject.emit_assertions(fixture, result)
        finally:
            subject.reachable, subject.sometimes, subject.always = originals

        self.assertGreater(len(calls), 0)
        self.assertTrue(all(details["case_id"] == "exact" for _message, details in calls))
        self.assertTrue(all(details["fee_delta"] == 0 for _message, details in calls))
        self.assertTrue(all(details["expected"] == "accepted" for _message, details in calls))

    def test_each_corpus_case_has_a_distinct_fixed_assertion_identity(self):
        subject = load_subject(self)
        expected_messages = {
            "underfee-minus-100": "underfee minus 100 matches on both implementations",
            "underfee-minus-2": "underfee minus 2 matches on both implementations",
            "underfee-minus-1": "underfee minus 1 matches on both implementations",
            "minimum-exact": "minimum exact fee matches on both implementations",
            "minimum-plus-1": "minimum plus 1 fee matches on both implementations",
        }
        result = {
            "both_classifiable": True,
            "phase1_agreement": True,
            "any_accepted": False,
            "observations": {
                "amaru": {"classification": "phase1_reject", "status": 400, "reason": "fee"},
                "cardano": {"classification": "phase1_reject", "status": 400, "reason": "fee"},
            },
        }
        calls = []
        originals = subject.reachable, subject.sometimes, subject.always
        subject.reachable = lambda message, details: calls.append(("reachable", message))
        subject.sometimes = lambda condition, message, details: calls.append(("sometimes", message))
        subject.always = lambda condition, message, details: calls.append(("always", message))
        try:
            fixtures = [
                self.fixture(subject, "underfee-minus-100", b"a", -100, "phase1_reject"),
                self.fixture(subject, "underfee-minus-2", b"e", -2, "phase1_reject"),
                self.fixture(subject, "underfee-minus-1", b"b", -1, "phase1_reject"),
                self.fixture(subject, "minimum-exact", b"c", 0, "accepted"),
                self.fixture(subject, "minimum-plus-1", b"d", 1, "accepted"),
            ]
            for fixture in fixtures:
                subject.emit_assertions(fixture, result)
        finally:
            subject.reachable, subject.sometimes, subject.always = originals

        emitted = {message for kind, message in calls if kind == "always"}
        self.assertTrue(set(expected_messages.values()).issubset(emitted))

    def test_readiness_has_a_dedicated_fixed_assertion_identity(self):
        subject = load_subject(self)
        fixture = self.fixture(subject, "underfee-minus-1", b"b", -1, "phase1_reject")
        result = {
            "both_classifiable": True,
            "phase1_agreement": True,
            "any_accepted": False,
            "observations": {
                "amaru": {"classification": "phase1_reject", "status": 400, "reason": "fee"},
                "cardano": {"classification": "phase1_reject", "status": 400, "reason": "fee"},
            },
        }
        calls = []
        original = subject.sometimes
        subject.sometimes = lambda condition, message, details: calls.append((condition, message))
        try:
            subject.emit_readiness_assertion(fixture, result)
        finally:
            subject.sometimes = original

        self.assertIn((True, "mixed fee-corpus readiness succeeds"), calls)

    def test_per_case_properties_distinguish_match_mismatch_and_unavailable(self):
        subject = load_subject(self)
        cases = [
            ("underfee-minus-100", -100, "phase1_reject", "underfee minus 100"),
            ("underfee-minus-2", -2, "phase1_reject", "underfee minus 2"),
            ("underfee-minus-1", -1, "phase1_reject", "underfee minus 1"),
            ("minimum-exact", 0, "accepted", "minimum exact fee"),
            ("minimum-plus-1", 1, "accepted", "minimum plus 1 fee"),
        ]

        def result(left, right):
            classifiable = left != "unavailable" and right != "unavailable"
            return {
                "both_classifiable": classifiable,
                "phase1_agreement": classifiable and left == right == "phase1_reject",
                "any_accepted": "accepted" in (left, right),
                "observations": {
                    "amaru": {"classification": left, "status": None, "reason": ""},
                    "cardano": {"classification": right, "status": None, "reason": ""},
                },
            }

        for case_id, fee_delta, expected, prefix in cases:
            fixture = self.fixture(subject, case_id, b"z", fee_delta, expected)
            opposite = "accepted" if expected == "phase1_reject" else "phase1_reject"
            scenarios = [
                (result(expected, expected), True, True),
                (result(expected, opposite), False, False),
                (result("unavailable", expected), False, True),
            ]
            for observation, expected_sometimes, expected_always in scenarios:
                calls = []
                originals = subject.reachable, subject.sometimes, subject.always
                subject.reachable = lambda message, details: None
                subject.sometimes = lambda condition, message, details: calls.append(("sometimes", condition, message))
                subject.always = lambda condition, message, details: calls.append(("always", condition, message))
                try:
                    subject.emit_assertions(fixture, observation)
                finally:
                    subject.reachable, subject.sometimes, subject.always = originals

                sometimes_call = next(call for call in calls if call[2] == f"{prefix} sometimes matches on both implementations")
                always_call = next(call for call in calls if call[2] == f"{prefix} matches on both implementations")
                self.assertEqual(sometimes_call[1], expected_sometimes, case_id)
                self.assertEqual(always_call[1], expected_always, case_id)


=======
>>>>>>> 6082f0eedabe478801051a661435a5ffb3424f47
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

<<<<<<< HEAD
    def test_matches_expected_acceptance_and_phase1_rejection(self):
        subject = load_subject(self)
        accepted = {
            "both_classifiable": True,
            "observations": {
                "amaru": {"classification": "accepted"},
                "cardano": {"classification": "accepted"},
            },
        }
        rejected = {
            "both_classifiable": True,
            "observations": {
                "amaru": {"classification": "phase1_reject"},
                "cardano": {"classification": "phase1_reject"},
            },
        }

        self.assertTrue(subject.matches_expected(accepted, "accepted"))
        self.assertTrue(subject.matches_expected(rejected, "phase1_reject"))
        self.assertFalse(subject.matches_expected(accepted, "phase1_reject"))

    def test_matches_expected_is_false_for_unavailable_observation(self):
        subject = load_subject(self)
        result = {
            "both_classifiable": False,
            "observations": {
                "amaru": {"classification": "unavailable"},
                "cardano": {"classification": "phase1_reject"},
            },
        }

        self.assertFalse(subject.matches_expected(result, "phase1_reject"))

=======
>>>>>>> 6082f0eedabe478801051a661435a5ffb3424f47

if __name__ == "__main__":
    unittest.main()
