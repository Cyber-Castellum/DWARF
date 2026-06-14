"""Antithesis backend: render a Profile into a hermetic Antithesis test bundle.

The bundle layout follows the installed antithesis-* skills (see
profile_manager.antithesis_conventions). The workload container is the Amaru
node's peer: it dials the node and drives CBOR mini-protocol traffic, emitting
Antithesis SDK assertions.

The compose document is hand-built as a YAML string (matching the style of
profile_manager.profiles.compose_template) so this module adds no runtime
dependency on PyYAML — the package parses its JSON-in-YAML inputs with json.
"""
from profile_manager import antithesis_conventions as conv
from profile_manager.backends.base import BackendArtifacts, write_artifacts

AMARU_IMAGE = "amaru"
# The Amaru node image as built locally on the runtime host (name:tag differ from
# the registry ref the bundle uses, so the push retags from this source).
AMARU_LOCAL_IMAGE = "dwarf/amaru:0.1.2"
WORKLOAD_IMAGE = "dwarf-antithesis-workload"
CARDANO_NODE_IMAGE = "cardano-node-devnet"
AMARU_PORT = 3001
CARDANO_NODE_PORT = 3001
DEFAULT_REGISTRY = "us-central1-docker.pkg.dev/molten-verve-216720/cardano-repository"


def amaru_service_names(profile) -> list[str]:
    return [f"amaru-{i}" for i in range(1, profile.amaru_node_count + 1)]


def cardano_node_service_names(profile) -> list[str]:
    return [f"cardano-node-{i}" for i in range(1, profile.node_count + 1)]


def render_compose(profile, registry: str, tag: str = "latest") -> str:
    """Return the docker-compose.yaml text for the Antithesis bundle.

    Antithesis requirements honored: top-level name, every service sets
    container_name == hostname (hyphenated), platform linux/amd64, init: true,
    registry image refs (hermetic), and the workload waits for amaru to be
    service_healthy.
    """
    amaru_names = amaru_service_names(profile)
    haskell_names = cardano_node_service_names(profile)
    # TCP-listen readiness probe; quoted as a YAML double-quoted scalar.
    amaru_probe = f"bash -c '</dev/tcp/127.0.0.1/{AMARU_PORT}'"
    node_probe = f"bash -c '</dev/tcp/127.0.0.1/{CARDANO_NODE_PORT}'"
    lines: list[str] = [
        f"name: {profile.compose_project}",
        "services:",
    ]
    for name in haskell_names:
        lines.extend([
            f"  {name}:",
            f"    container_name: {name}",
            f"    hostname: {name}",
            f"    platform: {conv.PLATFORM}",
            "    init: true",
            f"    image: {registry}/{CARDANO_NODE_IMAGE}:{tag}",
            "    healthcheck:",
            "      test:",
            "        - CMD-SHELL",
            f'        - "{node_probe}"',
            "      interval: 3s",
            "      timeout: 3s",
            "      retries: 40",
        ])
    for name in amaru_names:
        lines.extend([
            f"  {name}:",
            f"    container_name: {name}",
            f"    hostname: {name}",
            f"    platform: {conv.PLATFORM}",
            "    init: true",
            f"    image: {registry}/{AMARU_IMAGE}:{tag}",
            "    environment:",
            f'      AMARU_NETWORK_MAGIC: "{profile.network_magic}"',
            "    healthcheck:",
            "      test:",
            "        - CMD-SHELL",
            f'        - "{amaru_probe}"',
            "      interval: 3s",
            "      timeout: 3s",
            "      retries: 40",
        ])
    if haskell_names:
        all_nodes = haskell_names + amaru_names
        targets = ",".join(f"{n}={n}:{CARDANO_NODE_PORT}" for n in all_nodes)
        lines.extend([
            "  workload:",
            "    container_name: workload",
            "    hostname: workload",
            f"    platform: {conv.PLATFORM}",
            "    init: true",
            f"    image: {registry}/{WORKLOAD_IMAGE}:{tag}",
            "    environment:",
            f'      WORKLOAD_TARGETS: "{targets}"',
            f'      AMARU_NETWORK_MAGIC: "{profile.network_magic}"',
            "    depends_on:",
        ])
        for name in all_nodes:
            lines.extend([f"      {name}:", "        condition: service_healthy"])
        lines.extend(["    command:", "      - /antithesis/setup-complete.sh"])
    else:
        primary = amaru_names[0]
        lines.extend([
            "  workload:",
            "    container_name: workload",
            "    hostname: workload",
            f"    platform: {conv.PLATFORM}",
            "    init: true",
            f"    image: {registry}/{WORKLOAD_IMAGE}:{tag}",
            "    environment:",
            f'      AMARU_TARGET: "{primary}:{AMARU_PORT}"',
            f'      AMARU_NETWORK_MAGIC: "{profile.network_magic}"',
            "    depends_on:",
        ])
        for name in amaru_names:
            lines.extend([
                f"      {name}:",
                "        condition: service_healthy",
            ])
        lines.extend([
            "    command:",
            "      - /antithesis/setup-complete.sh",
        ])
    return "\n".join(lines) + "\n"


