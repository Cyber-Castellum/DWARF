import json
import shlex
from dataclasses import dataclass
from pathlib import Path

from profile_manager.profile_shapes import shape_from_profile_dict


PROFILE_ROOT = Path(__file__).resolve().parents[1] / "profiles"

# Default locations on the remote build host.
REMOTE_SOURCE_PATH = "${HOME}/cardano-node"
REMOTE_DOCKERFILE_PATH = "${HOME}/dwarf-fw/devnet-build/cardano-node.Dockerfile"


@dataclass(frozen=True)
class Profile:
    id: str
    label: str
    node_type: str
    node_count: int
    amaru_node_count: int
    network_magic: int
    peer_sharing: bool
    remote_runtime_root: str
    compose_project: str
    topology_pattern: str | None = None
    shared_genesis: bool = False
    amaru_network: str | None = None
    upstream_peer_address: str | None = None
    listen_address: str | None = None
    config_source_dir: str | None = None
    public_network: str | None = None
    testbed: str | None = None

    @classmethod
    def from_dict(cls, data):
        shape = shape_from_profile_dict(data)
        return cls(
            id=data["id"],
            label=data["label"],
            node_type=shape.node_type,
            node_count=shape.haskell_count,
            amaru_node_count=shape.amaru_count,
            network_magic=int(data["network_magic"]),
            peer_sharing=bool(data["peer_sharing"]),
            remote_runtime_root=shape.remote_runtime_root,
            compose_project=shape.compose_project,
            topology_pattern=shape.topology_pattern,
            shared_genesis=shape.shared_genesis,
            amaru_network=data.get("amaru_network"),
            upstream_peer_address=data.get("upstream_peer_address"),
            listen_address=data.get("listen_address"),
            config_source_dir=data.get("config_source_dir"),
            public_network=data.get("public_network"),
            testbed=data.get("testbed"),
        )


def load_profiles():
    profiles = []
    for path in sorted(PROFILE_ROOT.glob("*/profile.yaml")):
        profiles.append(Profile.from_dict(json.loads(path.read_text(encoding="utf-8"))))
    return profiles


def find_profile(profile_id):
    for profile in load_profiles():
        if profile.id == profile_id:
            return profile
    raise KeyError(f"Unknown profile: {profile_id}")


def profile_list_text():
    lines = ["Available profiles:"]
    for profile in load_profiles():
        setting = "PeerSharingEnabled" if profile.peer_sharing else "PeerSharingDisabled"
        if profile.amaru_node_count:
            count_text = f"haskell={profile.node_count} amaru={profile.amaru_node_count}"
        else:
            count_text = f"nodes={profile.node_count}"
        lines.append(f"- {profile.id}: {profile.label} ({setting}, {count_text})")
    return "\n".join(lines) + "\n"


def profile_diff_text(left_id, right_id):
    left = find_profile(left_id)
    right = find_profile(right_id)
    fields = (
        "label",
        "node_type",
        "node_count",
        "amaru_node_count",
        "network_magic",
        "peer_sharing",
        "remote_runtime_root",
        "compose_project",
        "topology_pattern",
        "shared_genesis",
        "amaru_network",
        "upstream_peer_address",
        "listen_address",
        "config_source_dir",
        "public_network",
        "testbed",
    )
    lines = [
        "Profile diff",
        f"Left: {left.id}",
        f"Right: {right.id}",
        "",
        "| Field | Left | Right | Status |",
        "|---|---|---|---|",
    ]
    for field in fields:
        left_value = getattr(left, field)
        right_value = getattr(right, field)
        status = "same" if left_value == right_value else f"{left_value} -> {right_value}"
        lines.append(f"| {field} | {left_value} | {right_value} | {status} |")
    return "\n".join(lines) + "\n"


def status_command():
    return r"""set -e
echo "HOST=$(hostname)"
date -u
echo "DOCKER_STATUS"
docker info --format 'server_version={{.ServerVersion}} containers={{.Containers}} running={{.ContainersRunning}}' 2>/dev/null || echo "docker unavailable"
echo "DWARF_COMPOSE_PROJECTS"
docker compose ls --format json 2>/dev/null | grep -E 'dwarf-' || true
echo "CARDANO_CONTAINERS"
docker ps --filter 'label=ada2.managed=dwarf' --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}' 2>/dev/null || true
echo "RUNTIME_SIZES"
for d in ${HOME}/cardano-profiles/*/env; do
  [ -e "$d" ] && du -sh "$d" 2>/dev/null || true
done
echo "IMAGES"
docker image ls 'dwarf/cardano-node' --format '{{.Repository}}:{{.Tag}} size={{.Size}}' 2>/dev/null || true
"""


