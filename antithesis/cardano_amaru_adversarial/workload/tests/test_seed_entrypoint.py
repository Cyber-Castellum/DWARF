import os
import subprocess
import tempfile
import unittest
from pathlib import Path


BUNDLE = Path(__file__).resolve().parents[2]


class SeedEntrypointTests(unittest.TestCase):
    def test_sdk_diagnostic_stdout_does_not_contaminate_seed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_python = root / "python3"
            fake_python.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' 'Assertion output will be sent to: /tmp/sdk.jsonl'\n"
                "printf '%s\\n' '633847313526240873'\n",
                encoding="utf-8",
            )
            fake_adversary = root / "dwarf-adversary"
            fake_adversary.write_text(
                "#!/bin/sh\nprintf '%s\\n' \"$@\"\n", encoding="utf-8"
            )
            fake_python.chmod(0o755)
            fake_adversary.chmod(0o755)
            env = dict(os.environ)
            env["PATH"] = f"{root}:{env['PATH']}"
            env["DWARF_ADVERSARY_BIN"] = str(fake_adversary)

            result = subprocess.run(
                [
                    "sh",
                    str(BUNDLE / "adversary-image" / "seed-entrypoint.sh"),
                    "--network-magic",
                    "42",
                ],
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )

        self.assertEqual(
            result.stdout.splitlines(),
            ["--seed", "633847313526240873", "--network-magic", "42"],
        )
        self.assertIn("mutation seed = 633847313526240873", result.stderr)


if __name__ == "__main__":
    unittest.main()
