import json
import os
import re
import shutil
import shlex
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from profile_manager.inspect import command_for_view, component_command, doctor_command, logs_command
from profile_manager.profiles import (
    Profile,
    REMOTE_DOCKERFILE_PATH,
    REMOTE_SOURCE_PATH,
    compose_template,
    deploy_command,
    deploy_dry_run_text,
)


CUSTOM_PACKAGE_ROOT_ENV = "ADA2_PROFILE_MANAGER_CUSTOM_PACKAGE_ROOT"
DEFAULT_CUSTOM_PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "custom-packages"
CUSTOM_EVIDENCE_ROOT_ENV = "ADA2_PROFILE_MANAGER_CUSTOM_EVIDENCE_ROOT"
DEFAULT_CUSTOM_EVIDENCE_ROOT = Path(__file__).resolve().parents[2] / "agent" / "testing" / "custom-packages"
REMOTE_AMARU_SOURCE_PATH = "${HOME}/amaru-verification"
REMOTE_AMARU_DOCKERFILE_PATH = "${HOME}/amaru-verification/docker/Dockerfile.amaru"

EXECUTION_TYPES = {
    "status-only",
    "source-review",
    "safe-test",
    "read-only-runtime",
    "bounded-smoke",
    "approval-required-runtime",
    "destructive-copy-state",
    "promotion-review",
}
SAFETY_LEVELS = {"safe", "controlled", "approval-required", "destructive-copy-state"}
TOPOLOGY_MODES = {"local-only", "lan-limited"}
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@dataclass(frozen=True)
class ValidationResult:
    package: dict
    profile: dict
    errors: tuple
    warnings: tuple

    @property
    def ok(self):
        return not self.errors


def custom_package_root():
    override = os.environ.get(CUSTOM_PACKAGE_ROOT_ENV)
    if override:
        return Path(override).expanduser()
    return DEFAULT_CUSTOM_PACKAGE_ROOT


def custom_evidence_root():
    override = os.environ.get(CUSTOM_EVIDENCE_ROOT_ENV)
    if override:
        return Path(override).expanduser()
    return DEFAULT_CUSTOM_EVIDENCE_ROOT


