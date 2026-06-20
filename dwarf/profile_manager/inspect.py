"""Shell snippets the CLI sends over SSH to inspect devnet profiles.

Output fields are stable (cardano_node_processes, socket_count, listener_count,
loopback_only, tip_block, sync_progress, etc.) so the dashboard's parser in
profile_manager.dashboard can consume both host/tmux-style and docker-style
runtime layouts.
"""
import shlex

from profile_manager.profiles import find_profile


LEGACY_RUNTIME_ROOT = "${HOME}/cardano-devnet"


def resolve_runtime(profile_id=None, runtime_root=None):
    if runtime_root:
        label = runtime_root.strip("/").replace("/", "-") or "root"
        return runtime_root, label
    if profile_id:
        profile = find_profile(profile_id)
        return profile.remote_runtime_root, profile.id
    return LEGACY_RUNTIME_ROOT, "legacy-cardano-devnet"


def _quote(value):
    return shlex.quote(value)


def _project_for_runtime(runtime_root):
    """Derive the docker compose project name from a runtime root.

    The profile definition provides `compose_project`; when only a bare runtime
    path is provided (e.g. --runtime-root), we fall back to the directory name.
    """
    try:
        for profile in _load_profiles():
            if profile.remote_runtime_root == runtime_root:
                return profile.compose_project
    except Exception:
        pass
    name = runtime_root.rstrip("/").split("/")[-1]
    return f"dwarf-{name}" if name else "dwarf-unknown"


def _load_profiles():
    from profile_manager.profiles import load_profiles
    return load_profiles()


def _header(runtime_root, view):
    runtime = _quote(runtime_root)
    project = _quote(_project_for_runtime(runtime_root))
    return f"""set -e
runtime={runtime}
env_root="$runtime/env"
project={project}
metadata_path="$runtime/runtime.json"
runtime_layout="legacy-devnet"
target_implementation="cardano-node"
testbed="local-devnet"
listen_address=""
upstream_peer_address=""
chain_dir=""
ledger_dir=""
log_path=""
pid_file=""
socket_path=""
config_root=""
session_name=""
if [ -f "$metadata_path" ]; then
  eval "$(python3 - "$metadata_path" <<'PY'
import json
import shlex
import sys
from pathlib import Path

path = Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
impl = data.get("target_implementation", "unknown")
testbed = data.get("testbed", "unknown")
layout = "metadata"
if impl == "amaru" and testbed == "public-preview":
    layout = "preview-amaru"
elif impl == "cardano-node" and testbed == "public-preview":
    layout = "preview-haskell"
for key, value in {{
    "runtime_layout": layout,
    "target_implementation": impl,
    "testbed": testbed,
    "listen_address": data.get("listen_address", ""),
    "upstream_peer_address": data.get("upstream_peer_address", ""),
    "chain_dir": data.get("chain_dir", ""),
    "ledger_dir": data.get("ledger_dir", ""),
    "log_path": data.get("log_path", ""),
    "pid_file": data.get("pid_file", ""),
    "socket_path": data.get("socket_path", ""),
    "config_root": data.get("config_root", ""),
    "session_name": data.get("session", ""),
}}.items():
    print(f"{{key}}={{shlex.quote(str(value))}}")
PY
)"
fi
echo "INSPECT_VIEW={view}"
echo "RUNTIME_ROOT=$runtime"
echo "ENV_ROOT=$env_root"
echo "COMPOSE_PROJECT=$project"
echo "RUNTIME_LAYOUT=$runtime_layout"
echo "TARGET_IMPLEMENTATION=$target_implementation"
echo "TESTBED=$testbed"
"""


