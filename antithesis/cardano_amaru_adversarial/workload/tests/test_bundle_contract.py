<<<<<<< HEAD
import json
import os
import subprocess
=======
>>>>>>> 6082f0eedabe478801051a661435a5ffb3424f47
import unittest
from pathlib import Path


BUNDLE = Path(__file__).resolve().parents[2]


class BundleContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compose = (BUNDLE / "docker-compose.yaml").read_text(encoding="utf-8")

    def test_public_bundle_does_not_mount_genesis_signing_keys(self):
        self.assertNotIn("utxo-keys", self.compose)
        self.assertNotIn("genesis.1.skey", self.compose)

    def test_mixed_submission_services_and_static_fixture_are_wired(self):
        self.assertIn("cardano-phase1-reference:", self.compose)
        self.assertIn("cardano-submit-api:", self.compose)
        self.assertIn("mixed-phase1-workload:", self.compose)
        self.assertNotIn("phase1-fixture:", self.compose)
        self.assertTrue((BUNDLE / "fixture" / "static" / "underfee.tx").is_file())
        self.assertTrue((BUNDLE / "fixture" / "static" / "metadata.json").is_file())

<<<<<<< HEAD
    def test_signed_fee_corpus_has_expected_boundary_family(self):
        fixture_root = BUNDLE / "fixture" / "static"
        manifest = json.loads((fixture_root / "corpus.json").read_text(encoding="utf-8"))
        cases = manifest["cases"]

        self.assertEqual(len(cases), 5)
        self.assertEqual(sorted(case["fee_delta"] for case in cases), [-100, -2, -1, 0, 1])
        self.assertEqual(len({case["case_id"] for case in cases}), 5)
        self.assertTrue(all((fixture_root / case["tx_file"]).is_file() for case in cases))
        self.assertTrue(all(len(case["cbor_sha256"]) == 64 for case in cases))
        self.assertTrue(all(case["cbor_size"] > 0 for case in cases))

        accepted = [case for case in cases if case["expected"] == "accepted"]
        self.assertEqual(len(accepted), 2)
        self.assertEqual(len({case["input"] for case in accepted}), 2)

    def test_signed_fee_corpus_has_a_reproducible_structural_verifier(self):
        verifier = BUNDLE / "fixture" / "verify-corpus-container.sh"
        python_verifier = BUNDLE / "fixture" / "verify_corpus.py"
        self.assertTrue(verifier.is_file())
        self.assertTrue(python_verifier.is_file())
        source = python_verifier.read_text(encoding="utf-8")
        self.assertIn("transaction", source)
        self.assertIn("txid", source)
        self.assertIn("debug", source)
        self.assertIn("cbor_sha256", source)
        completed = subprocess.run(
            [str(verifier)], check=True, capture_output=True, text=True
        )
        self.assertIn("verified 5 signed fee-corpus transactions", completed.stdout)

    def test_public_bundle_contains_no_signing_keys(self):
        self.assertEqual(list(BUNDLE.rglob("*.skey")), [])