def active_profile_command():
    """Return a stdout stream of "<project>	<service>	<status>" lines for any active Dwarf devnet."""
    return r"""docker ps --filter 'label=ada2.managed=dwarf' --format '{{.Labels}}\t{{.Names}}\t{{.Status}}' 2>/dev/null | grep 'ada2.managed=dwarf' || true"""


def _bool_text(value):
    return "true" if value else "false"


def _profile_deploy_mode(profile):
    if profile.node_count > 0 and profile.amaru_node_count > 0:
        return "mixed"
    if profile.amaru_node_count > 0:
        return "amaru-only"
    return "haskell-only"


def _is_generated_haskell_local_profile(profile):
    return (
        _profile_deploy_mode(profile) == "haskell-only"
        and profile.config_source_dir is None
        and profile.topology_pattern == "local-mesh"
        and profile.node_count > 1
    )


def _public_network(profile):
    return profile.public_network or profile.amaru_network or "preview"


def _public_testbed(profile):
    return profile.testbed or f"public-{_public_network(profile)}"


def _profile_as_custom_bundle(profile):
    return (
        {
            "id": profile.id,
            "label": profile.label,
            "version": 1,
            "execution_type": "approval-required-runtime",
            "safety_level": "approval-required",
            "requires_approval": True,
            "mutates_runtime": True,
            "touches_public_network": False,
            "profile_id": profile.id,
            "candidate_ids": [],
            "output_dir": f"agent/testing/devnet-profiles/{profile.id}",
            "commands": [],
        },
        {
            "id": profile.id,
            "label": profile.label,
            "network_magic": profile.network_magic,
            "node_counts": {"haskell": profile.node_count, "amaru": profile.amaru_node_count},
            "peer_sharing": profile.peer_sharing,
            "submit_api": {"enabled": False, "bind": "127.0.0.1:8090"},
            "observability": {"logs": True},
            "topology": {
                "mode": "local-only",
                "pattern": profile.topology_pattern,
                "shared_genesis": profile.shared_genesis,
                "public_roots": [],
                "bootstrap_peers": [],
            },
            "runtime_root": profile.remote_runtime_root,
            "compose_project": profile.compose_project,
        },
    )


def compose_template(profile):
    """Render a docker-compose.yml for a profile.

    N node services, one per pool node. Each shares a bridge network named after
    the compose project so the nodes can reach each other by container name. The
    env dir is bind-mounted into each container.
    """
    project = profile.compose_project
    network = f"{project}-net"
    lines = [
        "name: " + project,
        "networks:",
        f"  {network}:",
        "    driver: bridge",
        "services:",
    ]
    for i in range(1, profile.node_count + 1):
        service = f"node{i}"
        lines.extend([
            f"  {service}:",
            "    image: dwarf/cardano-node:${CARDANO_NODE_SHA:-latest}",
            "    labels:",
            "      ada2.managed: dwarf",
            f"      ada2.profile: {profile.id}",
            f"      ada2.service: {service}",
            "    networks:",
            f"      {network}:",
            "        aliases:",
            f"          - {service}",
            "    volumes:",
            f"      - {profile.remote_runtime_root}/env:/env:rw",
            f"      - {profile.remote_runtime_root}/logs/{service}:/logs:rw",
            "    command:",
            f"      - --config=/env/node-data/node{i}/config.json",
            f"      - --topology=/env/node-data/node{i}/topology.json",
            f"      - --database-path=/env/node-data/node{i}/db",
            f"      - --socket-path=/env/socket/{service}/sock",
            "    restart: on-failure",
        ])
    return "\n".join(lines) + "\n"


