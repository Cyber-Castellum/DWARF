from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
TESTNET = ROOT / "antithesis" / "cardano_node_dwarf"


def _compose():
    return yaml.safe_load((TESTNET / "docker-compose.yaml").read_text())


def test_testnet_files_present():
    for f in ("docker-compose.yaml", "testnet.yaml", "relay-topology.json", "tracer-config.yaml", "README.md"):
        assert (TESTNET / f).is_file(), f


def test_all_images_are_public_registries():
    doc = _compose()
    imgs = [svc["image"] for svc in doc.get("services", {}).values()
            if isinstance(svc, dict) and svc.get("image")]
    assert imgs, "no service images found"
    for img in imgs:
        assert img.startswith(("ghcr.io/", "docker.io/")), f"non-public image: {img}"
        assert "/" in img, f"suspect local image: {img}"


def test_harness_containers_have_fault_exclusion_label():
    doc = _compose()
    labeled = [
        name for name, svc in doc.get("services", {}).items()
        if isinstance(svc, dict) and "com.antithesis.exclude_from_faults" in (svc.get("labels") or {})
    ]
    assert labeled, "expected fault-exclusion labels on harness containers"


def test_compute_next_try_counts_matching_runs():
    from profile_manager.moog import compute_next_try
    facts = [
        {"key": {"type": "test-run", "commitId": "abc", "directory": "antithesis/cardano_node_dwarf",
                 "platform": "github", "repository": {"organization": "Cyber-Castellum", "repo": "DWARF"},
                 "requester": "J-GainSec"}},
        {"key": {"type": "test-run", "commitId": "abc", "directory": "antithesis/cardano_node_dwarf",
                 "platform": "github", "repository": {"organization": "Cyber-Castellum", "repo": "DWARF"},
                 "requester": "J-GainSec"}},
        {"key": {"type": "test-run", "commitId": "OTHER", "directory": "antithesis/cardano_node_dwarf",
                 "platform": "github", "repository": {"organization": "Cyber-Castellum", "repo": "DWARF"},
                 "requester": "J-GainSec"}},
    ]
    n = compute_next_try(facts, commit="abc", directory="antithesis/cardano_node_dwarf",
                         repository="Cyber-Castellum/DWARF", requester="J-GainSec", platform="github")
    assert n == 3


def test_compute_next_try_starts_at_one():
    from profile_manager.moog import compute_next_try
    assert compute_next_try([], commit="z", directory="d",
                            repository="o/r", requester="u", platform="github") == 1


def test_parse_test_run_phase_reads_phase():
    from profile_manager.moog import parse_test_run_phase
    assert parse_test_run_phase([{"value": {"phase": "accepted"}}]) == "accepted"


def test_parse_test_run_phase_handles_empty():
    from profile_manager.moog import parse_test_run_phase
    assert parse_test_run_phase([]) is None
    assert parse_test_run_phase([{"value": {}}]) is None


def test_cli_parses_moog_create_test_approve():
    from profile_manager.cli import build_parser
    args = build_parser().parse_args([
        "moog", "create-test", "--repo", "Cyber-Castellum/DWARF",
        "--github-user", "J-GainSec", "--directory", "antithesis/cardano_node_dwarf",
        "--commit", "deadbeef", "--duration", "1", "--no-faults", "--approve", "--json",
    ])
    assert args.command == "moog"
    assert args.moog_command == "create-test"
    assert args.approve is True
    assert args.no_faults is True
    assert args.directory == "antithesis/cardano_node_dwarf"


def test_cli_parses_moog_test_status():
    from profile_manager.cli import build_parser
    args = build_parser().parse_args(["moog", "test-status", "abc123", "--json"])
    assert args.command == "moog"
    assert args.moog_command == "test-status"
    assert args.test_run_id == "abc123"