=======
>>>>>>> 6082f0eedabe478801051a661435a5ffb3424f47
    def test_reference_image_contains_matching_public_chain_state(self):
        dockerfile = (BUNDLE / "reference-image" / "Dockerfile").read_text(
            encoding="utf-8"
        )
        self.assertIn("COPY state/ /state/", dockerfile)
        self.assertIn("COPY configs/ /configs/", dockerfile)
        self.assertEqual(
            (BUNDLE / "reference-image" / "state" / "protocolMagicId").read_text(
                encoding="ascii"
            ),
            "42",
        )

    def test_amaru_submit_api_is_enabled(self):
        entrypoint = (BUNDLE / "relay-image" / "entrypoint.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("--submit-api-address", entrypoint)

    def test_bind_mounted_amaru_entrypoints_use_shell_interpreter(self):
        safe_invocation = "exec /bin/sh /usr/local/bin/dwarf-amaru-entrypoint.sh"
        self.assertEqual(self.compose.count(safe_invocation), 2)
        self.assertNotIn(
            "exec /usr/local/bin/dwarf-amaru-entrypoint.sh", self.compose
        )

    def test_workload_image_catalogs_assertions_and_commands(self):
        dockerfile_path = BUNDLE / "workload" / "Dockerfile"
        if not dockerfile_path.exists():
            self.fail("mixed phase-1 workload Dockerfile has not been implemented")
        dockerfile = dockerfile_path.read_text(encoding="utf-8")
        self.assertIn("/opt/antithesis/catalog/", dockerfile)
        self.assertIn("/opt/antithesis/test/v1/mixed-phase1/", dockerfile)
        self.assertIn("fixture/static/ /fixture/", dockerfile)

<<<<<<< HEAD
    def test_workload_runtime_dependencies_are_reproducibly_pinned(self):
        dockerfile = (BUNDLE / "workload" / "Dockerfile").read_text(encoding="utf-8")
        first_line = dockerfile.splitlines()[0]
        self.assertIn("python:3.11-slim@sha256:", first_line)
        self.assertIn("antithesis==0.2.0", dockerfile)
        self.assertNotIn("pip install --no-cache-dir antithesis\n", dockerfile)

    def test_fee_corpus_commands_are_present_and_executable(self):
        command_root = BUNDLE / "workload" / "test" / "v1" / "mixed-phase1"
        commands = {
            "first_fee_valid_boundaries.py",
            "parallel_driver_underfee.py",
            "parallel_driver_underfee_corpus.py",
            "eventually_underfee_recovery.py",
        }
        for name in commands:
            path = command_root / name
            self.assertTrue(path.is_file(), name)
            self.assertTrue(os.access(path, os.X_OK), name)

    def test_parallel_and_recovery_commands_choose_only_negative_corpus_cases(self):
        command_root = BUNDLE / "workload" / "test" / "v1" / "mixed-phase1"
        for name in (
            "parallel_driver_underfee_corpus.py",
            "eventually_underfee_recovery.py",
        ):
            source = (command_root / name).read_text(encoding="utf-8")
            self.assertIn("random_choice", source)
            self.assertIn("select_negative_case", source)
            self.assertNotIn("setup_complete", source)
            self.assertNotIn("send_event", source)

    def test_first_command_uses_readiness_gated_valid_boundary_helper(self):
        path = (
            BUNDLE
            / "workload"
            / "test"
            / "v1"
            / "mixed-phase1"
            / "first_fee_valid_boundaries.py"
        )
        source = path.read_text(encoding="utf-8")
        self.assertIn("probe_valid_boundaries", source)
        self.assertIn("PHASE1_READY_ATTEMPTS", source)
        self.assertIn("emit_readiness_assertion", source)
        self.assertNotIn("setup_complete", source)
        self.assertNotIn("send_event", source)

=======
>>>>>>> 6082f0eedabe478801051a661435a5ffb3424f47
    def test_fault_exclusions_are_explicit(self):
        self.assertNotIn("com.antithesis.exclude_from_faults: 'true'", self.compose)
        self.assertNotIn('com.antithesis.exclude_from_faults: "true"', self.compose)
        self.assertIn(
            "com.antithesis.exclude_from_faults: network,kill,pause,stop", self.compose
        )

    def test_images_are_literal_not_environment_interpolated(self):
        image_lines = [
            line.strip() for line in self.compose.splitlines() if line.strip().startswith("image:")
        ]
        self.assertGreater(len(image_lines), 0)
        self.assertTrue(all("${" not in line for line in image_lines), image_lines)

    def test_compose_identity_is_project_scoped_for_isolated_validation(self):
        self.assertNotIn("container_name:", self.compose)
        self.assertNotIn("name: d807-cardano-amaru-testnet", self.compose)
        self.assertNotIn("name: d807-cardano-amaru-consumer-net", self.compose)

if __name__ == "__main__":
    unittest.main()