def _read_json(path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _required(data, fields, label, errors):
    for field in fields:
        if field not in data:
            errors.append(f"{label} missing required field: {field}")


def _valid_id(value):
    return isinstance(value, str) and bool(ID_PATTERN.match(value))


def _is_loopback_bind(bind):
    if not isinstance(bind, str):
        return False
    host = bind.rsplit(":", 1)[0]
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    return host in {"127.0.0.1", "localhost", "::1"}


def _nonnegative_int(value, field, errors):
    if isinstance(value, bool):
        errors.append(f"{field} must be a non-negative integer")
        return 0
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        errors.append(f"{field} must be a non-negative integer")
        return 0
    if parsed < 0:
        errors.append(f"{field} must be a non-negative integer")
        return 0
    return parsed


def load_bundle(bundle_path):
    root = Path(bundle_path)
    package_path = root / "package.json"
    profile_path = root / "profile.json"
    errors = []
    if not root.is_dir():
        errors.append(f"Bundle path is not a directory: {root}")
    if not package_path.exists():
        errors.append("Bundle missing package.json")
    if not profile_path.exists():
        errors.append("Bundle missing profile.json")
    if errors:
        return ValidationResult({}, {}, tuple(errors), ())
    try:
        package = _read_json(package_path)
        profile = _read_json(profile_path)
    except json.JSONDecodeError as error:
        return ValidationResult({}, {}, (f"Invalid JSON: {error}",), ())
    return validate_bundle_data(package, profile)


def validate_bundle_data(package, profile):
    errors = []
    warnings = []
    _required(
        package,
        (
            "id",
            "label",
            "version",
            "execution_type",
            "safety_level",
            "requires_approval",
            "mutates_runtime",
            "touches_public_network",
            "profile_id",
            "candidate_ids",
            "output_dir",
            "commands",
        ),
        "package.json",
        errors,
    )
    _required(
        profile,
        (
            "id",
            "label",
            "network_magic",
            "node_counts",
            "peer_sharing",
            "submit_api",
            "observability",
            "topology",
            "runtime_root",
        ),
        "profile.json",
        errors,
    )
    if errors:
        return ValidationResult(package, profile, tuple(errors), tuple(warnings))

    if not _valid_id(package["id"]):
        errors.append("package id must use lowercase letters, numbers, and hyphens")
    if not _valid_id(profile["id"]):
        errors.append("profile id must use lowercase letters, numbers, and hyphens")
    if package["profile_id"] != profile["id"]:
        errors.append("package profile_id must match profile id")
    if package["execution_type"] not in EXECUTION_TYPES:
        errors.append(f"unknown execution_type: {package['execution_type']}")
    if package["safety_level"] not in SAFETY_LEVELS:
        errors.append(f"unknown safety_level: {package['safety_level']}")
    if package["touches_public_network"] and not package.get("approval_reference"):
        errors.append("touches_public_network requires approval_reference")
    if package["mutates_runtime"] and not package["requires_approval"]:
        errors.append("mutates_runtime true requires requires_approval true")
    if package["execution_type"] == "destructive-copy-state" and not package.get("copied_state_path"):
        errors.append("destructive-copy-state requires copied_state_path")

    node_counts = profile.get("node_counts", {})
    if not isinstance(node_counts, dict):
        errors.append("profile node_counts must be an object")
        node_counts = {}
    haskell_nodes = _nonnegative_int(node_counts.get("haskell", 0), "node_counts.haskell", errors)
    amaru_nodes = _nonnegative_int(node_counts.get("amaru", 0), "node_counts.amaru", errors)
    if haskell_nodes < 1:
        warnings.append("Haskell node count less than 1")
    if amaru_nodes > 0 and haskell_nodes > 0:
        warnings.append("Amaru node count greater than zero; mixed runtime can use the built-in deployment adapter")
    elif amaru_nodes > 0:
        warnings.append("Amaru node count greater than zero; Amaru-only deploy still requires a built-in upstream-peer adapter")

    submit_api = profile.get("submit_api", {})
    if not isinstance(submit_api, dict):
        errors.append("profile submit_api must be an object")
        submit_api = {}
    if submit_api.get("enabled", False) and not _is_loopback_bind(submit_api.get("bind", "")):
        if not package.get("approval_reference"):
            errors.append("Submit API enabled with non-loopback bind address requires approval_reference")

    topology = profile.get("topology", {})
    if not isinstance(topology, dict):
        errors.append("profile topology must be an object")
        topology = {}
    if topology.get("mode") not in TOPOLOGY_MODES:
        errors.append(f"unknown topology mode: {topology.get('mode')}")
    if topology.get("mode") == "local-only":
        if topology.get("public_roots"):
            errors.append("local-only topology requires empty public_roots")
        if topology.get("bootstrap_peers"):
            errors.append("local-only topology requires empty bootstrap_peers")

    observability = profile.get("observability", {})
    if not isinstance(observability, dict):
        errors.append("profile observability must be an object")
        observability = {}
    if not isinstance(package.get("commands", []), list):
        errors.append("package commands must be a list")
    if package["execution_type"] in {"read-only-runtime", "bounded-smoke"} and not observability.get("logs", False):
        warnings.append("Observability logs disabled for a runtime evidence package")
    if not package.get("candidate_ids"):
        warnings.append("Missing candidate IDs for Milestone 1 evidence package")

    commands = package.get("commands", [])
    if not isinstance(commands, list):
        commands = []
    for command in commands:
        rendered = command if isinstance(command, str) else json.dumps(command)
        lowered = rendered.lower()
        if "cloudflare" in lowered or "nextcloud" in lowered:
            errors.append("Cardano package commands must not target Cloudflare or Nextcloud networks")

    return ValidationResult(package, profile, tuple(errors), tuple(warnings))


def validation_text(result):
    package = result.package
    profile = result.profile
    lines = []
    if result.ok:
        lines.append(f"Bundle valid: {package['id']}")
    else:
        lines.append("Bundle invalid")
    if package:
        lines.extend(
            [
                f"Package: {package.get('id', '(unknown)')}",
                f"Label: {package.get('label', '(unknown)')}",
                f"Execution Type: {package.get('execution_type', '(unknown)')}",
                f"Safety Level: {package.get('safety_level', '(unknown)')}",
                f"Requires Approval: {'yes' if package.get('requires_approval') else 'no'}",
                f"Public Network: {'yes' if package.get('touches_public_network') else 'no'}",
            ]
        )
    if profile:
        node_counts = profile.get("node_counts", {})
        lines.extend(
            [
                f"Profile: {profile.get('id', '(unknown)')}",
                f"Haskell nodes: {node_counts.get('haskell', 0)}",
                f"Amaru nodes: {node_counts.get('amaru', 0)}",
                f"PeerSharing: {'enabled' if profile.get('peer_sharing') else 'disabled'}",
                f"Runtime Root: {profile.get('runtime_root', '(unknown)')}",
            ]
        )
    if result.errors:
        lines.append("")
        lines.append("Errors:")
        lines.extend(f"- {error}" for error in result.errors)
    if result.warnings:
        lines.append("")
        lines.append("Warnings:")
        lines.extend(f"- {warning}" for warning in result.warnings)
    return "\n".join(lines) + "\n"


def installed_package_dirs():
    root = custom_package_root()
    if not root.exists():
        return []
    return sorted(path for path in root.iterdir() if path.is_dir() and (path / "package.json").exists())


def find_installed_package(package_id):
    for path in installed_package_dirs():
        result = load_bundle(path)
        if result.package.get("id") == package_id:
            return path, result
    raise KeyError(f"Unknown custom package: {package_id}")


def custom_profile_from_package(package, profile):
    node_counts = profile.get("node_counts", {})
    return Profile(
        id=profile["id"],
        label=profile["label"],
        node_type="cardano-node",
        node_count=int(node_counts.get("haskell", 0)),
        amaru_node_count=int(node_counts.get("amaru", 0)),
        network_magic=int(profile["network_magic"]),
        peer_sharing=bool(profile.get("peer_sharing")),
        remote_runtime_root=profile["runtime_root"],
        compose_project=profile.get("compose_project", f"dwarf-custom-{profile['id']}"),
    )


def _custom_node_counts(profile):
    node_counts = profile.get("node_counts", {})
    return int(node_counts.get("haskell", 0)), int(node_counts.get("amaru", 0))


def _custom_deploy_mode(profile):
    haskell_nodes, amaru_nodes = _custom_node_counts(profile)
    if haskell_nodes > 0 and amaru_nodes > 0:
        return "mixed"
    if amaru_nodes > 0:
        return "amaru-only"
    return "haskell-only"


def _amaru_network_name(network_magic):
    return f"testnet_{int(network_magic)}"


def _mixed_compose_template(package, profile):
    generated_profile = custom_profile_from_package(package, profile)
    haskell_nodes, amaru_nodes = _custom_node_counts(profile)
    if haskell_nodes < 1 or amaru_nodes < 1:
        return compose_template(generated_profile)
    runtime_root = profile["runtime_root"]
    project = generated_profile.compose_project
    network = f"{project}-net"
    lines = [
        "name: " + project,
        "networks:",
        f"  {network}:",
        "    driver: bridge",
        "services:",
    ]
    amaru_network = _amaru_network_name(profile["network_magic"])
    for i in range(1, amaru_nodes + 1):
        service = f"amaru{i}"
        peer = "host.docker.internal:33001" if i == 1 else f"amaru{i - 1}:3000"
        lines.extend([
            f"  {service}:",
            "    image: dwarf/amaru:${AMARU_SHA:-latest}",
            "    labels:",
            "      ada2.managed: dwarf",
            f"      ada2.profile: {profile['id']}",
            f"      ada2.service: {service}",
            "      ada2.node_type: amaru",
            "    networks:",
            f"      {network}:",
            "        aliases:",
            f"          - {service}",
            "    extra_hosts:",
            "      - host.docker.internal:host-gateway",
        ])
        if i > 1:
            lines.extend([
                "    depends_on:",
                f"      amaru{i - 1}:",
                "        condition: service_started",
            ])
        lines.extend([
            "    environment:",
            f"      AMARU_NETWORK: {amaru_network}",
            f"      AMARU_PEER_ADDRESS: {peer}",
            "      AMARU_CHAIN_DIR: /srv/amaru/chain.db",
            "      AMARU_LEDGER_DIR: /srv/amaru/ledger.db",
            "    volumes:",
            f"      - {runtime_root}/amaru/{service}:/srv/amaru:rw",
            "    restart: on-failure",
        ])
    return "\n".join(lines) + "\n"


def package_list_text():
    lines = ["Custom packages:"]
    packages = installed_package_dirs()
    if not packages:
        lines.append("- none installed")
        return "\n".join(lines) + "\n"
    for path in packages:
        result = load_bundle(path)
        package = result.package
        state = "valid" if result.ok else "invalid"
        lines.append(f"- {package.get('id', path.name)}: {package.get('label', '(unknown)')} ({package.get('execution_type', 'unknown')}; {state})")
    return "\n".join(lines) + "\n"


def package_status_text(package_id):
    path, result = find_installed_package(package_id)
    text = validation_text(result)
    return text + f"Install Path: {path}\n"


def package_run_blockers(result, approved=False):
    blockers = []
    package = result.package
    profile = result.profile
    if not result.ok:
        blockers.append("package validation failed")
        return tuple(blockers)
    allowed_execution_types = {
        "read-only-runtime",
        "bounded-smoke",
        "approval-required-runtime",
        "destructive-copy-state",
    }
    if package.get("execution_type") not in allowed_execution_types:
        blockers.append(f"execution_type {package.get('execution_type')} is not supported by custom package run")
    if package.get("requires_approval") and not approved:
        blockers.append("package requires explicit approval")
    if package.get("touches_public_network"):
        blockers.append("public-network contact is not supported by custom package run")
    if package.get("mutates_runtime") and package.get("execution_type") not in {"approval-required-runtime", "destructive-copy-state"}:
        blockers.append("runtime mutation requires approval-required-runtime or destructive-copy-state execution type")
    if package.get("safety_level") == "destructive-copy-state" and package.get("execution_type") != "destructive-copy-state":
        blockers.append("destructive-copy-state safety requires destructive-copy-state execution type")
    if package.get("execution_type") == "destructive-copy-state" and not package.get("copied_state_path"):
        blockers.append("destructive-copy-state requires copied_state_path")
    return tuple(blockers)


def package_deploy_blockers(result):
    blockers = []
    package = result.package
    profile = result.profile
    if not result.ok:
        blockers.append("package validation failed")
        return tuple(blockers)
    haskell_nodes, amaru_nodes = _custom_node_counts(profile)
    if haskell_nodes < 1 and amaru_nodes < 1:
        blockers.append("custom deploy requires at least one Haskell or Amaru node")
    if haskell_nodes < 1 and amaru_nodes > 0:
        blockers.append("Amaru-only deployment is not supported by the built-in adapter yet")
    if package.get("touches_public_network"):
        blockers.append("custom deploy does not support public-network contact")
    return tuple(blockers)


def package_deploy_dry_run_text(package, profile):
    generated_profile = custom_profile_from_package(package, profile)
    deploy_mode = _custom_deploy_mode(profile)
    lines = [
        f"DRY RUN deploy for custom package {package['id']}",
        "Would check for an active Cardano runtime first.",
        "Would ask whether to remove the currently deployed package/profile before deploying this one if any runtime is active.",
        "Would not reuse an already deployed package/profile runtime.",
    ]
    if deploy_mode == "haskell-only":
        lines.extend(
            [
                "",
                deploy_dry_run_text(generated_profile).strip(),
                "",
                "Rendered deploy command:",
                deploy_command(generated_profile),
            ]
        )
        return "\n".join(lines) + "\n"
    haskell_nodes, amaru_nodes = _custom_node_counts(profile)
    lines.extend(
        [
            "",
            f"Mixed Haskell/Amaru deploy: {haskell_nodes} Haskell node(s), {amaru_nodes} Amaru node(s).",
            f"Topology pattern: {profile.get('topology', {}).get('pattern') or 'local-mesh'}.",
            f"Shared genesis: {'yes' if profile.get('topology', {}).get('shared_genesis', True) else 'no'}.",
            f"Would create Haskell runtime env under {profile['runtime_root']}/env via ${HOME}/.local/bin/cardano-testnet create-env.",
            "Would run Haskell nodes from ${HOME}/.local/bin/cardano-node under tmux sessions scoped to this profile.",
            "Would run Amaru nodes from ${HOME}/amaru-verification/target/debug/amaru under tmux sessions scoped to this profile.",
            f"Would set AMARU_NETWORK={_amaru_network_name(profile['network_magic'])} and seed AMARU_PEER_ADDRESS from 127.0.0.1:33001 then prior Amaru peers.",
            "",
            "Rendered deploy command:",
            package_deploy_command(package, profile),
        ]
    )
    return "\n".join(lines) + "\n"


def package_deploy_command(package, profile):
    generated_profile = custom_profile_from_package(package, profile)
    deploy_mode = _custom_deploy_mode(profile)
    if deploy_mode == "haskell-only":
        return deploy_command(generated_profile)
    haskell_nodes, amaru_nodes = _custom_node_counts(profile)
    runtime = shlex.quote(profile["runtime_root"])
    project = shlex.quote(generated_profile.compose_project)
    peer_sharing = "true" if bool(profile.get("peer_sharing")) else "false"
    cardano_node_bin = shlex.quote("${HOME}/.local/bin/cardano-node")
    cardano_cli_bin = shlex.quote("${HOME}/.local/bin/cardano-cli")
    cardano_testnet_bin = shlex.quote("${HOME}/.local/bin/cardano-testnet")
    amaru_bin = shlex.quote("${HOME}/amaru-verification/target/debug/amaru")
    amaru_network = shlex.quote(_amaru_network_name(profile["network_magic"]))
    return f"""set -e
runtime={runtime}
project={project}
cardano_node_bin={cardano_node_bin}
cardano_cli_bin={cardano_cli_bin}
cardano_testnet_bin={cardano_testnet_bin}
amaru_bin={amaru_bin}
amaru_network={amaru_network}
if [ -e "$runtime/env" ] || [ -e "$runtime/amaru" ]; then
  echo "Runtime assets already exist under: $runtime" >&2
  exit 4
fi
mkdir -p "$runtime/logs" "$runtime/amaru" "$runtime/socket" "$runtime/pids"
cardano_sha=$("$cardano_node_bin" --version 2>/dev/null | head -n 1 | tr ' /' '__' | tr -cd '[:alnum:]_.-')
[ -n "$cardano_sha" ] || cardano_sha=host
amaru_sha=$("$amaru_bin" --version 2>/dev/null | head -n 1 | tr ' /' '__' | tr -cd '[:alnum:]_.-')
[ -n "$amaru_sha" ] || amaru_sha=host
printf 'CARDANO_NODE_SHA=%s\\nAMARU_SHA=%s\\n' "$cardano_sha" "$amaru_sha" > "$runtime/.env"
mkdir -p "$runtime/env"
export CARDANO_NODE="$cardano_node_bin"
export CARDANO_CLI="$cardano_cli_bin"
"$cardano_testnet_bin" \\
  create-env --output "$runtime/env" --num-pool-nodes {haskell_nodes} --testnet-magic {int(profile['network_magic'])} --node-logging-format json
python3 - "$runtime/env/configuration.yaml" <<'PY'
import sys
path = sys.argv[1]
body = open(path, encoding="utf-8").read()
body = body.replace('"PeerSharing": true', '"PeerSharing": {peer_sharing}')
body = body.replace('"PeerSharing": false', '"PeerSharing": {peer_sharing}')
open(path, "w", encoding="utf-8").write(body)
PY
python3 - "$runtime/env" "$runtime/runtime.json" "$project" {haskell_nodes} <<'PY'
import json, os, sys
env_dir = sys.argv[1]
metadata_path = sys.argv[2]
project = sys.argv[3]
node_count = int(sys.argv[4])
base_port = 33001
nodes = []
for i in range(1, node_count + 1):
    topo_path = os.path.join(env_dir, "node-data", f"node{{i}}", "topology.json")
    if not os.path.exists(topo_path):
        continue
    data = json.load(open(topo_path))
    port = base_port + i - 1
    peers = [{{"address": "127.0.0.1", "port": base_port + j - 1}}
             for j in range(1, node_count + 1) if j != i]
    if "Producers" in data:
        data["Producers"] = [dict(peer, valency=1) for peer in peers]
    else:
        data["localRoots"] = [{{
            "accessPoints": peers,
            "advertise": False,
            "valency": len(peers),
        }}]
    json.dump(data, open(topo_path, "w"), indent=2)
    nodes.append({{
        "name": f"node{{i}}",
        "port": port,
        "session": f"{{project}}-node{{i}}",
        "socket_path": os.path.join(os.path.dirname(metadata_path), "socket", f"node{{i}}.sock"),
        "db_dir": os.path.join(env_dir, "node-data", f"node{{i}}", "db"),
        "config_path": os.path.join(env_dir, "configuration.yaml"),
        "topology_path": topo_path,
        "log_path": os.path.join(os.path.dirname(metadata_path), "logs", f"node{{i}}", "stdout.log"),
        "pid_file": os.path.join(os.path.dirname(metadata_path), "pids", f"node{{i}}.pid"),
    }})
json.dump({{"profile_id": os.path.basename(os.path.dirname(metadata_path)), "haskell_nodes": nodes}}, open(metadata_path, "w"), indent=2)
PY
for i in $(seq 1 {haskell_nodes}); do mkdir -p "$runtime/logs/node$i"; done
for i in $(seq 1 {amaru_nodes}); do mkdir -p "$runtime/amaru/amaru$i" "$runtime/logs/amaru$i"; done
for i in $(seq 1 {haskell_nodes}); do
  session="$project-node$i"
  port=$((33000 + i))
  socket_path="$runtime/socket/node$i.sock"
  db_dir="$runtime/env/node-data/node$i/db"
  config_path="$runtime/env/configuration.yaml"
  topology_path="$runtime/env/node-data/node$i/topology.json"
  log_path="$runtime/logs/node$i/stdout.log"
  pid_file="$runtime/pids/node$i.pid"
  kes_key="$runtime/env/pools-keys/pool$i/kes.skey"
  vrf_key="$runtime/env/pools-keys/pool$i/vrf.skey"
  opcert="$runtime/env/pools-keys/pool$i/opcert.cert"
  byron_delegation="$runtime/env/pools-keys/pool$i/byron-delegation.cert"
  byron_signing="$runtime/env/pools-keys/pool$i/byron-delegate.key"
  if tmux has-session -t "$session" 2>/dev/null; then
    echo "Session already exists: $session" >&2
    exit 5
  fi
  tmux new-session -d -s "$session" "bash -lc 'echo \\$$ > $pid_file; exec $cardano_node_bin run --config $config_path --topology $topology_path --database-path $db_dir --socket-path $socket_path --port $port --host-addr 127.0.0.1 --shelley-kes-key $kes_key --shelley-vrf-key $vrf_key --shelley-operational-certificate $opcert --byron-delegation-certificate $byron_delegation --byron-signing-key $byron_signing 2>&1 | tee -a $log_path'"
done
python3 - "$runtime/runtime.json" "$project" {amaru_nodes} <<'PY'
import json, os, sys
metadata_path = sys.argv[1]
project = sys.argv[2]
node_count = int(sys.argv[3])
body = json.load(open(metadata_path))
base_port = 34001
amaru_nodes = []
for i in range(1, node_count + 1):
    port = base_port + i - 1
    amaru_nodes.append({{
        "name": f"amaru{{i}}",
        "port": port,
        "session": f"{{project}}-amaru{{i}}",
        "peer_address": "127.0.0.1:33001" if i == 1 else f"127.0.0.1:{{base_port + i - 2}}",
        "chain_dir": os.path.join(os.path.dirname(metadata_path), "amaru", f"amaru{{i}}", f"chain.testnet_{int(profile['network_magic'])}.db"),
        "ledger_dir": os.path.join(os.path.dirname(metadata_path), "amaru", f"amaru{{i}}", f"ledger.testnet_{int(profile['network_magic'])}.db"),
        "pid_file": os.path.join(os.path.dirname(metadata_path), "amaru", f"amaru{{i}}", "amaru.pid"),
        "log_path": os.path.join(os.path.dirname(metadata_path), "logs", f"amaru{{i}}", "stdout.log"),
    }})
body["amaru_nodes"] = amaru_nodes
json.dump(body, open(metadata_path, "w"), indent=2)
PY
for i in $(seq 1 {amaru_nodes}); do
  session="$project-amaru$i"
  port=$((34000 + i))
  peer_address="127.0.0.1:33001"
  if [ "$i" -gt 1 ]; then
    peer_address="127.0.0.1:$((34000 + i - 1))"
  fi
  state_root="$runtime/amaru/amaru$i"
  chain_dir="$state_root/chain.testnet_{int(profile['network_magic'])}.db"
  ledger_dir="$state_root/ledger.testnet_{int(profile['network_magic'])}.db"
  log_path="$runtime/logs/amaru$i/stdout.log"
  pid_file="$state_root/amaru.pid"
  bootstrap_stdout="$runtime/logs/amaru$i/bootstrap.stdout.log"
  bootstrap_stderr="$runtime/logs/amaru$i/bootstrap.stderr.log"
  if tmux has-session -t "$session" 2>/dev/null; then
    echo "Session already exists: $session" >&2
    exit 6
  fi
  "$amaru_bin" bootstrap --network "$amaru_network" --ledger-dir "$ledger_dir" --chain-dir "$chain_dir" >"$bootstrap_stdout" 2>"$bootstrap_stderr"
  tmux new-session -d -s "$session" "bash -lc 'echo \\$$ > $pid_file; exec $amaru_bin run --network $amaru_network --peer-address $peer_address --listen-address 127.0.0.1:$port --ledger-dir $ledger_dir --chain-dir $chain_dir --pid-file $pid_file 2>&1 | tee -a $log_path'"
done
tmux ls | grep "$project" || true
"""


def _assert_package_script_path(package_dir, script_path):
    if not isinstance(script_path, str) or not script_path:
        raise ValueError("script command requires path")
    package_root = Path(package_dir).resolve()
    path = (package_root / script_path).resolve()
    try:
        path.relative_to(package_root)
    except ValueError as error:
        raise ValueError("script path must stay inside installed package directory") from error
    if not path.is_file():
        raise ValueError(f"script not found: {script_path}")
    if not os.access(path, os.X_OK):
        raise ValueError(f"script is not executable: {script_path}")
    body = path.read_text(encoding="utf-8")
    lowered = body.lower()
    if "cloudflare" in lowered or "nextcloud" in lowered:
        raise ValueError("package scripts must not target Cloudflare or Nextcloud")
    return path, body


def _script_remote_command(package, profile, spec, package_dir, destructive=False):
    script_path, body = _assert_package_script_path(package_dir, spec.get("path"))
    args = spec.get("args", [])
    if not isinstance(args, list):
        raise ValueError("script args must be a list")
    copied_state_path = package.get("copied_state_path", "")
    runtime_root = profile["runtime_root"]
    arg_text = " ".join(shlex.quote(str(arg)) for arg in args)
    script_name = shlex.quote(script_path.name)
    copied_state_line = ""
    destructive_guard = ""
    if destructive:
        if not copied_state_path:
            raise ValueError("destructive-script requires copied_state_path")
        if str(copied_state_path).rstrip("/") == str(runtime_root).rstrip("/"):
            raise ValueError("copied_state_path must not equal runtime_root")
        copied_state_line = f"export COPIED_STATE_PATH={shlex.quote(str(copied_state_path))}\necho \"COPIED_STATE_PATH=$COPIED_STATE_PATH\""
        destructive_guard = """if [ "$COPIED_STATE_PATH" = "$RUNTIME_ROOT" ]; then
  echo "copied_state_path must not equal runtime_root" >&2
  exit 9
fi
mkdir -p "$COPIED_STATE_PATH"
"""
    return f"""set -e
export PACKAGE_ID={shlex.quote(package['id'])}
export PROFILE_ID={shlex.quote(profile['id'])}
export RUNTIME_ROOT={shlex.quote(runtime_root)}
{copied_state_line}
echo "APPROVED_PACKAGE_SCRIPT={shlex.quote(package['id'])}"
echo "SCRIPT_PATH={shlex.quote(str(script_path.relative_to(Path(package_dir).resolve())))}"
echo "SCRIPT_COMMAND={shlex.quote(str(script_path.relative_to(Path(package_dir).resolve())))} {arg_text}"
{destructive_guard}
script=$(mktemp)
cat > "$script" <<'ADA2_PACKAGE_SCRIPT'
{body}
ADA2_PACKAGE_SCRIPT
chmod +x "$script"
"$script" {arg_text}
rm -f "$script"
"""


def _command_spec_to_remote_command(spec, runtime_root, package=None, profile=None, package_dir=None):
    if not isinstance(spec, dict):
        raise ValueError("package command entries must be objects")
    command_type = spec.get("type")
    if command_type == "inspect":
        view = spec.get("view", "all")
        if view not in {"env", "nodes", "health", "all"}:
            raise ValueError(f"unsupported inspect view: {view}")
        return command_for_view(view, runtime_root)
    if command_type == "doctor":
        return doctor_command(runtime_root)
    if command_type == "logs":
        action = spec.get("action", "scan")
        if action not in {"collect", "scan", "tail"}:
            raise ValueError(f"unsupported logs action: {action}")
        return logs_command(action, runtime_root, spec.get("node"), int(spec.get("lines", 200)))
    if command_type == "component":
        component = spec.get("component", "all")
        view = spec.get("view", "health")
        if view not in {"status", "logs", "tip", "config", "health"}:
            raise ValueError(f"unsupported component view: {view}")
        return component_command(component, view, runtime_root, int(spec.get("lines", 200)))
    if command_type == "script":
        if not package or not profile or not package_dir:
            raise ValueError("script command requires installed package context")
        return _script_remote_command(package, profile, spec, package_dir, destructive=False)
    if command_type == "destructive-script":
        if not package or not profile or not package_dir:
            raise ValueError("destructive-script command requires installed package context")
        return _script_remote_command(package, profile, spec, package_dir, destructive=True)
    raise ValueError(f"unsupported package command type: {command_type}")


def package_remote_commands(package, profile, package_dir=None):
    runtime_root = profile["runtime_root"]
    commands = package.get("commands", [])
    if not commands:
        raise ValueError("package has no run commands")
    return [
        _command_spec_to_remote_command(spec, runtime_root, package=package, profile=profile, package_dir=package_dir)
        for spec in commands
    ]


def package_run_summary(package, profile, commands, dry_run=False):
    lines = []
    if dry_run:
        lines.append("DRY RUN")
    lines.extend(
        [
            f"Package: {package['id']}",
            f"Profile: {profile['id']}",
            f"Runtime Root: {profile['runtime_root']}",
            f"Execution Type: {package['execution_type']}",
            f"Safety Level: {package['safety_level']}",
            f"Commands: {len(commands)}",
            "",
        ]
    )
    return "\n".join(lines)


def refusal_text(blockers):
    lines = ["Refusing custom package run", ""]
    lines.extend(f"- {blocker}" for blocker in blockers)
    return "\n".join(lines) + "\n"


def write_custom_package_evidence(package, profile, action, config_path, config, command_results, dry_run=False, limitations=None):
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    directory = custom_evidence_root() / package["id"]
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{timestamp}-{action}.md"
    json_path = directory / f"{timestamp}-{action}.json"
    node_counts = profile.get("node_counts", {})
    submit_api = profile.get("submit_api", {})
    observability = profile.get("observability", {})
    topology = profile.get("topology", {})
    lines = [
        f"# Custom Package Evidence - {package['id']}",
        "",
        f"- Action: {action}",
        f"- Timestamp UTC: {timestamp}",
        f"- Package ID: {package['id']}",
        f"- Profile ID: {profile['id']}",
        f"- Config Path: {config_path}",
        f"- Deployment: {config.deployment_name}",
        f"- Remote Host: {config.ssh_user}@{config.host}",
        f"- Remote Base Path: {config.remote_base_path}",
        f"- Runtime Root: {profile['runtime_root']}",
        f"- Execution Type: {package['execution_type']}",
        f"- Safety Level: {package['safety_level']}",
        f"- Requires Approval: {'yes' if package.get('requires_approval') else 'no'}",
        f"- Mutates Runtime: {'yes' if package.get('mutates_runtime') else 'no'}",
        f"- Public Network: {'yes' if package.get('touches_public_network') else 'no'}",
        f"- Haskell Nodes: {node_counts.get('haskell', 0)}",
        f"- Amaru Nodes: {node_counts.get('amaru', 0)}",
        f"- PeerSharing: {'enabled' if profile.get('peer_sharing') else 'disabled'}",
        f"- Submit API: {'enabled' if submit_api.get('enabled') else 'disabled'}",
        f"- Submit API Bind: {submit_api.get('bind', '')}",
        f"- Observability Logs: {'enabled' if observability.get('logs') else 'disabled'}",
        f"- Observability Metrics: {'enabled' if observability.get('metrics') else 'disabled'}",
        f"- Trace Buffer: {'enabled' if observability.get('trace_buffer') else 'disabled'}",
        f"- Topology Mode: {topology.get('mode', '')}",
        "",
        "## Commands",
        "",
    ]
    for result in command_results:
        lines.extend(
            [
                "```bash",
                result.rendered_command,
                "```",
                f"- Exit Code: {result.returncode}",
                "",
                "### Stdout",
                "",
                "```text",
                (result.stdout or "").strip(),
                "```",
                "",
                "### Stderr",
                "",
                "```text",
                (result.stderr or "").strip(),
                "```",
                "",
            ]
        )
    if limitations:
        lines.extend(["## Limitations", ""])
        for limitation in limitations:
            lines.append(f"- {limitation}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    sidecar = {
        "package_id": package["id"],
        "profile_id": profile["id"],
        "action": action,
        "timestamp_utc": timestamp,
        "config_path": str(config_path),
        "deployment": config.deployment_name,
        "remote_host": f"{config.ssh_user}@{config.host}",
        "remote_base_path": config.remote_base_path,
        "runtime_root": profile["runtime_root"],
        "node_counts": node_counts,
        "peer_sharing": profile.get("peer_sharing"),
        "submit_api": submit_api,
        "observability": observability,
        "topology": topology,
        "execution_type": package["execution_type"],
        "safety_level": package["safety_level"],
        "requires_approval": package.get("requires_approval"),
        "mutates_runtime": package.get("mutates_runtime"),
        "touches_public_network": package.get("touches_public_network"),
        "candidate_ids": package.get("candidate_ids", []),
        "dry_run": dry_run,
        "limitations": limitations or [],
        "commands": [
            {
                "rendered_command": result.rendered_command,
                "exit_code": result.returncode,
                "stdout": result.stdout or "",
                "stderr": result.stderr or "",
            }
            for result in command_results
        ],
    }
    json_path.write_text(json.dumps(sidecar, indent=2) + "\n", encoding="utf-8")
    return path


def install_bundle(bundle_path, replace=False):
    result = load_bundle(bundle_path)
    if not result.ok:
        return 1, validation_text(result)
    package_id = result.package["id"]
    destination = custom_package_root() / package_id
    if destination.exists() and not replace:
        return 1, f"Package {package_id} is already installed. Use --replace to overwrite it.\n"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(bundle_path, destination)
    return 0, f"Installed package: {package_id}\nInstall Path: {destination}\n"


def _prompt(prompt, default=None):
    suffix = f" [{default}]" if default is not None else ""
    value = input(f"{prompt}{suffix}: ").strip()
    if value:
        return value
    return default if default is not None else ""


def _prompt_bool(prompt, default=False):
    default_text = "y" if default else "n"
    value = _prompt(prompt, default_text).lower()
    return value in {"y", "yes", "true", "1"}


def _prompt_int(prompt, default=0):
    return int(_prompt(prompt, str(default)))


def create_interactive_bundle(output_path):
    package_id = _prompt("Package ID")
    label = _prompt("Package label", package_id)
    haskell_nodes = _prompt_int("Haskell node count", 3)
    amaru_nodes = _prompt_int("Amaru node count", 0)
    peer_sharing = _prompt_bool("Enable PeerSharing?", False)
    submit_enabled = _prompt_bool("Enable Submit API?", False)
    logs = _prompt_bool("Enable logs?", True)
    metrics = _prompt_bool("Enable metrics?", True)
    trace_buffer = _prompt_bool("Enable trace buffer?", False)
    topology_mode = _prompt("Topology mode", "local-only")
    execution_type = _prompt("Execution type", "read-only-runtime")
    mutates_runtime = _prompt_bool("May mutate runtime?", False)
    touches_public_network = _prompt_bool("May touch public network?", False)
    output_dir = _prompt("Evidence output directory", f"agent/testing/custom/{package_id}")

    package = {
        "id": package_id,
        "label": label,
        "version": 1,
        "execution_type": execution_type,
        "safety_level": "approval-required" if mutates_runtime or touches_public_network else "safe",
        "requires_approval": bool(mutates_runtime or touches_public_network),
        "mutates_runtime": mutates_runtime,
        "touches_public_network": touches_public_network,
        "profile_id": package_id,
        "candidate_ids": [],
        "output_dir": output_dir,
        "commands": [],
    }
    profile = {
        "id": package_id,
        "label": f"{label} Profile",
        "network_magic": 42,
        "node_counts": {"haskell": haskell_nodes, "amaru": amaru_nodes},
        "peer_sharing": peer_sharing,
        "submit_api": {"enabled": submit_enabled, "bind": "127.0.0.1:0"},
        "observability": {"logs": logs, "metrics": metrics, "trace_buffer": trace_buffer},
        "topology": {"mode": topology_mode, "public_roots": [], "bootstrap_peers": []},
        "runtime_root": f"${HOME}/cardano-profiles/{package_id}",
    }
    result = validate_bundle_data(package, profile)
    print(validation_text(result), end="")
    if not result.ok:
        return 1
    if not _prompt_bool("Write bundle?", False):
        print("Create cancelled.")
        return 1
    output = Path(output_path)
    output.mkdir(parents=True, exist_ok=True)
    (output / "package.json").write_text(json.dumps(package, indent=2) + "\n", encoding="utf-8")
    (output / "profile.json").write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
    (output / "README.md").write_text(f"# {label}\n\nGenerated custom cardano-profile package.\n", encoding="utf-8")
    print(f"Wrote bundle: {output}")
    return 0
