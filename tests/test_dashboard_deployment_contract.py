from pathlib import Path

from profile_manager.config import DeploymentConfig


ROOT = Path(__file__).resolve().parents[1]


def test_default_ssh_key_matches_container_mount():
    config = DeploymentConfig.from_dict({})
    compose = (ROOT / "delivery/docker-compose.dwarf.yml").read_text(encoding="utf-8")

    assert config.ssh_key_path == "~/.ssh/cardano-box"
    assert ":/home/dwarf/.ssh/cardano-box:ro" in compose


def test_production_compose_never_overrides_packaged_source():
    compose = (ROOT / "delivery/docker-compose.dwarf.yml").read_text(encoding="utf-8")

    assert ":/home/dwarf/dwarf-fw/dwarf" not in compose


def test_image_records_public_source_revision():
    dockerfile = (ROOT / "infrastructure/docker/dwarf-fw.Dockerfile").read_text(encoding="utf-8")
    build = (ROOT / "delivery/scripts/build-image.sh").read_text(encoding="utf-8")
    compose = (ROOT / "delivery/docker-compose.dwarf.yml").read_text(encoding="utf-8")

    assert "ARG DWARF_SOURCE_REVISION" in dockerfile
    assert "org.opencontainers.image.revision=${DWARF_SOURCE_REVISION}" in dockerfile
    assert "DWARF_SOURCE_REVISION=${DWARF_SOURCE_REVISION}" in dockerfile
    assert "--build-arg" in build
    assert "DWARF_SOURCE_REVISION" in build
    assert "DWARF_SOURCE_REVISION:" not in compose


def test_runtime_root_default_is_checkout_independent():
    common = (ROOT / "delivery/scripts/common.sh").read_text(encoding="utf-8")
    getting_started = (ROOT / "dwarf/dashboard/templates/learn/getting_started.j2").read_text(
        encoding="utf-8"
    )
    operator_runbook = (ROOT / "dwarf/dashboard/templates/learn/operator_runbook.j2").read_text(
        encoding="utf-8"
    )
    install = (ROOT / "INSTALL.md").read_text(encoding="utf-8")
    uninstall = (ROOT / "delivery/scripts/uninstall.sh").read_text(encoding="utf-8")

    assert '${XDG_DATA_HOME:-${HOME}/.local/share}/dwarf' in common
    assert "${PACKAGE_ROOT}/var" not in common
    assert "~/.local/share/dwarf" in getting_started
    assert "~/.local/share/dwarf" in operator_runbook
    assert "\ndelivery/scripts/" not in getting_started
    assert "\ndelivery/scripts/" not in operator_runbook
    assert "~/.local/share/dwarf" in install
    assert "package-local runtime data under var/" not in uninstall


def test_public_delivery_examples_work_with_non_executable_archive_modes():
    docs = [
        ROOT / "README.md",
        ROOT / "INSTALL.md",
        ROOT / "OPERATIONS.md",
        ROOT / "infrastructure/docker/README.md",
    ]

    for path in docs:
        for line in path.read_text(encoding="utf-8").splitlines():
            assert not line.startswith("delivery/scripts/"), f"{path}: {line}"


def test_retention_defaults_and_runbook_match_keep_until_manual_removal():
    config = DeploymentConfig.from_dict({})
    runbook = (ROOT / "dwarf/dashboard/templates/learn/operator_runbook.j2").read_text(
        encoding="utf-8"
    )

    assert config.runs_retention_days == 0
    assert config.bundles_retention_days == 0
    assert "Auto-pruned" not in runbook
    assert "until manually removed" in runbook
