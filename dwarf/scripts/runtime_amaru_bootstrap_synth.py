from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_substrate_common import run_command
AMARU_TESTNET_DIR = SCRIPT_DIR.parents[1] / "codebases" / "amaru" / "docker" / "testnet"
DEFAULT_LOADER_BASE_IMAGE = "ghcr.io/pragma-org/amaru/loader:main"
DEFAULT_AMARU_IMAGE = "dwarf/amaru:0.1.2"
DEFAULT_LOADER_IMAGE = "dwarf/amaru-loader:0.1.2"


def _copy_tree_contents(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for child in source.iterdir():
        target = destination / child.name
        if child.is_dir():
            shutil.copytree(child, target, dirs_exist_ok=True)
        else:
            shutil.copy2(child, target)


def _pool_slot(node: dict) -> int:
    return int(node.get("host_slot_index") or node.get("slot_index") or 0)


def _stage_loader_scripts(scripts_root: Path) -> None:
    scripts_root.mkdir(parents=True, exist_ok=True)
    cardano_loader = (AMARU_TESTNET_DIR / "cardano-loader.sh").read_text(encoding="utf-8")
    # The upstream script hard-codes a 5-node comma rule in bulk.json generation.
    # Dwarf composes smaller synthetic devnets, so patch the staged copy to use the
    # actual pool count while preserving the rest of the upstream loader flow.
    cardano_loader = cardano_loader.replace("[[ $i -ne 5 ]]", "[[ $i -ne $number_of_pools ]]")
    cardano_loader_path = scripts_root / "cardano-loader.sh"
    cardano_loader_path.write_text(cardano_loader, encoding="utf-8")
    amaru_loader_path = scripts_root / "amaru-loader.sh"
    amaru_loader = (AMARU_TESTNET_DIR / "amaru-loader.sh").read_text(encoding="utf-8")
    amaru_loader = amaru_loader.replace(
        "# import headers\namaru import-headers --network ${NETWORK_NAME} --chain-dir ${BASEDIR}/chain.${NETWORK_NAME}.db",
        """# import headers
header_args=()
for header_file in ${BASEDIR}/${NETWORK_NAME}/headers/*.cbor; do
    header_args+=(--header-file "$header_file")
done
amaru import-headers --network ${NETWORK_NAME} --chain-dir ${BASEDIR}/chain.${NETWORK_NAME}.db "${header_args[@]}" """,
    )
    amaru_loader_path.write_text(amaru_loader, encoding="utf-8")
    cardano_loader_path.chmod(0o755)
    amaru_loader_path.chmod(0o755)


def ensure_loader_image(
    *,
    runtime_root: Path,
    loader_image: str = DEFAULT_LOADER_IMAGE,
    loader_base_image: str = DEFAULT_LOADER_BASE_IMAGE,
    amaru_image: str = DEFAULT_AMARU_IMAGE,
) -> str:
    inspect = run_command(["docker", "image", "inspect", loader_image])
    if inspect.returncode == 0:
        return loader_image
    amaru_inspect = run_command(["docker", "image", "inspect", amaru_image])
    if amaru_inspect.returncode != 0:
        raise RuntimeError(f"amaru image {amaru_image} is not available to build loader override")
    build_root = runtime_root / "amaru-bootstrap-loader" / "image-build"
    build_root.mkdir(parents=True, exist_ok=True)
    dockerfile_path = build_root / "Dockerfile"
    dockerfile_path.write_text(
        "\n".join(
            [
                f"FROM {loader_base_image}",
                f"COPY --from={amaru_image} /usr/local/bin/amaru /usr/local/bin/amaru",
                "RUN getent passwd 1000 >/dev/null || useradd --create-home --uid 1000 dwarf",
                "USER 1000:1000",
                "",
            ]
        ),
        encoding="utf-8",
    )
    build = run_command(["docker", "build", "-t", loader_image, str(build_root)])
    if build.returncode != 0:
        raise RuntimeError(build.stderr or build.stdout or f"failed to build loader image {loader_image}")
    return loader_image


def prepare_loader_workspace(*, runtime_root: Path, plan: dict) -> dict:
    env_root = runtime_root / "env"
    workspace_root = runtime_root / "amaru-bootstrap-loader"
    generated_root = workspace_root / "generated"
    scripts_root = workspace_root / "scripts"
    configs_root = workspace_root / "configs"
    cardano_state_root = workspace_root / "cardano-state"
    amaru_state_root = workspace_root / "amaru-state"

    generated_root.mkdir(parents=True, exist_ok=True)
    cardano_state_root.mkdir(parents=True, exist_ok=True)
    amaru_state_root.mkdir(parents=True, exist_ok=True)
    _stage_loader_scripts(scripts_root)

    source_configs_root = env_root / "configs" if (env_root / "configs").exists() else env_root

    config_roots: dict[str, str] = {}
    cardano_state_roots: dict[str, str] = {}
    target_cardano_state_roots: dict[str, str] = {}
    for node in plan["nodes"]:
        if node["impl"] != "cardano-node":
            continue
        slot = str(_pool_slot(node))
        config_root = configs_root / slot
        config_files_root = config_root / "configs"
        keys_root = config_root / "keys"
        config_files_root.mkdir(parents=True, exist_ok=True)
        keys_root.mkdir(parents=True, exist_ok=True)
        _copy_tree_contents(source_configs_root, config_files_root)
        if not (config_files_root / "config.json").exists() and (config_files_root / "configuration.yaml").exists():
            shutil.copy2(config_files_root / "configuration.yaml", config_files_root / "config.json")
        pool_keys_root = env_root / "pools-keys" / f"pool{slot}"
        _copy_tree_contents(pool_keys_root, keys_root)

        staged_db_root = cardano_state_root / slot
        staged_db_root.mkdir(parents=True, exist_ok=True)
        target_db_root = env_root / "node-data" / f"node{slot}" / "db"
        target_db_root.mkdir(parents=True, exist_ok=True)

        config_roots[slot] = str(config_root)
        cardano_state_roots[slot] = str(staged_db_root)
        target_cardano_state_roots[slot] = str(target_db_root)

    amaru_slot_map: dict[str, int] = {}
    amaru_state_roots: dict[str, str] = {}
    target_amaru_state_roots: dict[str, str] = {}
    amaru_slot = 1
    for node in plan["nodes"]:
        if node["impl"] != "amaru":
            continue
        slot = str(amaru_slot)
        staged_state_root = amaru_state_root / slot
        staged_state_root.mkdir(parents=True, exist_ok=True)
        target_state_root = Path(str(node["state_root"]))
        target_state_root.mkdir(parents=True, exist_ok=True)
        amaru_slot_map[node["id"]] = amaru_slot
        amaru_state_roots[slot] = str(staged_state_root)
        target_amaru_state_roots[slot] = str(target_state_root)
        amaru_slot += 1

    return {
        "workspace_root": str(workspace_root),
        "scripts_root": str(scripts_root),
        "generated_root": str(generated_root),
        "config_roots": config_roots,
        "cardano_state_roots": cardano_state_roots,
        "target_cardano_state_roots": target_cardano_state_roots,
        "amaru_state_root": str(amaru_state_root),
        "amaru_state_roots": amaru_state_roots,
        "target_amaru_state_roots": target_amaru_state_roots,
        "amaru_slot_map": amaru_slot_map,
        "network_name": str(plan["network"]),
    }


def loader_commands(*, layout: dict, loader_image: str = DEFAULT_LOADER_IMAGE) -> tuple[list[str], list[str]]:
    workspace_root = Path(str(layout["workspace_root"]))
    scripts_root = workspace_root / "scripts"
    cardano_cmd = [
        "docker",
        "run",
        "--rm",
        "--user",
        "1000:1000",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--tmpfs",
        "/tmp",
        "--tmpfs",
        "/run",
        "-e",
        f"NETWORK_NAME={layout['network_name']}",
        "-v",
        f"{layout['generated_root']}:/data/generated",
        "-v",
        f"{scripts_root / 'cardano-loader.sh'}:/data/cardano-loader.sh:ro",
    ]
    for slot, config_root in sorted(layout["config_roots"].items(), key=lambda item: int(item[0])):
        cardano_cmd.extend(["-v", f"{config_root}:/configs/{slot}"])
    for slot, state_root in sorted(layout["cardano_state_roots"].items(), key=lambda item: int(item[0])):
        cardano_cmd.extend(["-v", f"{state_root}:/state/{slot}"])
    cardano_cmd.extend([loader_image, "/data/cardano-loader.sh"])

    first_slot = sorted(layout["config_roots"], key=int)[0]
    amaru_cmd = [
        "docker",
        "run",
        "--rm",
        "--user",
        "1000:1000",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--tmpfs",
        "/tmp",
        "--tmpfs",
        "/run",
        "-e",
        f"NETWORK_NAME={layout['network_name']}",
        "-v",
        f"{layout['generated_root']}:/data/generated",
        "-v",
        f"{scripts_root / 'amaru-loader.sh'}:/data/amaru-loader.sh:ro",
        "-v",
        f"{layout['config_roots'][first_slot]}:/cardano/config",
        "-v",
        f"{layout['cardano_state_roots'][first_slot]}:/cardano/state",
    ]
    for slot, state_root in sorted(layout["amaru_state_roots"].items(), key=lambda item: int(item[0])):
        amaru_cmd.extend(["-v", f"{state_root}:/state/{slot}"])
    amaru_cmd.extend([loader_image, "/data/amaru-loader.sh"])
    return cardano_cmd, amaru_cmd


def _replace_tree(target_root: Path, source_root: Path) -> None:
    if target_root.exists():
        shutil.rmtree(target_root)
    shutil.copytree(source_root, target_root)


def _apply_staged_state(layout: dict) -> None:
    for slot, staged_root in layout["amaru_state_roots"].items():
        target_root = Path(layout["target_amaru_state_roots"][slot])
        if target_root.exists():
            shutil.rmtree(target_root)
        shutil.copytree(Path(staged_root), target_root)


def synthesize_amaru_bootstrap(*, runtime_root: Path, plan: dict, loader_image: str = DEFAULT_LOADER_IMAGE) -> dict:
    loader_image = ensure_loader_image(runtime_root=runtime_root, loader_image=loader_image)
    layout = prepare_loader_workspace(runtime_root=runtime_root, plan=plan)
    cardano_cmd, amaru_cmd = loader_commands(layout=layout, loader_image=loader_image)
    cardano = run_command(cardano_cmd)
    if cardano.returncode != 0:
        raise RuntimeError(f"cardano loader failed: {cardano.stderr or cardano.stdout}")
    amaru = run_command(amaru_cmd)
    if amaru.returncode != 0:
        raise RuntimeError(f"amaru loader failed: {amaru.stderr or amaru.stdout}")
    _apply_staged_state(layout)
    report = {
        "network_name": layout["network_name"],
        "loader_image": loader_image,
        "workspace_root": layout["workspace_root"],
        "generated_root": layout["generated_root"],
        "amaru_slot_map": layout["amaru_slot_map"],
        "cardano_command": cardano_cmd,
        "amaru_command": amaru_cmd,
    }
    report_path = runtime_root / "amaru-bootstrap-loader" / "synth-report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    report["report_path"] = str(report_path)
    return report