def deploy_dry_run_text(profile):
    if _profile_deploy_mode(profile) == "mixed":
        from profile_manager.custom_packages import package_deploy_dry_run_text

        package, custom_profile = _profile_as_custom_bundle(profile)
        return package_deploy_dry_run_text(package, custom_profile)
    if _is_generated_haskell_local_profile(profile):
        return (
            f"DRY RUN deploy for {profile.id}\n"
            f"Generated Haskell-only local devnet: {profile.node_count} Haskell node(s), 0 Amaru node(s).\n"
            f"Topology pattern: {profile.topology_pattern}.\n"
            f"Shared genesis: {'yes' if profile.shared_genesis else 'no'}.\n"
            f"Would create remote runtime root: {profile.remote_runtime_root}\n"
            "Would create the devnet env via ${HOME}/.local/bin/cardano-testnet create-env.\n"
            "Would run Haskell nodes from ${HOME}/.local/bin/cardano-node under tmux sessions scoped to this profile.\n"
            "Would auto-assign localhost listener ports and rewrite local-mesh topology from the generated node count.\n"
            "Would write runtime metadata under runtime.json for host-process inspection.\n"
            "No remote state changed.\n"
        )
    if _profile_deploy_mode(profile) == "haskell-only" and profile.config_source_dir:
        network = _public_network(profile)
        return (
            f"DRY RUN deploy for {profile.id}\n"
            f"Would create remote runtime root: {profile.remote_runtime_root}\n"
            f"Would fail fast if upstream peer {profile.upstream_peer_address} is unreachable.\n"
            f"Would copy the official {network} config set from {profile.config_source_dir}.\n"
            f"Would rewrite topology.json under the runtime root to use {profile.upstream_peer_address} as the bootstrap peer.\n"
            f"Would start one Haskell cardano-node from ${HOME}/.local/bin/cardano-node listening on {profile.listen_address}.\n"
            "Would write runtime metadata under runtime.json and keep logs under logs/node1/.\n"
            f"This profile depends on public {network} connectivity and is not a self-contained local devnet.\n"
            "No remote state changed.\n"
        )
    if _profile_deploy_mode(profile) == "amaru-only":
        network = _public_network(profile)
        return (
            f"DRY RUN deploy for {profile.id}\n"
            f"Would create remote runtime root: {profile.remote_runtime_root}\n"
            f"Would fail fast if upstream peer {profile.upstream_peer_address} is unreachable.\n"
            f"Would bootstrap Amaru for network {profile.amaru_network} using the bundled upstream bootstrap config.\n"
            f"Would start one Amaru node from ${HOME}/amaru-verification/target/debug/amaru listening on {profile.listen_address}.\n"
            "Would write runtime metadata under runtime.json and keep logs under logs/amaru1/.\n"
            f"This profile depends on public {network} connectivity and is not a self-contained local devnet.\n"
            "No remote state changed.\n"
        )
    return (
        f"DRY RUN deploy for {profile.id}\n"
        f"Would create remote runtime root: {profile.remote_runtime_root}\n"
        f"Would generate devnet env via cardano-testnet create-env (in a build-stage container).\n"
        f"Would (re)build docker image dwarf/cardano-node from {REMOTE_SOURCE_PATH}.\n"
        f"Would bring up docker compose project: {profile.compose_project} ({profile.node_count} node containers).\n"
        "Would verify no other Dwarf-managed compose project is running first.\n"
        "No remote state changed.\n"
    )


def remove_dry_run_text():
    return (
        "DRY RUN remove\n"
        "Would detect active Dwarf-managed compose projects.\n"
        "Would ask for explicit confirmation before stopping anything.\n"
        "Would run docker compose down -v and archive the runtime directory.\n"
        "No remote state changed.\n"
    )


