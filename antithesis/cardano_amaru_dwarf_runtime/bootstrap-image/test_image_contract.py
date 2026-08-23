import hashlib
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent


class BootstrapImageContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = ROOT / "Dockerfile"
        cls.dockerfile = path.read_text(encoding="utf-8") if path.exists() else ""

    def test_source_and_build_tools_are_immutable(self):
        self.assertIn(
            "rust:bookworm@sha256:e70e2eec3d495fd5c8e0be74adda86507dfac7f51a724fbf9813ff59b2b247c7",
            self.dockerfile,
        )
        self.assertIn("493bffba0cc4db2291643cdd6698197c374958b3", self.dockerfile)
        self.assertIn("git checkout --detach", self.dockerfile)
        self.assertIn("git apply --check", self.dockerfile)
        self.assertIn("cargo build --release --locked --bin amaru", self.dockerfile)

    def test_runtime_contains_pinned_cardano_analyser_and_python(self):
        self.assertIn(
            "ghcr.io/intersectmbo/cardano-node@sha256:3275d357053d21f3220f74b0854fd584e1fe322dfa1bbb78effd760c3191d14c AS cardano",
            self.dockerfile,
        )
        self.assertIn(
            "python:3.11-slim@sha256:9c900dea9e8fb7e16277c179b555cc72d29a352dbc33cff48ad5a0412fd5bfc7 AS runtime",
            self.dockerfile,
        )
        self.assertIn("COPY --from=cardano /nix/store /nix/store", self.dockerfile)
        self.assertIn("/usr/local/bin/db-analyser", self.dockerfile)
        self.assertIn("/usr/local/bin/cardano-cli", self.dockerfile)
        self.assertIn("/usr/local/bin/amaru", self.dockerfile)
        self.assertIn("resolve_snapshot_points.py", self.dockerfile)
        self.assertIn("COPY bootstrap.py /usr/local/bin/bootstrap.py", self.dockerfile)

    def test_build_is_fail_closed_and_copies_no_host_binary(self):
        self.assertNotIn("|| true", self.dockerfile)
        self.assertNotRegex(self.dockerfile, r"(?m)^COPY\s+amaru\s")
        self.assertNotRegex(self.dockerfile, r"(TOKEN|PASSWORD|API_KEY|SECRET)=")

    def test_patches_are_exact_proven_diffs_and_parse_cleanly(self):
        expected_hashes = {
            "0001-local-custom-bootstrap.patch":
                "13f17164be8e091a7190e7fc08b3106e79cefee19dcbcfdceec5528b6b2a276c",
            "0002-tvar-definite-map.patch":
                "4a463e3ab7bc4285fb7f3e5697485b028695717bfa3c406bf02ed711e8baac36",
            "0003-custom-global-parameters.patch":
                "94716503597d4599ed7a7b664f8d46370aaf4c2232914a5e5877c314d1f70712",
            "0004-live-bootstrap-nonce.patch":
                "41e1f217e0a2ef53350821fb2b225e9decf7287447d1a31883f3b97fbde1b094",
        }

        for name, expected_hash in expected_hashes.items():
            with self.subTest(patch=name):
                patch = ROOT / "patches" / name
                contents = patch.read_bytes()
                self.assertEqual(hashlib.sha256(contents).hexdigest(), expected_hash)
                subprocess.run(
                    ["git", "apply", "--numstat", str(patch)],
                    cwd=ROOT,
                    check=True,
                    capture_output=True,
                    text=True,
                )


if __name__ == "__main__":
    unittest.main()