def inspect_env_command(runtime_root):
    return _header(runtime_root, "env") + r"""
echo "## runtime paths"
if [ "$runtime_layout" = "preview-amaru" ]; then
  for path in "$runtime" "$metadata_path" "$chain_dir" "$ledger_dir" "$log_path" "$pid_file"; do
    if [ -e "$path" ]; then
      printf "exists\t%s\n" "$path"
    else
      printf "missing\t%s\n" "$path"
    fi
  done
  echo "## runtime metadata"
  echo "listen_address=$listen_address"
  echo "upstream_peer_address=$upstream_peer_address"
  echo "session_name=$session_name"
  echo "## sizes"
  du -sh "$runtime" 2>/dev/null || true
  du -sh "$chain_dir" "$ledger_dir" 2>/dev/null || true
  du -sh "$log_path" 2>/dev/null || true
elif [ "$runtime_layout" = "preview-haskell" ]; then
  for path in "$runtime" "$metadata_path" "$config_root" "$config_root/config.json" "$config_root/topology.json" "$chain_dir" "$socket_path" "$log_path" "$pid_file"; do
    if [ -e "$path" ]; then
      printf "exists\t%s\n" "$path"
    else
      printf "missing\t%s\n" "$path"
    fi
  done
  echo "## runtime metadata"
  echo "listen_address=$listen_address"
  echo "upstream_peer_address=$upstream_peer_address"
  echo "session_name=$session_name"
  echo "## preview config"
  if [ -f "$config_root/config.json" ]; then
    jq '{EnableP2P, EnableLogging, TraceBlockFetchClient, TraceChainSyncClient}' "$config_root/config.json" 2>/dev/null || true
  fi
  if [ -f "$config_root/topology.json" ]; then
    echo "TOPOLOGY=$config_root/topology.json"
    cat "$config_root/topology.json" 2>/dev/null || true
  fi
  echo "## sizes"
  du -sh "$runtime" 2>/dev/null || true
  du -sh "$chain_dir" 2>/dev/null || true
  du -sh "$log_path" 2>/dev/null || true
else
  for path in "$runtime" "$env_root" "$env_root/configuration.yaml" "$env_root/shelley-genesis.json" "$env_root/byron-genesis.json" "$env_root/conway-genesis.json" "$env_root/logs" "$env_root/socket"; do
    if [ -e "$path" ]; then
      printf "exists\t%s\n" "$path"
    else
      printf "missing\t%s\n" "$path"
    fi
  done
  echo "## config values"
  if [ -f "$env_root/configuration.yaml" ]; then
    grep -n '"PeerSharing"\|"RequiresNetworkMagic"\|"TraceConnectionManager"\|"TracePeerSelection"\|"TraceKeepAlive"\|"TracePeerSharing"' "$env_root/configuration.yaml" || true
  fi
  echo "## topology summaries"
  for topology in "$env_root"/node-data/node*/topology.json; do
    [ -f "$topology" ] || continue
    echo "TOPOLOGY=$topology"
    jq '{bootstrapPeers, localRoots, publicRoots, useLedgerAfterSlot}' "$topology" 2>/dev/null || cat "$topology"
  done
  echo "## sizes"
  du -sh "$runtime" 2>/dev/null || true
  du -sh "$env_root/logs" 2>/dev/null || true
  du -sh "$env_root"/node-data/node*/db 2>/dev/null || true
fi
echo "## containers"
docker compose --project-name "$project" ps --format 'table {{.Name}}\t{{.Image}}\t{{.Status}}' 2>/dev/null || true
"""


