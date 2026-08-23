import json
import subprocess
import sys
import unittest
import re
from pathlib import Path


BUNDLE = Path(__file__).resolve().parents[1]
RUNTIME_IMAGE = "ghcr.io/j-gainsec/dwarf-amaru-807-runtime:0.1.0"


class BundleContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compose_path = BUNDLE / "docker-compose.yaml"
        cls.compose = compose_path.read_text(encoding="utf-8") if compose_path.exists() else ""

    def test_runtime_bootstrap_uses_the_live_cardano_lineage(self):
        self.assertIn("bootstrap-producer:", self.compose)
        self.assertIn("p1-state:/live:ro", self.compose)
        self.assertIn("bootstrap-state:/scratch", self.compose)
        self.assertIn("amaru-bundle:/output", self.compose)
        self.assertIn("/usr/local/bin/bootstrap.py", self.compose)

    def test_runtime_uses_epoch_transition_fixed_amaru(self):
        self.assertIn(RUNTIME_IMAGE, self.compose)
        self.assertIn("exec /usr/local/bin/amaru node run", self.compose)
        self.assertNotIn("--migrate-chain-db", self.compose)
        self.assertNotIn("cf657b91", self.compose)
        self.assertNotIn("e7d3aa4ce3bd14bd08a08b998b6486f", self.compose)

    def test_native_v5_bundle_gates_private_relay_stores(self):
        self.assertGreaterEqual(self.compose.count("READY.v5"), 3)
        self.assertIn("a1-state:/srv/amaru", self.compose)
        self.assertIn("a2-state:/srv/amaru", self.compose)
        self.assertIn("amaru-bundle:/bundle:ro", self.compose)
        self.assertIn("global-parameters.json", self.compose)

    def test_runtime_parameters_match_the_generated_network(self):
        era = json.loads((BUNDLE / "amaru-runtime" / "era-history.json").read_text(encoding="utf-8"))
        params = json.loads(
            (BUNDLE / "amaru-runtime" / "global-parameters.json").read_text(encoding="utf-8")
        )
        self.assertEqual(era["eras"][0]["params"]["epoch_size_slots"], 400)
        self.assertEqual(era["eras"][0]["params"]["slot_length"], 500)
        self.assertEqual(params["consensus_security_param"], 20)
        self.assertEqual(params["epoch_length_scale_factor"], 4)
        self.assertEqual(params["active_slot_coeff_inverse"], 5)
        self.assertEqual(params["system_start"], "from_shelley_genesis")

    def test_bootstrap_source_and_patches_are_locked(self):
        bootstrap = BUNDLE / "bootstrap-image"
        lock = (bootstrap / "SOURCE.lock").read_text(encoding="utf-8")
        self.assertIn("493bffba0cc4db2291643cdd6698197c374958b3", lock)
        patches = sorted((bootstrap / "patches").glob("*.patch"))
        self.assertEqual(
            [path.name for path in patches],
            [
                "0001-local-custom-bootstrap.patch",
                "0002-tvar-definite-map.patch",
                "0003-custom-global-parameters.patch",
                "0004-live-bootstrap-nonce.patch",
            ],
        )
        expected_paths = (
            "crates/amaru/src/bootstrap/mod.rs",
            "crates/amaru/src/cardano_node/tvar.rs",
            "crates/amaru/src/bin/amaru/cmd/dev/ledger/states/import.rs",
            "crates/amaru/src/bootstrap/mod.rs",
        )
        for patch_path, expected in zip(patches, expected_paths):
            self.assertIn(expected, patch_path.read_text(encoding="utf-8"))

    def test_dwarf_is_inserted_on_the_target_path_only(self):
        self.assertIn("dwarf-adversary:", self.compose)
        self.assertIn("--upstream relay1.example:3001", self.compose)
        target = self.compose.split("  amaru-relay-1:", 1)[1].split("  amaru-relay-2:", 1)[0]
        control = self.compose.split("  amaru-relay-2:", 1)[1].split("  amaru-consumer:", 1)[0]
        self.assertIn("--peer-address relay1.example:3001", target)
        self.assertIn("--peer-address dwarf-adversary.example:3001", target)
        self.assertNotIn("dwarf-adversary.example", control)

    def test_amaru_only_consumer_proves_end_to_end_serving(self):
        self.assertIn("amaru-consumer-seed:", self.compose)
        self.assertIn("amaru-consumer:", self.compose)
        topology = (BUNDLE / "amaru-consumer-topology.json").read_text(encoding="utf-8")
        self.assertIn("amaru-relay-1.example", topology)
        self.assertIn("amaru-relay-2.example", topology)
        self.assertNotIn("relay1.example", topology)
        self.assertNotIn("relay2.example", topology)

    def test_support_services_are_fault_excluded_and_runtime_named(self):
        excluded = (
            "configurator",
            "tracer",
            "p1",
            "p2",
            "p3",
            "relay1",
            "relay2",
            "bootstrap-producer",
            "amaru-consumer-seed",
            "amaru-consumer",
            "sidecar",
            "tracer-sidecar",
            "log-tailer",
            "dwarf-adversary",
            "dwarf-oracle",
        )
        for service in excluded:
            match = re.search(
                rf"(?ms)^  {re.escape(service)}:\n(.*?)(?=^  [a-zA-Z0-9_-]+:\n|^volumes:\n)",
                self.compose,
            )
            self.assertIsNotNone(match, service)
            section = match.group(1)
            self.assertIn(f"container_name: {service}", section, service)
            self.assertIn("labels:", section, service)

    def test_oracle_is_a_new_reproducible_catalogued_package(self):
        dockerfile = (BUNDLE / "oracle" / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("python:3.11-slim@sha256:", dockerfile)
        self.assertIn("antithesis==0.2.0", dockerfile)
        self.assertIn("/opt/antithesis/catalog/oracle.py", dockerfile)
        self.assertNotIn("|| true", dockerfile)

    def test_package_contains_no_sensitive_or_macos_metadata_files(self):
        forbidden = []
        for path in BUNDLE.rglob("*"):
            if path.name.startswith("._") or path.name == ".env" or path.suffix == ".skey":
                forbidden.append(path.relative_to(BUNDLE).as_posix())
        self.assertEqual(forbidden, [])

    def test_fault_target_checker_accepts_only_the_two_amaru_relays(self):
        services = {
            "amaru-relay-1": {},
            "amaru-relay-2": {},
            "p1": {
                "container_name": "p1",
                "labels": {"com.antithesis.exclude_from_faults": "network,kill,pause,stop"},
            },
        }
        result = subprocess.run(
            [sys.executable, str(BUNDLE / "tests" / "check_fault_targets.py")],
            input=json.dumps({"services": services}),
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_fault_target_checker_rejects_scope_or_runtime_name_drift(self):
        excluded = {"com.antithesis.exclude_from_faults": "network,kill,pause,stop"}
        bad_payloads = (
            {
                "services": {
                    "amaru-relay-1": {},
                    "amaru-relay-2": {},
                    "p1": {"container_name": "p1"},
                }
            },
            {
                "services": {
                    "amaru-relay-1": {},
                    "amaru-relay-2": {},
                    "p1": {"container_name": "wrong", "labels": excluded},
                }
            },
        )
        for payload in bad_payloads:
            with self.subTest(payload=payload):
                result = subprocess.run(
                    [sys.executable, str(BUNDLE / "tests" / "check_fault_targets.py")],
                    input=json.dumps(payload),
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
