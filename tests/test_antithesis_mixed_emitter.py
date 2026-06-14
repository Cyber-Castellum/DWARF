import yaml
from profile_manager.antithesis import render_compose, render_test_command
from profile_manager.profiles import find_profile

REGISTRY = "us-central1-docker.pkg.dev/molten-verve-216720/cardano-repository"


def _compose(profile):
    return yaml.safe_load(render_compose(profile, registry=REGISTRY, tag="t1"))


def test_mixed_compose_has_both_node_types_and_workload():
    doc = _compose(find_profile("profile-c-mixed-haskell-amaru-minimal"))
    assert set(doc["services"]) == {"cardano-node-1", "amaru-1", "workload"}


def test_mixed_images_are_registry_refs():
    doc = _compose(find_profile("profile-c-mixed-haskell-amaru-minimal"))
    assert doc["services"]["cardano-node-1"]["image"] == f"{REGISTRY}/cardano-node-devnet:t1"
    assert doc["services"]["amaru-1"]["image"] == f"{REGISTRY}/amaru:t1"


def test_mixed_workload_targets_both_nodes_and_waits_for_health():
    doc = _compose(find_profile("profile-c-mixed-haskell-amaru-minimal"))
    wl = doc["services"]["workload"]
    assert wl["environment"]["WORKLOAD_TARGETS"] == "cardano-node-1=cardano-node-1:3001,amaru-1=amaru-1:3001"
    assert wl["depends_on"]["cardano-node-1"]["condition"] == "service_healthy"
    assert wl["depends_on"]["amaru-1"]["condition"] == "service_healthy"


def test_mixed_uses_differential_test_command():
    assert "drive-differential" in render_test_command(find_profile("profile-c-mixed-haskell-amaru-minimal"))


def test_single_amaru_test_command_unchanged():
    assert "drive-once" in render_test_command(find_profile("profile-l-amaru-closed-devnet"))


def test_image_push_includes_cardano_node_for_mixed():
    from profile_manager.antithesis import image_push_commands
    cmds = image_push_commands(find_profile("profile-c-mixed-haskell-amaru-minimal"),
                               registry=REGISTRY, tag="t1")
    joined = "\n".join(cmds)
    assert f"{REGISTRY}/cardano-node-devnet:t1" in joined
    assert "antithesis_devnet/build.sh" in joined
    assert f"{REGISTRY}/amaru:t1" in joined
    assert f"{REGISTRY}/dwarf-antithesis-workload:t1" in joined


def test_image_push_omits_cardano_node_for_single_amaru():
    from profile_manager.antithesis import image_push_commands
    cmds = image_push_commands(find_profile("profile-l-amaru-closed-devnet"),
                               registry=REGISTRY, tag="t1")
    assert all("cardano-node-devnet" not in c for c in cmds)


def test_image_push_tags_amaru_from_real_local_image():
    # The local Amaru image is dwarf/amaru:<ver>, not amaru:<tag>; the push must
    # tag from the real local image to the registry ref.
    from profile_manager.antithesis import image_push_commands, AMARU_LOCAL_IMAGE
    cmds = image_push_commands(find_profile("profile-l-amaru-closed-devnet"),
                               registry=REGISTRY, tag="t1")
    assert f"docker tag {AMARU_LOCAL_IMAGE} {REGISTRY}/amaru:t1" in cmds
    assert AMARU_LOCAL_IMAGE == "dwarf/amaru:0.1.2"


def test_mixed_bundle_test_command_is_differential():
    from pathlib import Path
    p = Path(__file__).resolve().parents[1] / "antithesis" / "mixed-haskell-amaru" / "test" / "parallel_driver.sh"
    assert "drive-differential" in p.read_text()


def test_mixed_repo_bundle_validates():
    from pathlib import Path
    from profile_manager.moog import validate_moog_asset, moog_asset_summary
    bundle = Path(__file__).resolve().parents[1] / "antithesis" / "mixed-haskell-amaru"
    summary = moog_asset_summary(validate_moog_asset(str(bundle)))
    assert summary["state"] in {"ready", "warn"}
    assert summary["error_count"] == 0