def inspect_nodes_command(runtime_root):
    return _header(runtime_root, "nodes") + r"""
echo "## containers"
docker compose --project-name "$project" ps --format '{{.Name}}\t{{.Image}}\t{{.Status}}' 2>/dev/null || true
echo "## node details"
if [ "$runtime_layout" = "preview-amaru" ]; then
  echo "NODE=amaru1"
  echo "session=$session_name"
  echo "chain_dir=$chain_dir"
  echo "ledger_dir=$ledger_dir"
  echo "listen_address=$listen_address"
  echo "pid_file=$pid_file"
  echo "log=$log_path"
  [ -d "$chain_dir" ] && echo "chain_dir_status=present" || echo "chain_dir_status=missing"
  [ -d "$ledger_dir" ] && echo "ledger_dir_status=present" || echo "ledger_dir_status=missing"
  [ -f "$pid_file" ] && echo "pid_file_status=present" || echo "pid_file_status=missing"
  if [ -f "$log_path" ]; then
    size=$(du -sh "$log_path" 2>/dev/null | awk '{print $1}')
    mtime=$(stat -c %y "$log_path" 2>/dev/null || stat -f "%Sm" "$log_path" 2>/dev/null || true)
    echo "log=$log_path size=$size mtime=$mtime"
  else
    echo "log=$log_path missing"
  fi
elif [ "$runtime_layout" = "preview-haskell" ]; then
  echo "NODE=node1"
  echo "session=$session_name"
  echo "config_root=$config_root"
  echo "db=$chain_dir"
  echo "socket=$socket_path"
  echo "listen_address=$listen_address"
  echo "pid_file=$pid_file"
  echo "log=$log_path"
  [ -d "$config_root" ] && echo "config_root_status=present" || echo "config_root_status=missing"
  [ -d "$chain_dir" ] && echo "db_status=present" || echo "db_status=missing"
  [ -S "$socket_path" ] && echo "socket_status=present" || echo "socket_status=missing"
  [ -f "$pid_file" ] && echo "pid_file_status=present" || echo "pid_file_status=missing"
  if [ -f "$log_path" ]; then
    size=$(du -sh "$log_path" 2>/dev/null | awk '{print $1}')
    mtime=$(stat -c %y "$log_path" 2>/dev/null || stat -f "%Sm" "$log_path" 2>/dev/null || true)
    echo "log=$log_path size=$size mtime=$mtime"
  else
    echo "log=$log_path missing"
  fi
else
  for node in node1 node2 node3; do
    node_dir="$env_root/node-data/$node"
    sock="$env_root/socket/$node/sock"
    container="${project}-${node}-1"
    echo "NODE=$node"
    echo "container=$container"
    echo "topology=$node_dir/topology.json"
    echo "db=$node_dir/db"
    echo "socket=$sock"
    [ -d "$node_dir" ] && echo "node_dir_status=present" || echo "node_dir_status=missing"
    [ -S "$sock" ] && echo "socket_status=present" || echo "socket_status=missing"
    du -sh "$node_dir/db" 2>/dev/null || true
    if docker inspect "$container" >/dev/null 2>&1; then
      status=$(docker inspect -f '{{.State.Status}}' "$container" 2>/dev/null || echo "unknown")
      echo "container_status=$status"
    else
      echo "container_status=not-containerized"
    fi
    for log in "$env_root/logs/$node/stdout.log" "$env_root/logs/$node/stderr.log"; do
      if [ -f "$log" ]; then
        size=$(du -sh "$log" 2>/dev/null | awk '{print $1}')
        mtime=$(stat -c %y "$log" 2>/dev/null || stat -f "%Sm" "$log" 2>/dev/null || true)
        echo "log=$log size=$size mtime=$mtime"
      else
        echo "log=$log missing"
      fi
    done
  done
fi
"""