def deploy_command(profile):
    """Build the image, generate the env via cardano-testnet, write compose, up -d."""
    deploy_mode = _profile_deploy_mode(profile)
    if deploy_mode == "mixed":
        from profile_manager.custom_packages import package_deploy_command

        package, custom_profile = _profile_as_custom_bundle(profile)
        return package_deploy_command(package, custom_profile)
    if _is_generated_haskell_local_profile(profile):
        runtime = shlex.quote(profile.remote_runtime_root)
        project = shlex.quote(profile.compose_project)
        peer_sharing = _bool_text(profile.peer_sharing)
        return f"""set -e
runtime={runtime}
project={project}
cardano_node_bin=${HOME}/.local/bin/cardano-node
cardano_cli_bin=${HOME}/.local/bin/cardano-cli
cardano_testnet_bin=${HOME}/.local/bin/cardano-testnet
if [ -e "$runtime/env" ]; then
  echo "Runtime assets already exist under: $runtime" >&2
  exit 4
fi
mkdir -p "$runtime/logs" "$runtime/socket" "$runtime/pids"
cardano_sha=$("$cardano_node_bin" --version 2>/dev/null | head -n 1 | tr ' /' '__' | tr -cd '[:alnum:]_.-')
[ -n "$cardano_sha" ] || cardano_sha=host
printf 'CARDANO_NODE_SHA=%s\\n' "$cardano_sha" > "$runtime/.env"
mkdir -p "$runtime/env"
export CARDANO_NODE="$cardano_node_bin"
export CARDANO_CLI="$cardano_cli_bin"
"$cardano_testnet_bin" \\
  create-env --output "$runtime/env" --num-pool-nodes {profile.node_count} --testnet-magic {profile.network_magic} --node-logging-format json
python3 - "$runtime/env/configuration.yaml" <<'PY'
import sys
path = sys.argv[1]
body = open(path, encoding="utf-8").read()
body = body.replace('"PeerSharing": true', '"PeerSharing": {peer_sharing}')
body = body.replace('"PeerSharing": false', '"PeerSharing": {peer_sharing}')
open(path, "w", encoding="utf-8").write(body)
PY
python3 - "$runtime/env" "$runtime/runtime.json" "$project" {profile.node_count} <<'PY'
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
for i in $(seq 1 {profile.node_count}); do mkdir -p "$runtime/logs/node$i"; done
for i in $(seq 1 {profile.node_count}); do
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
sleep 10
tmux ls | grep "$project" || true
"""
    if deploy_mode == "haskell-only" and profile.config_source_dir:
        runtime = shlex.quote(profile.remote_runtime_root)
        session = shlex.quote(profile.compose_project)
        node_bin = shlex.quote("${HOME}/.local/bin/cardano-node")
        public_network = _public_network(profile)
        testbed = _public_testbed(profile)
        listen_address_raw = profile.listen_address or "127.0.0.1:39100"
        listen_host, listen_port_text = listen_address_raw.rsplit(":", 1)
        listen_port = int(listen_port_text)
        peer_address = shlex.quote(profile.upstream_peer_address or "preview-node.play.dev.cardano.org:3001")
        listen_address = shlex.quote(listen_address_raw)
        listen_host_q = shlex.quote(listen_host)
        config_source_dir = shlex.quote(profile.config_source_dir)
        return f"""set -e
runtime={runtime}
session={session}
node_bin={node_bin}
peer_address={peer_address}
listen_address={listen_address}
config_source_dir={config_source_dir}
config_root="$runtime/config"
db_dir="$runtime/db"
socket_dir="$runtime/socket"
socket_path="$socket_dir/node.sock"
log_dir="$runtime/logs/node1"
log_path="$log_dir/stdout.log"
metadata_path="$runtime/runtime.json"
pid_file="$runtime/node.pid"
if tmux has-session -t "$session" 2>/dev/null; then
  echo "Session already exists: $session" >&2
  exit 3
fi
if [ -e "$db_dir" ] || [ -e "$config_root" ]; then
  echo "Runtime assets already exist under: $runtime" >&2
  exit 4
fi
python3 - "$peer_address" <<'PY'
import socket
import sys
peer = sys.argv[1]
host, port_text = peer.rsplit(":", 1)
port = int(port_text)
with socket.create_connection((host, port), timeout=5):
    pass
PY
mkdir -p "$config_root" "$db_dir" "$socket_dir" "$log_dir"
cp "$config_source_dir"/config.json "$config_root"/
cp "$config_source_dir"/topology.json "$config_root"/
cp "$config_source_dir"/byron-genesis.json "$config_root"/
cp "$config_source_dir"/shelley-genesis.json "$config_root"/
cp "$config_source_dir"/alonzo-genesis.json "$config_root"/
cp "$config_source_dir"/conway-genesis.json "$config_root"/
for optional_file in checkpoints.json peer-snapshot.json; do
  [ -f "$config_source_dir/$optional_file" ] && cp "$config_source_dir/$optional_file" "$config_root"/
done
python3 - "$peer_address" "$config_root/topology.json" <<'PY'
import json
import pathlib
import sys

peer = sys.argv[1]
topology_path = pathlib.Path(sys.argv[2])
host, port_text = peer.rsplit(":", 1)
port = int(port_text)
body = json.loads(topology_path.read_text(encoding="utf-8"))
body["bootstrapPeers"] = [{{"address": host, "port": port}}]
topology_path.write_text(json.dumps(body, indent=2) + "\\n", encoding="utf-8")
PY
cat > "$metadata_path" <<JSON
{{
  "profile_id": "{profile.id}",
  "target_implementation": "cardano-node",
  "network": "{public_network}",
  "upstream_peer_address": "{profile.upstream_peer_address or 'preview-node.play.dev.cardano.org:3001'}",
  "listen_address": "{profile.listen_address or '127.0.0.1:39100'}",
  "session": "{profile.compose_project}",
  "binary": "${HOME}/.local/bin/cardano-node",
  "chain_dir": "$db_dir",
  "log_path": "$log_dir/stdout.log",
  "pid_file": "$pid_file",
  "socket_path": "$socket_path",
  "config_root": "$config_root",
  "testbed": "{testbed}"
}}
JSON
tmux new-session -d -s "$session" "bash -lc 'cd $config_root; echo \\$$ > $pid_file; exec $node_bin run --config config.json --topology topology.json --database-path $db_dir --socket-path $socket_path --port {listen_port} --host-addr {listen_host_q} 2>&1 | tee -a $log_path'"
sleep 10
tmux ls
"""
    if deploy_mode == "amaru-only":
        runtime = shlex.quote(profile.remote_runtime_root)
        session = shlex.quote(profile.compose_project)
        amaru_bin = shlex.quote("${HOME}/amaru-verification/target/debug/amaru")
        amaru_network = shlex.quote(profile.amaru_network or "preview")
        public_network = _public_network(profile)
        testbed = _public_testbed(profile)
        peer_address = shlex.quote(profile.upstream_peer_address or "preview-node.play.dev.cardano.org:3001")
        listen_address = shlex.quote(profile.listen_address or "127.0.0.1:39000")
        return f"""set -e
runtime={runtime}
session={session}
amaru_bin={amaru_bin}
amaru_network={amaru_network}
peer_address={peer_address}
listen_address={listen_address}
state_root="$runtime/amaru1"
chain_dir="$state_root/chain.$amaru_network.db"
ledger_dir="$state_root/ledger.$amaru_network.db"
log_dir="$runtime/logs/amaru1"
log_path="$log_dir/stdout.log"
bootstrap_stdout="$log_dir/bootstrap.stdout.log"
bootstrap_stderr="$log_dir/bootstrap.stderr.log"
metadata_path="$runtime/runtime.json"
pid_file="$state_root/amaru.pid"
if tmux has-session -t "$session" 2>/dev/null; then
  echo "Session already exists: $session" >&2
  exit 3
fi
if [ -e "$state_root" ]; then
  echo "Runtime assets already exist under: $state_root" >&2
  exit 4
fi
python3 - "$peer_address" <<'PY'
import socket
import sys
peer = sys.argv[1]
host, port_text = peer.rsplit(":", 1)
port = int(port_text)
with socket.create_connection((host, port), timeout=5):
    pass
PY
mkdir -p "$state_root" "$log_dir"
cat > "$metadata_path" <<JSON
{{
  "profile_id": "{profile.id}",
  "target_implementation": "amaru",
  "network": "{public_network}",
  "upstream_peer_address": "{profile.upstream_peer_address or 'preview-node.play.dev.cardano.org:3001'}",
  "listen_address": "{profile.listen_address or '127.0.0.1:39000'}",
  "session": "{profile.compose_project}",
  "binary": "${HOME}/amaru-verification/target/debug/amaru",
  "chain_dir": "$state_root/chain.{profile.amaru_network or 'preview'}.db",
  "ledger_dir": "$state_root/ledger.{profile.amaru_network or 'preview'}.db",
  "log_path": "$log_dir/stdout.log",
  "pid_file": "$state_root/amaru.pid",
  "testbed": "{testbed}"
}}
JSON
cd "$runtime"
"$amaru_bin" bootstrap --network "$amaru_network" --ledger-dir "$ledger_dir" --chain-dir "$chain_dir" >"$bootstrap_stdout" 2>"$bootstrap_stderr"
tmux new-session -d -s "$session" "bash -lc 'cd $runtime; export RUST_BACKTRACE=full; $amaru_bin run --network $amaru_network --peer-address $peer_address --listen-address $listen_address --ledger-dir $ledger_dir --chain-dir $chain_dir --pid-file $pid_file 2>&1 | tee -a $log_path'"
sleep 10
tmux ls
"""
    runtime = shlex.quote(profile.remote_runtime_root)
    project = shlex.quote(profile.compose_project)
    peer_sharing = _bool_text(profile.peer_sharing)
    compose_yaml = compose_template(profile)
    compose_heredoc = compose_yaml.rstrip("\n")
    return f"""set -e
runtime={runtime}
project={project}
source_path={shlex.quote(REMOTE_SOURCE_PATH)}
dockerfile={shlex.quote(REMOTE_DOCKERFILE_PATH)}
if docker compose --project-name "$project" ls --format '{{{{.Name}}}}' 2>/dev/null | grep -q "^$project$"; then
  echo "Compose project already up: $project" >&2
  exit 3
fi
if [ -e "$runtime/env" ]; then
  echo "Runtime env already exists: $runtime/env" >&2
  exit 4
fi
mkdir -p "$runtime/logs"
sha=$(git -C "$source_path" rev-parse --short HEAD 2>/dev/null || echo latest)
echo "CARDANO_NODE_SHA=$sha" > "$runtime/.env"
docker build --file "$dockerfile" --tag "dwarf/cardano-node:$sha" --tag "dwarf/cardano-node:latest" "$source_path"
docker run --rm \\
  --user "$(id -u):$(id -g)" \\
  -v "$runtime:/work" \\
  --entrypoint /usr/local/bin/cardano-testnet \\
  "dwarf/cardano-node:$sha" \\
  create-env --output /work/env --num-pool-nodes {profile.node_count} --testnet-magic {profile.network_magic} --node-logging-format json
python3 - "$runtime/env/configuration.yaml" <<'PY'
import sys
path = sys.argv[1]
body = open(path, encoding="utf-8").read()
body = body.replace('"PeerSharing": true', '"PeerSharing": {peer_sharing}')
body = body.replace('"PeerSharing": false', '"PeerSharing": {peer_sharing}')
open(path, "w", encoding="utf-8").write(body)
PY
python3 - "$runtime/env" {profile.node_count} <<'PY'
import json, sys, os
env_dir = sys.argv[1]
node_count = int(sys.argv[2])
# Rewrite each node's topology.json to point at docker-network peers (node1..nodeN)
# instead of the host-loopback addresses that cardano-testnet generates by default.
for i in range(1, node_count + 1):
    topo_path = os.path.join(env_dir, "node-data", f"node{{i}}", "topology.json")
    if not os.path.exists(topo_path):
        continue
    data = json.load(open(topo_path))
    peers = [{{"address": f"node{{j}}", "port": 3001, "valency": 1}}
             for j in range(1, node_count + 1) if j != i]
    # Shape per cardano-node topology spec; keep the publicRoots/useLedgerAfterSlot keys
    # that were present in the original file if any.
    data["Producers" if "Producers" in data else "localRoots"] = peers
    json.dump(data, open(topo_path, "w"), indent=2)
PY
mkdir -p {{"$runtime/logs/node"}}1{{"$runtime/logs/node"}}2{{"$runtime/logs/node"}}3 2>/dev/null || true
for i in $(seq 1 {profile.node_count}); do mkdir -p "$runtime/logs/node$i"; done
cat > "$runtime/docker-compose.yml" <<'COMPOSE_EOF'
{compose_heredoc}
COMPOSE_EOF
docker compose --project-directory "$runtime" --project-name "$project" up -d
docker compose --project-directory "$runtime" --project-name "$project" ps
"""


