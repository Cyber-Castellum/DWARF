import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import bootstrap


GLOBAL_KEYS = (
    "AMARU_GLOBAL_CONSENSUS_SECURITY_PARAM",
    "AMARU_GLOBAL_EPOCH_LENGTH_SCALE_FACTOR",
    "AMARU_GLOBAL_ACTIVE_SLOT_COEFF_INVERSE",
    "AMARU_GLOBAL_MAX_LOVELACE_SUPPLY",
    "AMARU_GLOBAL_SLOTS_PER_KES_PERIOD",
    "AMARU_GLOBAL_MAX_KES_EVOLUTION",
    "AMARU_GLOBAL_SYSTEM_START",
)


class BootstrapControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.live = self.root / "live"
        self.config = self.root / "config"
        self.output = self.root / "output"
        self.scratch = self.root / "scratch"
        self.bin = self.root / "bin"
        self.calls = self.root / "calls.jsonl"
        for directory in (self.live, self.config, self.bin):
            directory.mkdir(parents=True)
        for directory in ("immutable", "ledger", "volatile"):
            (self.live / directory).mkdir()
        (self.live / "protocolMagicId").write_text("42\n", encoding="utf-8")
        (self.live / "lock").write_text("", encoding="utf-8")
        (self.config / "config.json").write_text(
            json.dumps({"ShelleyGenesisFile": "shelley-genesis.json"}),
            encoding="utf-8",
        )
        (self.config / "shelley-genesis.json").write_text(
            json.dumps({"systemStart": "2026-08-23T08:06:09Z"}),
            encoding="utf-8",
        )

        self.era_history = self.root / "era-history.json"
        self.era_history.write_text(
            json.dumps(
                {
                    "stability_window": 300,
                    "eras": [
                        {
                            "start": {"time": 0, "slot": 0, "epoch": 0},
                            "end": None,
                            "params": {
                                "epoch_size_slots": 400,
                                "slot_length": 500,
                                "era_name": "Conway",
                            },
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.global_parameters = self.root / "global-parameters.json"
        self.global_parameters.write_text(
            json.dumps(
                {
                    "consensus_security_param": 20,
                    "epoch_length_scale_factor": 4,
                    "active_slot_coeff_inverse": 5,
                    "max_lovelace_supply": 45000000000000000,
                    "slots_per_kes_period": 129600,
                    "max_kes_evolution": 62,
                    "system_start": "from_shelley_genesis",
                }
            ),
            encoding="utf-8",
        )
        self.analyser = self._write_executable(
            "db-analyser",
            """#!/usr/bin/env python3
import os
import sys

if os.environ.get("FAKE_ANALYSER_FAILURE"):
    print("preserved analyser stderr", file=sys.stderr)
    raise SystemExit(23)
stream = sys.stderr if os.environ.get("FAKE_ANALYSER_TO_STDERR") else sys.stdout
print(os.environ.get("FAKE_ANALYSER_OUTPUT", ""), file=stream)
""",
        )
        self.amaru = self._write_executable(
            "amaru",
            """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
global_keys = [key for key in os.environ if key.startswith("AMARU_GLOBAL_")]
record = {
    "argv": args,
    "cwd": os.getcwd(),
    "era_history": os.environ.get("AMARU_ERA_HISTORY"),
    "globals": {key: os.environ[key] for key in sorted(global_keys)},
    "bootstrap_active_nonce": os.environ.get("AMARU_BOOTSTRAP_ACTIVE_NONCE"),
    "bootstrap_active_epoch": os.environ.get("AMARU_BOOTSTRAP_ACTIVE_EPOCH"),
}
with open(os.environ["FAKE_CALL_LOG"], "a", encoding="utf-8") as stream:
    stream.write(json.dumps(record) + "\\n")

failure = os.environ.get("FAKE_AMARU_FAILURE")
if failure and " ".join(args[:2]) == failure:
    print(f"forced {failure} failure", file=sys.stderr)
    raise SystemExit(31)

def value(flag):
    return args[args.index(flag) + 1]

if args[:2] == ["snapshot", "create"]:
    snapshot_dir = Path(value("--snapshot-dir"))
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    for index, arg in enumerate(args):
        if arg == "--snapshot":
            point = args[index + 1].split("::", 1)[0]
            (snapshot_dir / f"{point}.tar.zst").write_bytes(b"snapshot")
elif args[:2] == ["node", "bootstrap"]:
    ledger = Path(value("--ledger-dir"))
    chain = Path(value("--chain-dir"))
    (ledger / "live").mkdir(parents=True, exist_ok=True)
    chain.mkdir(parents=True, exist_ok=True)
    (chain / "CURRENT").write_text("MANIFEST-000001\\n", encoding="utf-8")
elif args[:3] == ["dev", "chain", "migrate"]:
    pass
else:
    print(f"unexpected fake amaru command: {args}", file=sys.stderr)
    raise SystemExit(32)
""",
        )
        self.cardano_cli = self._write_executable(
            "cardano-cli",
            """#!/usr/bin/env python3
import os
import sys

if os.environ.get("FAKE_CARDANO_CLI_FAILURE"):
    print("forced cardano-cli failure", file=sys.stderr)
    raise SystemExit(41)
print(os.environ.get(
    "FAKE_PROTOCOL_STATE",
    '{"lastSlot":1202,"epochNonce":"' + "a" * 64 + '"}',
))
""",
        )

    def tearDown(self):
        self.temp.cleanup()

    def _write_executable(self, name, contents):
        path = self.bin / name
        path.write_text(contents, encoding="utf-8")
        path.chmod(0o755)
        return path

    def _valid_analyser_output(self):
        return (ROOT / "fixtures" / "db-analyser-slots.txt").read_text(encoding="utf-8")

    def _run(self, analyser_output=None, extra_env=None):
        env = os.environ.copy()
        env.update(
            {
                "FAKE_CALL_LOG": str(self.calls),
                "FAKE_ANALYSER_OUTPUT": (
                    self._valid_analyser_output() if analyser_output is None else analyser_output
                ),
            }
        )
        env.update(extra_env or {})
        return subprocess.run(
            [
                sys.executable,
                str(ROOT / "bootstrap.py"),
                "--live-db",
                str(self.live),
                "--config-dir",
                str(self.config),
                "--output-root",
                str(self.output),
                "--scratch-root",
                str(self.scratch),
                "--era-history",
                str(self.era_history),
                "--global-parameters",
                str(self.global_parameters),
                "--network",
                "testnet_42",
                "--epoch-length",
                "400",
                "--db-analyser",
                str(self.analyser),
                "--amaru",
                str(self.amaru),
                "--cardano-cli",
                str(self.cardano_cli),
                "--cardano-socket",
                str(self.live / "node.socket"),
            ],
            env=env,
            capture_output=True,
            text=True,
        )

    def _calls(self):
        if not self.calls.exists():
            return []
        return [json.loads(line) for line in self.calls.read_text(encoding="utf-8").splitlines()]

    def test_immature_copied_chain_returns_documented_retry_exit(self):
        result = self._run(
            "[now] BlockNo 1 SlotNo 10 " + "1" * 64 + "\n"
            "[now] BlockNo 2 SlotNo 20 " + "2" * 64 + "\n"
        )

        self.assertEqual(result.returncode, bootstrap.RETRY_EXIT)
        self.assertIn("fewer than three completed epochs", result.stderr)
        self.assertFalse((self.output / "testnet_42").exists())

    def test_empty_new_chain_is_immature_and_retryable(self):
        result = self._run("")

        self.assertEqual(result.returncode, bootstrap.RETRY_EXIT)
        self.assertIn("no block rows", result.stderr)

    def test_live_copy_race_is_retryable(self):
        shutil.rmtree(self.live / "volatile")

        result = self._run()

        self.assertEqual(result.returncode, bootstrap.RETRY_EXIT)
        self.assertIn("live ChainDB copy", result.stderr)

    def test_analyser_failure_preserves_stderr_and_is_fatal(self):
        result = self._run(extra_env={"FAKE_ANALYSER_FAILURE": "1"})

        self.assertEqual(result.returncode, 23)
        self.assertIn("preserved analyser stderr", result.stderr)
        self.assertNotEqual(result.returncode, bootstrap.RETRY_EXIT)

    def test_successful_analyser_rows_on_stderr_are_accepted(self):
        result = self._run(extra_env={"FAKE_ANALYSER_TO_STDERR": "1"})

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(bootstrap.bundle_complete(self.output / "testnet_42"))

    def test_parser_failure_is_fatal_not_retryable(self):
        result = self._run("[now] BlockNo malformed\n")

        self.assertEqual(result.returncode, 1)
        self.assertIn("malformed db-analyser block row", result.stderr)

    def test_commands_receive_three_points_and_matching_custom_parameters(self):
        result = self._run()

        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self._calls()
        snapshot = next(call for call in calls if call["argv"][:2] == ["snapshot", "create"])
        node = next(call for call in calls if call["argv"][:2] == ["node", "bootstrap"])
        migrate = next(call for call in calls if call["argv"][:3] == ["dev", "chain", "migrate"])

        snapshot_args = snapshot["argv"]
        points = [snapshot_args[index + 1] for index, value in enumerate(snapshot_args) if value == "--snapshot"]
        self.assertEqual(len(points), 3)
        self.assertEqual(points, bootstrap.select_points(bootstrap.parse_rows(self._valid_analyser_output()), 400))
        self.assertEqual(snapshot_args[snapshot_args.index("--network") + 1], "testnet_42")
        self.assertEqual(snapshot_args[snapshot_args.index("--epoch") + 1], "3")
        self.assertEqual(snapshot_args[snapshot_args.index("--cardano-node-config-dir") + 1], str(self.config))
        copied_db = Path(snapshot_args[snapshot_args.index("--cardano-node-db") + 1])
        self.assertNotEqual(copied_db, self.live)
        self.assertTrue(str(copied_db).startswith(str(self.scratch)))

        node_args = node["argv"]
        self.assertEqual(node_args[node_args.index("--network") + 1], "testnet_42")
        self.assertEqual(node_args[node_args.index("--epoch") + 1], "3")
        self.assertIn(".tmp-", node_args[node_args.index("--ledger-dir") + 1])
        self.assertIn(".tmp-", node_args[node_args.index("--chain-dir") + 1])
        self.assertEqual(node["era_history"], str(self.era_history))
        self.assertEqual(set(node["globals"]), set(GLOBAL_KEYS))
        self.assertEqual(node["globals"]["AMARU_GLOBAL_SYSTEM_START"], "1787472369000")
        self.assertEqual(node["bootstrap_active_nonce"], "a" * 64)
        self.assertEqual(node["bootstrap_active_epoch"], "3")
        committed_parameters = json.loads(
            (self.output / "testnet_42" / "global-parameters.json").read_text(encoding="utf-8")
        )
        self.assertEqual(committed_parameters["system_start"], 1787472369000)
        self.assertEqual(migrate["argv"][migrate["argv"].index("--network") + 1], "testnet_42")

    def test_target_epoch_nonce_is_retryable_until_live_node_crosses_boundary(self):
        result = self._run(
            extra_env={
                "FAKE_PROTOCOL_STATE": '{"lastSlot":1195,"epochNonce":"' + "b" * 64 + '"}'
            }
        )

        self.assertEqual(result.returncode, bootstrap.RETRY_EXIT)
        self.assertIn("has not entered target epoch 3", result.stderr)
        self.assertEqual(self._calls(), [])

    def test_malformed_live_epoch_nonce_is_fatal(self):
        result = self._run(
            extra_env={"FAKE_PROTOCOL_STATE": '{"lastSlot":1202,"epochNonce":"not-a-nonce"}'}
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("invalid live epoch nonce", result.stderr)
        self.assertEqual(self._calls(), [])

    def test_live_node_past_target_epoch_is_fatal(self):
        result = self._run(
            extra_env={
                "FAKE_PROTOCOL_STATE": '{"lastSlot":1603,"epochNonce":"' + "c" * 64 + '"}'
            }
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("advanced beyond target epoch 3", result.stderr)
        self.assertEqual(self._calls(), [])

    def test_snapshot_and_bootstrap_failures_are_fatal(self):
        for command in ("snapshot create", "node bootstrap"):
            with self.subTest(command=command):
                shutil.rmtree(self.output, ignore_errors=True)
                shutil.rmtree(self.scratch, ignore_errors=True)
                self.calls.unlink(missing_ok=True)
                result = self._run(extra_env={"FAKE_AMARU_FAILURE": command})
                self.assertEqual(result.returncode, 31)
                self.assertIn(f"forced {command} failure", result.stderr)
                self.assertFalse((self.output / "testnet_42").exists())

    def test_complete_bundle_requires_every_v5_artifact(self):
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        final = self.output / "testnet_42"
        self.assertTrue(bootstrap.bundle_complete(final))

        required = [
            final / "READY.v5",
            final / "era-history.json",
            final / "global-parameters.json",
            final / "chain.testnet_42.db" / "CURRENT",
            final / "ledger.testnet_42.db" / "live",
            next((final / "snapshots").glob("*.tar.zst")),
        ]
        for path in required:
            with self.subTest(missing=path.name):
                backup = path.with_name(path.name + ".missing")
                path.rename(backup)
                self.assertFalse(bootstrap.bundle_complete(final))
                backup.rename(path)

    def test_output_is_atomic_ready_is_last_and_complete_output_is_idempotent(self):
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        final = self.output / "testnet_42"
        first_calls = self.calls.read_text(encoding="utf-8")
        self.assertFalse(any(path.name.startswith(".testnet_42.tmp-") for path in self.output.iterdir()))

        ready_mtime = (final / "READY.v5").stat().st_mtime_ns
        other_mtimes = [
            path.stat().st_mtime_ns
            for path in final.rglob("*")
            if path != final / "READY.v5"
        ]
        self.assertGreaterEqual(ready_mtime, max(other_mtimes))

        second = self._run()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("already complete", second.stdout)
        self.assertEqual(self.calls.read_text(encoding="utf-8"), first_calls)


if __name__ == "__main__":
    unittest.main()