def inspect_health_command(runtime_root):
    return _header(runtime_root, "health") + r"""
echo "## session"
docker compose --project-name "$project" ls --format json 2>/dev/null | grep -E "\"Name\":\"$project\"" || true
tmux ls 2>/dev/null | grep -E 'cardano-profile-|cardano-devnet' || true
echo "## counts"
if [ "$runtime_layout" = "preview-amaru" ]; then
  amaru_processes=$(python3 - "$pid_file" <<'PY'
import os, sys
path = sys.argv[1]
try:
    pid = int(open(path, encoding="utf-8").read().strip())
    os.kill(pid, 0)
except Exception:
    print(0)
else:
    print(1)
PY
)
  echo "amaru_processes=$amaru_processes"
  echo "socket_count=0"
  listener_ok=$(python3 - "$listen_address" <<'PY'
import socket, sys
host, port = sys.argv[1].rsplit(":", 1)
try:
    with socket.create_connection((host, int(port)), timeout=3):
        print(1)
except OSError:
    print(0)
PY
)
  echo "listener_count=$listener_ok"
  if printf '%s' "$listen_address" | grep -Eq '^127\.|^\[::1\]|^localhost:'; then
    echo "loopback_only=true"
  else
    echo "loopback_only=false"
    echo "$listen_address"
  fi
  echo "## peer sharing"
  echo "peer_sharing=not_applicable"
  echo "## tip"
  echo "tip_query=not_applicable"
elif [ "$runtime_layout" = "preview-haskell" ]; then
  cardano_processes=$(python3 - "$pid_file" <<'PY'
import os, sys
path = sys.argv[1]
try:
    pid = int(open(path, encoding="utf-8").read().strip())
    os.kill(pid, 0)
except Exception:
    print(0)
else:
    print(1)
PY
)
  echo "cardano_node_processes=$cardano_processes"
  socket_count=$([ -S "$socket_path" ] && echo 1 || echo 0)
  echo "socket_count=$socket_count"
  listener_ok=$(python3 - "$listen_address" <<'PY'
import socket, sys
host, port = sys.argv[1].rsplit(":", 1)
try:
    with socket.create_connection((host, int(port)), timeout=3):
        print(1)
except OSError:
    print(0)
PY
)
  echo "listener_count=$listener_ok"
  if printf '%s' "$listen_address" | grep -Eq '^127\.|^\[::1\]|^localhost:'; then
    echo "loopback_only=true"
  else
    echo "loopback_only=false"
    echo "$listen_address"
  fi
  echo "## peer sharing"
  echo "peer_sharing=not_applicable"
  echo "## tip"
  if [ -S "$socket_path" ]; then
    CARDANO_NODE_SOCKET_PATH="$socket_path" ${HOME}/.local/bin/cardano-cli query tip --testnet-magic 2 2>/dev/null || echo "tip_query=failed"
    echo
  else
    echo "tip_query=missing_socket"
  fi
else
  docker_node_processes=$(docker ps --filter "label=com.docker.compose.project=$project" --filter "label=ada2.service" -q 2>/dev/null | wc -l | tr -d ' ')
  host_node_processes=$(ps -eo command 2>/dev/null | grep -Ec '[/]home/dwarf/.local/bin/cardano-node run' || true)
  if [ "${docker_node_processes:-0}" -gt 0 ]; then
    node_processes="$docker_node_processes"
  else
    node_processes="$host_node_processes"
  fi
  echo "cardano_node_processes=$node_processes"
  socket_count=$(find "$env_root/socket" -type s 2>/dev/null | wc -l | tr -d ' ')
  echo "socket_count=$socket_count"
  docker_listener_count=$(docker ps --filter "label=com.docker.compose.project=$project" -q 2>/dev/null | wc -l | tr -d ' ')
  host_listener_count=$(ss -ltnp 2>/dev/null | grep -Ec 'cardano|amaru' || true)
  if [ "${docker_listener_count:-0}" -gt 0 ]; then
    listener_count="$docker_listener_count"
  else
    listener_count="$host_listener_count"
  fi
  echo "listener_count=$listener_count"
  non_loopback=$(docker ps --filter "label=com.docker.compose.project=$project" --format '{{.Ports}}' 2>/dev/null | grep -v '127\.0\.0\.1' | grep -v '^$' | grep -E -- '->' || true)
  if [ -z "$non_loopback" ]; then
    non_loopback=$(ss -ltnp 2>/dev/null | grep -E 'cardano|amaru' | grep -Ev '127\.0\.0\.1|::1' || true)
  fi
  if [ -n "$non_loopback" ]; then
    echo "loopback_only=false"
    echo "$non_loopback"
  else
    echo "loopback_only=true"
  fi
  echo "## peer sharing"
  if [ -f "$env_root/configuration.yaml" ]; then
    grep -n '"PeerSharing"' "$env_root/configuration.yaml" || true
  fi
  echo "## tip"
  container="${project}-node1-1"
  socket="$env_root/socket/node1/sock"
  if [ -S "$socket" ]; then
    CARDANO_NODE_SOCKET_PATH="$socket" ${HOME}/.local/bin/cardano-cli query tip --testnet-magic 42 2>/dev/null || echo "tip_query=failed"
    echo
  elif docker inspect "$container" >/dev/null 2>&1; then
    docker exec -e CARDANO_NODE_SOCKET_PATH=/env/socket/node1/sock "$container" \
      cardano-cli query tip --testnet-magic 42 2>/dev/null || echo "tip_query=failed"
    echo
  else
    echo "tip_query=missing_socket"
  fi
fi
echo "## disk pressure"
df -h "$runtime" 2>/dev/null || df -h / 2>/dev/null || true
if [ "$runtime_layout" = "preview-amaru" ]; then
  du -sh "$runtime" "$chain_dir" "$ledger_dir" "$log_path" 2>/dev/null || true
elif [ "$runtime_layout" = "preview-haskell" ]; then
  du -sh "$runtime" "$chain_dir" "$log_path" 2>/dev/null || true
else
  du -sh "$runtime" "$env_root/logs" 2>/dev/null || true
fi
"""


