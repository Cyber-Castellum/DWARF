from __future__ import annotations

from dataclasses import dataclass


DEFAULT_RUNTIME_ROOT_BASE = "${HOME}/cardano-profiles"
DEFAULT_COMPOSE_PROJECT_PREFIX = "dwarf-"
VALID_TOPOLOGY_PATTERNS = {"local-mesh"}


@dataclass(frozen=True)
class ProfileShape:
    node_type: str
    haskell_count: int
    amaru_count: int
    topology_pattern: str | None
    shared_genesis: bool
    remote_runtime_root: str
    compose_project: str


def _parse_nonnegative_int(value, *, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a non-negative integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must be a non-negative integer") from error
    if parsed < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return parsed


def _derive_node_type(*, haskell_count: int, amaru_count: int) -> str:
    if haskell_count > 0 and amaru_count > 0:
        return "mixed"
    if amaru_count > 0:
        return "amaru"
    return "cardano-node"


def shape_from_profile_dict(data: dict) -> ProfileShape:
    profile_id = data["id"]
    haskell_count = _parse_nonnegative_int(
        data.get("haskell_count", data.get("node_count", 0)),
        field="haskell_count",
    )
    amaru_count = _parse_nonnegative_int(
        data.get("amaru_count", data.get("amaru_node_count", 0)),
        field="amaru_count",
    )
    if haskell_count == 0 and amaru_count == 0:
        raise ValueError("profile must declare at least one Haskell or Amaru node")

    topology_pattern = data.get("topology_pattern")
    if topology_pattern is None and haskell_count + amaru_count > 1:
        topology_pattern = "local-mesh"
    if topology_pattern is not None and topology_pattern not in VALID_TOPOLOGY_PATTERNS:
        raise ValueError(
            f"unsupported topology_pattern {topology_pattern!r}; expected one of {sorted(VALID_TOPOLOGY_PATTERNS)}"
        )

    shared_genesis = bool(data.get("shared_genesis", haskell_count > 0))
    node_type = data.get("node_type") or _derive_node_type(haskell_count=haskell_count, amaru_count=amaru_count)
    remote_runtime_root = data.get("remote_runtime_root", f"{DEFAULT_RUNTIME_ROOT_BASE}/{profile_id}")
    compose_project = data.get("compose_project", f"{DEFAULT_COMPOSE_PROJECT_PREFIX}{profile_id}")
    return ProfileShape(
        node_type=node_type,
        haskell_count=haskell_count,
        amaru_count=amaru_count,
        topology_pattern=topology_pattern,
        shared_genesis=shared_genesis,
        remote_runtime_root=remote_runtime_root,
        compose_project=compose_project,
    )