def remove_command(remote_base_path):
    base = shlex.quote(remote_base_path)
    return f"""set -e
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
archive_root={base}/archive
mkdir -p "$archive_root"
# Stop and remove every Dwarf-managed compose project.
docker ps --filter 'label=ada2.managed=dwarf' --format '{{{{.Label "com.docker.compose.project"}}}}' 2>/dev/null | sort -u | while read -r project; do
  [ -n "$project" ] || continue
  proj_dir=$(docker compose ls --all --format json 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); import json as j
for row in d:
  if row.get('Name') == '$project':
    print(row.get('ConfigFiles',''))
    break" 2>/dev/null || true)
  docker compose --project-name "$project" down --volumes --remove-orphans 2>/dev/null || true
done
# Kill stragglers (direct docker containers not in a compose project)
docker ps --filter 'label=ada2.managed=dwarf' --format '{{{{.ID}}}}' 2>/dev/null | xargs -r docker rm -f 2>/dev/null || true
# Archive runtime directories.
for path in {base}/profile-*; do
  if [ -e "$path" ]; then
    name=$(basename "$path")
    mv "$path" "$archive_root/${{name}}-$timestamp"
  fi
done
docker ps --filter 'label=ada2.managed=dwarf' --format 'table {{{{.Names}}}}' 2>/dev/null || true
"""