def inspect_all_command(runtime_root):
    return "\n".join(
        [
            inspect_env_command(runtime_root),
            "echo",
            inspect_nodes_command(runtime_root),
            "echo",
            inspect_health_command(runtime_root),
        ]
    )


def command_for_view(view, runtime_root):
    if view == "env":
        return inspect_env_command(runtime_root)
    if view == "nodes":
        return inspect_nodes_command(runtime_root)
    if view == "health":
        return inspect_health_command(runtime_root)
    if view == "all":
        return inspect_all_command(runtime_root)
    raise ValueError(f"Unknown inspect view: {view}")


def doctor_command(runtime_root):
    return _header(runtime_root, "doctor") + r"""
echo "DOCTOR_SECTION=host"
hostname
uname -a
lsb_release -a 2>/dev/null || cat /etc/os-release 2>/dev/null || true
uptime
nproc
free -h 2>/dev/null || true
df -h "$runtime" 2>/dev/null || df -h / 2>/dev/null || true
echo "DOCTOR_SECTION=tooling"
for tool in docker git jq python3; do
  if command -v "$tool" >/dev/null 2>&1; then
    echo "tool_present=$tool"
  else
    echo "tool_missing=$tool"
  fi
done
docker --version 2>/dev/null || true
docker compose version 2>/dev/null || true
docker image ls 'dwarf/cardano-node' --format '{{.Repository}}:{{.Tag}} size={{.Size}}' 2>/dev/null || true
echo "DOCTOR_SECTION=runtime"
if [ "$runtime_layout" = "preview-amaru" ]; then
  for path in "$runtime" "$metadata_path" "$chain_dir" "$ledger_dir" "$log_path" "$pid_file"; do
    [ -e "$path" ] && echo "exists=$path" || echo "missing=$path"
  done
  echo "listen_address=$listen_address"
  echo "upstream_peer_address=$upstream_peer_address"
  echo "session_name=$session_name"
elif [ "$runtime_layout" = "preview-haskell" ]; then
  for path in "$runtime" "$metadata_path" "$config_root" "$config_root/config.json" "$config_root/topology.json" "$chain_dir" "$socket_path" "$log_path" "$pid_file"; do
    [ -e "$path" ] && echo "exists=$path" || echo "missing=$path"
  done
  echo "listen_address=$listen_address"
  echo "upstream_peer_address=$upstream_peer_address"
  echo "session_name=$session_name"
else
  for path in "$runtime" "$env_root" "$env_root/configuration.yaml" "$env_root/socket" "$env_root/logs"; do
    [ -e "$path" ] && echo "exists=$path" || echo "missing=$path"
  done
  docker compose --project-name "$project" ls 2>/dev/null || true
fi
echo "DOCTOR_SECTION=nodes"
if [ "$runtime_layout" = "preview-amaru" ]; then
  python3 - "$pid_file" <<'PY'
import os, sys
path = sys.argv[1]
try:
    pid = int(open(path, encoding="utf-8").read().strip())
    os.kill(pid, 0)
except Exception:
    print("amaru_pid=missing")
else:
    print(f"amaru_pid={pid}")
PY
elif [ "$runtime_layout" = "preview-haskell" ]; then
  python3 - "$pid_file" <<'PY'
import os, sys
path = sys.argv[1]
try:
    pid = int(open(path, encoding="utf-8").read().strip())
    os.kill(pid, 0)
except Exception:
    print("cardano_node_pid=missing")
else:
    print(f"cardano_node_pid={pid}")
PY
  [ -S "$socket_path" ] && echo "$socket_path" || echo "missing_socket=$socket_path"
else
  docker ps --filter "label=com.docker.compose.project=$project" --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}' 2>/dev/null || true
  find "$env_root/socket" -type s 2>/dev/null | sort || true
fi
echo "DOCTOR_SECTION=chain"
if [ "$runtime_layout" = "preview-amaru" ]; then
  echo "tip_query=not_applicable"
elif [ "$runtime_layout" = "preview-haskell" ]; then
  if [ -S "$socket_path" ]; then
    CARDANO_NODE_SOCKET_PATH="$socket_path" ${HOME}/.local/bin/cardano-cli query tip --testnet-magic 2 2>/dev/null || echo "tip_query=failed"
  else
    echo "tip_query=missing_socket"
  fi
else
  container="${project}-node1-1"
  if docker inspect "$container" >/dev/null 2>&1; then
    docker exec -e CARDANO_NODE_SOCKET_PATH=/env/socket/node1/sock "$container" \
      cardano-cli query tip --testnet-magic 42 2>/dev/null || echo "tip_query=failed"
  else
    echo "tip_query=missing_container"
  fi
fi
echo "DOCTOR_SECTION=logs"
if [ "$runtime_layout" = "preview-amaru" ] || [ "$runtime_layout" = "preview-haskell" ]; then
  if [ -f "$log_path" ]; then
    size=$(du -sh "$log_path" 2>/dev/null | awk '{print $1}')
    echo "log=$log_path size=$size"
    grep -n -i -E '"sev":"(Error|Critical|Alert|Emergency)"|panic|exception|rollback' "$log_path" 2>/dev/null | tail -n 80 || true
  else
    echo "log_missing=$log_path"
  fi
else
  find "$env_root/logs" -type f -maxdepth 3 2>/dev/null | sort | while read -r log; do
    size=$(du -sh "$log" 2>/dev/null | awk '{print $1}')
    echo "log=$log size=$size"
  done
  grep -R -n -i -E '"sev":"(Error|Critical|Alert|Emergency)"|panic|exception|rollback' "$env_root/logs" "$runtime/logs" 2>/dev/null | tail -n 80 || true
fi
echo "DOCTOR_SECTION=safety"
if [ "$runtime_layout" = "preview-amaru" ] || [ "$runtime_layout" = "preview-haskell" ]; then
  if printf '%s' "$listen_address" | grep -Eq '^127\.|^\[::1\]|^localhost:'; then
    echo "loopback_only=true"
  else
    echo "loopback_only=false"
    echo "$listen_address"
  fi
else
  non_loopback=$(docker ps --filter "label=com.docker.compose.project=$project" --format '{{.Ports}}' 2>/dev/null | grep -v '127\.0\.0\.1' | grep -v '^$' | grep -E -- '->' || true)
  if [ -n "$non_loopback" ]; then
    echo "loopback_only=false"
    echo "$non_loopback"
  else
    echo "loopback_only=true"
  fi
  if [ -f "$env_root/configuration.yaml" ]; then
    grep -n '"PeerSharing"\|"RequiresNetworkMagic"' "$env_root/configuration.yaml" || true
  fi
fi
"""


