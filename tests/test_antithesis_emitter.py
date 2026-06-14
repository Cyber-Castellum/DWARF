import yaml  # dev/test dependency; production code does not import yaml
from profile_manager.antithesis import render_compose
from profile_manager.profiles import find_profile

REGISTRY = "us-central1-docker.pkg.dev/molten-verve-216720/cardano-repository"


def _compose(profile):
    return yaml.safe_load(render_compose(profile, registry=REGISTRY, tag="t1"))


def test_compose_has_amaru_and_workload_services():
    doc = _compose(find_profile("profile-l-amaru-closed-devnet"))
    assert set(doc["services"]) == {"amaru-1", "workload"}


def test_compose_services_are_antithesis_compliant():
    doc = _compose(find_profile("profile-l-amaru-closed-devnet"))
    for name, svc in doc["services"].items():
        assert svc["platform"] == "linux/amd64"
        assert svc["init"] is True
        assert svc["container_name"] == name
        assert svc["hostname"] == name
        assert "_" not in name  # DNS-safe


def test_compose_images_are_registry_refs():
    doc = _compose(find_profile("profile-l-amaru-closed-devnet"))
    assert doc["services"]["amaru-1"]["image"] == f"{REGISTRY}/amaru:t1"
    assert doc["services"]["workload"]["image"] == f"{REGISTRY}/dwarf-antithesis-workload:t1"


def test_compose_workload_waits_for_amaru_healthy():
    doc = _compose(find_profile("profile-l-amaru-closed-devnet"))
    assert doc["services"]["workload"]["depends_on"]["amaru-1"]["condition"] == "service_healthy"
    assert "healthcheck" in doc["services"]["amaru-1"]


def test_antithesis_backend_renders_full_bundle():
    from profile_manager.backends import get_backend
    arts = get_backend("antithesis").render(find_profile("profile-l-amaru-closed-devnet"))
    rels = set(arts.files)
    assert "config/docker-compose.yaml" in rels
    assert "setup-complete.sh" in rels
    assert "test/parallel_driver.sh" in rels
    assert "README.md" in rels
    assert arts.summary["amaru_node_count"] == 1


def test_setup_complete_is_verbatim_skill_asset():
    from profile_manager.backends import get_backend
    from profile_manager import antithesis_conventions as conv
    arts = get_backend("antithesis").render(find_profile("profile-l-amaru-closed-devnet"))
    assert arts.files["setup-complete.sh"] == conv.SETUP_COMPLETE_SH


def test_build_bundle_writes_to_disk(tmp_path):
    from profile_manager.antithesis import build_antithesis_bundle
    written = build_antithesis_bundle(
        find_profile("profile-l-amaru-closed-devnet"), tmp_path, registry="REG", tag="x"
    )
    assert "config/docker-compose.yaml" in written
    assert (tmp_path / "config" / "docker-compose.yaml").exists()
    assert (tmp_path / "setup-complete.sh").read_text().startswith("#!/usr/bin/env bash")


def test_image_push_commands_target_configured_registry():
    from profile_manager.antithesis import image_push_commands
    cmds = image_push_commands(find_profile("profile-l-amaru-closed-devnet"),
                               registry=REGISTRY, tag="t1")
    joined = "\n".join(cmds)
    assert f"{REGISTRY}/amaru:t1" in joined
    assert f"{REGISTRY}/dwarf-antithesis-workload:t1" in joined
    assert any(c.startswith("docker push ") for c in cmds)
    assert any("docker build" in c for c in cmds)