def render_test_command(profile) -> str:
    """A parallel_ test command. Mixed profiles run the differential driver; single-node
    profiles run the single-target driver. Must NOT emit setup_complete.
    """
    sub = "drive-differential" if profile.node_count > 0 else "drive-once"
    return (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"python3 /workload/workload.py {sub}\n"
    )


def render_readme(profile) -> str:
    return (
        f"# Antithesis bundle: {profile.id}\n\n"
        "Hermetic Amaru closed-devnet security workload generated by Dwarf "
        "(`cardano-profile antithesis build`).\n\n"
        "- `config/docker-compose.yaml` — Amaru node + Dwarf CBOR workload (registry images).\n"
        "- `setup-complete.sh` — emits the Antithesis setup_complete signal.\n"
        "- `test/parallel_driver.sh` — drives CBOR and emits SDK assertions.\n\n"
        "No secrets are stored here. Do not commit wallets, PATs, or credentials.\n"
    )


def render_bundle(profile, registry: str = DEFAULT_REGISTRY, tag: str = "latest") -> BackendArtifacts:
    files = {
        conv.COMPOSE_RELPATH: render_compose(profile, registry=registry, tag=tag),
        conv.SETUP_COMPLETE_RELPATH: conv.SETUP_COMPLETE_SH,
        f"{conv.TEST_DIR}/parallel_driver.sh": render_test_command(profile),
        "README.md": render_readme(profile),
    }
    summary = {
        "profile": profile.id,
        "amaru_node_count": profile.amaru_node_count,
        "registry": registry,
        "tag": tag,
        "compose_project": profile.compose_project,
    }
    return BackendArtifacts(backend="antithesis", files=files, summary=summary)


class AntithesisBackend:
    name = "antithesis"

    def render(self, profile, scenario=None) -> BackendArtifacts:
        return render_bundle(profile)


def build_antithesis_bundle(profile, out_dir, registry: str = DEFAULT_REGISTRY, tag: str = "latest") -> list[str]:
    """Render the bundle and write it under out_dir. Returns written relative paths."""
    arts = render_bundle(profile, registry=registry, tag=tag)
    return write_artifacts(arts, out_dir)


WORKLOAD_BUILD_CONTEXT = "dwarf/antithesis_workload"


def image_push_commands(profile, registry: str = DEFAULT_REGISTRY, tag: str = "latest") -> list[str]:
    """Return the shell commands to build+push the workload and tag+push Amaru.

    Returned as strings so callers can print them (dry-run) or execute them.
    Registry auth is environment-supplied (docker login / config.json); never
    embedded here. The Amaru node image is pre-built locally as AMARU_LOCAL_IMAGE
    and retagged to the registry ref the bundle compose references.
    """
    workload_ref = f"{registry}/{WORKLOAD_IMAGE}:{tag}"
    amaru_ref = f"{registry}/{AMARU_IMAGE}:{tag}"
    commands = [
        f"docker build --platform {conv.PLATFORM} -t {workload_ref} {WORKLOAD_BUILD_CONTEXT}",
        f"docker push {workload_ref}",
        f"docker tag {AMARU_LOCAL_IMAGE} {amaru_ref}",
        f"docker push {amaru_ref}",
    ]
    if profile.node_count > 0:
        node_ref = f"{registry}/{CARDANO_NODE_IMAGE}:{tag}"
        commands += [
            f"bash dwarf/antithesis_devnet/build.sh {registry} {tag}",
            f"docker push {node_ref}",
        ]
    return commands