def logs_command(action, runtime_root, node=None, tail_lines=200):
    node_filter = _quote(node or "")
    tail_count = int(tail_lines)
    command = _header(runtime_root, f"logs-{action}") + f"""
echo "LOG_ACTION={action}"
echo "LOG_NODE={node or 'all'}"
"""
    if action == "collect":
        return command + r"""
find "$env_root/logs" "$runtime/logs" -type f \( -name '*.log' -o -name 'stdout.log' -o -name 'stderr.log' \) 2>/dev/null | sort | while read -r log; do
  size=$(du -sh "$log" 2>/dev/null | awk '{print $1}')
  mtime=$(stat -c %y "$log" 2>/dev/null || stat -f "%Sm" "$log" 2>/dev/null || true)
  echo "log=$log size=$size mtime=$mtime"
  tail -n 20 "$log" 2>/dev/null || true
done
echo "No remote state changed."
"""
    if action == "scan":
        return command + r"""
grep -R -n -i -E '"sev":"(Error|Critical|Alert|Emergency)"|panic|exception|rollback|invalid' "$env_root/logs" "$runtime/logs" 2>/dev/null | tail -n 200 || true
echo "No remote state changed."
"""
    if action == "tail":
        return command + f"""
node={node_filter}
if [ -n "$node" ]; then
  container="${{project}}-${{node}}-1"
  if docker inspect "$container" >/dev/null 2>&1; then
    docker logs --tail {tail_count} "$container" 2>&1 || true
  fi
  for log in "$env_root/logs/$node/stdout.log" "$env_root/logs/$node/stderr.log"; do
    echo "LOG=$log"
    tail -n {tail_count} "$log" 2>/dev/null || true
  done
else
  for container in $(docker ps --filter "label=com.docker.compose.project=$project" --format '{{{{.Names}}}}' 2>/dev/null); do
    echo "CONTAINER=$container"
    docker logs --tail {tail_count} "$container" 2>&1 || true
  done
fi
echo "No remote state changed."
"""
    raise ValueError(f"Unknown log action: {action}")


