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

    def test_workload_image_catalogs_assertions_and_commands(self):
        dockerfile_path = BUNDLE / "workload" / "Dockerfile"
        if not dockerfile_path.exists():
            self.fail("mixed phase-1 workload Dockerfile has not been implemented")
        dockerfile = dockerfile_path.read_text(encoding="utf-8")
        self.assertIn("/opt/antithesis/catalog/", dockerfile)
        self.assertIn("/opt/antithesis/test/v1/mixed-phase1/", dockerfile)
        self.assertIn("fixture/static/ /fixture/", dockerfile)

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