def component_command(component, view, runtime_root, tail_lines=200):
    component_value = _quote(component)
    tail_count = int(tail_lines)
    command = _header(runtime_root, f"component-{view}") + f"""
component={component_value}
echo "COMPONENT={component}"
echo "COMPONENT_VIEW={view}"
"""
    if component == "all" and view == "health":
        return command + r"""
for node in node1 node2 node3; do
  container="${project}-${node}-1"
  echo "NODE=$node"
  if docker inspect "$container" >/dev/null 2>&1; then
    status=$(docker inspect -f '{{.State.Status}}' "$container" 2>/dev/null || echo unknown)
    echo "container_status=$status"
  else
    echo "container_status=missing"
  fi
  [ -S "$env_root/socket/$node/sock" ] && echo "socket_status=present" || echo "socket_status=missing"
  du -sh "$env_root/node-data/$node/db" 2>/dev/null || true
done
echo "No remote state changed."
"""
    if view == "status":
        return command + r"""
node_dir="$env_root/node-data/$component"
container="${project}-${component}-1"
echo "node_dir=$node_dir"
[ -d "$node_dir" ] && echo "node_status=present" || echo "node_status=missing"
[ -S "$env_root/socket/$component/sock" ] && echo "socket_status=present" || echo "socket_status=missing"
if docker inspect "$container" >/dev/null 2>&1; then
  status=$(docker inspect -f '{{.State.Status}}' "$container" 2>/dev/null || echo unknown)
  echo "container_status=$status"
else
  echo "container_status=missing"
fi
du -sh "$node_dir/db" 2>/dev/null || true
echo "No remote state changed."
"""
    if view == "logs":
        return command + f"""
container="${{project}}-${{component}}-1"
if docker inspect "$container" >/dev/null 2>&1; then
  docker logs --tail {tail_count} "$container" 2>&1 || true
fi
for log in "$env_root/logs/$component/stdout.log" "$env_root/logs/$component/stderr.log"; do
  echo "LOG=$log"
  tail -n {tail_count} "$log" 2>/dev/null || true
done
echo "No remote state changed."
"""
    if view == "tip":
        return command + r"""
container="${project}-${component}-1"
echo "container=$container"
if docker inspect "$container" >/dev/null 2>&1; then
  docker exec -e CARDANO_NODE_SOCKET_PATH=/env/socket/$component/sock "$container" \
    cardano-cli query tip --testnet-magic 42 2>/dev/null || echo "tip_query=failed"
else
  echo "tip_query=missing_container"
fi
echo "No remote state changed."
"""
    if view == "config":
        return command + r"""
node_dir="$env_root/node-data/$component"
echo "configuration.yaml=$env_root/configuration.yaml"
grep -n '"PeerSharing"\|"RequiresNetworkMagic"\|"TraceConnectionManager"\|"TracePeerSelection"\|"TraceKeepAlive"' "$env_root/configuration.yaml" 2>/dev/null || true
echo "topology.json=$node_dir/topology.json"
cat "$node_dir/topology.json" 2>/dev/null || true
echo "No remote state changed."
"""
    raise ValueError(f"Unknown component view: {view}")
