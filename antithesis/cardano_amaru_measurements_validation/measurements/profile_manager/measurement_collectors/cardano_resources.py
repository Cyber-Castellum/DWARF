"""Run-scoped external resource tap for the actual Cardano-node container."""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from profile_manager.measurement_collectors.amaru_resources import AmaruResourceCollector
from scripts.runtime_resource_profile import _load_runtime_node


_BYTE_VALUE = re.compile(r"^([0-9]+(?:\.[0-9]+)?)\s*([kmgtpe]?i?B)$", re.IGNORECASE)
_BYTE_FACTORS = {
    "b": 1,
    "kb": 1000,
    "mb": 1000**2,
    "gb": 1000**3,
    "tb": 1000**4,
    "pb": 1000**5,
    "eb": 1000**6,
    "kib": 1024,
    "mib": 1024**2,
    "gib": 1024**3,
    "tib": 1024**4,
    "pib": 1024**5,
    "eib": 1024**6,
}
_DOCKER_STATS_TIMEOUT_SECONDS = 15.0


def _bytes(value: str) -> int:
    match = _BYTE_VALUE.fullmatch(value.strip())
    if match is None:
        raise RuntimeError(f"Docker stats returned an invalid byte value: {value!r}")
    return int(float(match.group(1)) * _BYTE_FACTORS[match.group(2).lower()])


def _byte_pair(value: object, field: str) -> tuple[int, int]:
    parts = str(value or "").split("/", 1)
    if len(parts) != 2:
        raise RuntimeError(f"Docker stats returned an invalid {field} counter")
    return _bytes(parts[0]), _bytes(parts[1])


def _docker_container_identity(
    runtime_metadata_path: Path,
    target_node: str,
    *,
    docker_run: Callable[..., Any],
) -> dict[str, str]:
    try:
        runtime = json.loads(runtime_metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("runtime metadata is unavailable for resource sampling") from exc
    profile_id = str(runtime.get("profile_id") or "")
    if not profile_id:
        raise RuntimeError("runtime metadata has no topology identity")
    node = _load_runtime_node(runtime_metadata_path, target_node)
    container_name = str(node.get("container_name") or "")
    if not container_name:
        raise RuntimeError("runtime node has no container identity")
    inspected = docker_run(
        ["docker", "inspect", container_name],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if inspected.returncode != 0:
        raise RuntimeError("managed resource container is unavailable")
    try:
        rows = json.loads(inspected.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Docker inspect returned invalid resource identity") from exc
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise RuntimeError("Docker inspect returned ambiguous resource identity")
    item = rows[0]
    actual_name = str(item.get("Name") or "").lstrip("/")
    container_id = str(item.get("Id") or "")
    labels = ((item.get("Config") or {}).get("Labels") or {})
    if actual_name != container_name or not container_id or not isinstance(labels, dict):
        raise RuntimeError("Docker resource container identity is inconsistent")
    expected = {
        "io.dwarf.managed": "true",
        "io.dwarf.topology": profile_id,
        "io.dwarf.component": "node",
    }
    expected_node_id = target_node
    compose_project = str(runtime.get("compose_project") or "")
    expected_service = ""
    expected_implementation = ""
    actual_topology = runtime.get("actual_topology")
    exact_mapping_required = (
        runtime.get("lifecycle") == "cardano_amaru_relay_bootstrap_control"
        or (
            isinstance(actual_topology, dict)
            and "selected_target_node_services" in actual_topology
        )
    )
    if exact_mapping_required:
        if not isinstance(actual_topology, dict):
            raise RuntimeError("runtime metadata has no exact resource target mapping")
        mappings = actual_topology.get("selected_target_node_services")
        if not isinstance(mappings, dict) or not mappings:
            raise RuntimeError("runtime metadata has no exact resource target mapping")
        matched_node_ids = [
            str(node_id)
            for node_id, service in mappings.items()
            if str(service) == target_node
        ]
        if len(matched_node_ids) != 1:
            raise RuntimeError("runtime metadata has no exact resource target mapping")
        expected_node_id = matched_node_ids[0]
        expected_service = target_node
        selected_nodes = actual_topology.get("selected_nodes") or []
        selected = [
            item
            for item in selected_nodes
            if isinstance(item, dict)
            and str(item.get("id") or "") == expected_node_id
            and str(item.get("service") or "") == expected_service
        ]
        if len(selected) != 1:
            raise RuntimeError("runtime metadata has no exact resource target identity")
        expected_implementation = str(selected[0].get("implementation") or "")
        if not expected_implementation:
            raise RuntimeError("runtime metadata has no resource target implementation")
    expected["io.dwarf.node_id"] = expected_node_id
    if expected_service:
        if not compose_project:
            raise RuntimeError("runtime metadata has no Compose project identity")
        expected.update(
            {
                "io.dwarf.implementation": expected_implementation,
                "com.docker.compose.project": compose_project,
                "com.docker.compose.service": expected_service,
            }
        )
    installation_id = str(os.environ.get("DWARF_INSTALLATION_ID") or "").strip()
    if not installation_id:
        raise RuntimeError("DWARF installation identity is unavailable")
    expected["io.dwarf.installation"] = installation_id
    if any(labels.get(key) != value for key, value in expected.items()):
        raise RuntimeError("refusing to sample a container outside the managed topology")
    if str((item.get("State") or {}).get("Status") or "") != "running":
        raise RuntimeError("managed resource container is not running")
    return {
        "node": target_node,
        "container_name": container_name,
        "container_id": container_id,
    }


def _docker_container_sample(
    identity: dict[str, str],
    *,
    docker_run: Callable[..., Any],
) -> dict[str, Any]:
    result = docker_run(
        [
            "docker",
            "stats",
            "--no-stream",
            "--format",
            "{{json .}}",
            identity["container_id"],
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=_DOCKER_STATS_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise RuntimeError("Docker could not sample the managed resource container")
    lines = [line for line in str(result.stdout or "").splitlines() if line.strip()]
    if len(lines) != 1:
        raise RuntimeError("Docker stats returned ambiguous resource samples")
    try:
        row = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        raise RuntimeError("Docker stats returned an invalid resource sample") from exc
    if not isinstance(row, dict):
        raise RuntimeError("Docker stats returned a non-object resource sample")
    stats_id = str(row.get("ID") or "")
    if not stats_id or not identity["container_id"].startswith(stats_id):
        raise RuntimeError("Docker stats returned a different resource container")
    if str(row.get("Name") or "") != identity["container_name"]:
        raise RuntimeError("Docker stats returned a different resource name")
    try:
        cpu_percent = float(str(row["CPUPerc"]).strip().removesuffix("%"))
        container_tasks = int(str(row["PIDs"]).strip())
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("Docker stats omitted required CPU or task counters") from exc
    container_memory_bytes, _memory_limit = _byte_pair(row.get("MemUsage"), "memory")
    network_rx, network_tx = _byte_pair(row.get("NetIO"), "network")
    disk_read, disk_write = _byte_pair(row.get("BlockIO"), "block I/O")
    return {
        "container_id": identity["container_id"],
        "container_name": identity["container_name"],
        "monotonic_seconds": time.monotonic(),
        "ts_epoch_s": time.time(),
        "cpu_time_seconds": None,
        "cpu_percent": cpu_percent,
        "rss_bytes": None,
        "container_memory_bytes": container_memory_bytes,
        "network_rx_bytes": network_rx,
        "network_tx_bytes": network_tx,
        "network_scope": "container-network-namespace",
        "disk_read_bytes": disk_read,
        "disk_write_bytes": disk_write,
        "fd_count": None,
        "threads": None,
        "container_tasks": container_tasks,
    }


class ManagedContainerResourceCollector(AmaruResourceCollector):
    """Sample an exact managed node container without sharing host PIDs."""

    def __init__(
        self,
        *args,
        docker_run: Callable[..., Any] = subprocess.run,
        **kwargs,
    ) -> None:
        self._docker_run = docker_run
        self._container_identity: dict[str, str] | None = None
        self._prefer_container = (
            kwargs.get("resolve_pid") is None and kwargs.get("sample_reader") is None
        )
        kwargs.setdefault(
            "capture_timeout_seconds", _DOCKER_STATS_TIMEOUT_SECONDS
        )
        super().__init__(*args, **kwargs)

    def prepare(self, context) -> None:
        if self._prefer_container:
            node = _load_runtime_node(self.runtime_metadata_path, self.target_node)
            if node.get("container_name"):
                if not self.runtime_metadata_path.is_file():
                    raise FileNotFoundError(
                        f"runtime metadata is unavailable: {self.runtime_metadata_path}"
                    )
                context.collector_dir.mkdir(parents=True, exist_ok=True)
                self._container_identity = _docker_container_identity(
                    self.runtime_metadata_path,
                    self.target_node,
                    docker_run=self._docker_run,
                )
                return
        super().prepare(context)

    def _read_sample(self, index: int) -> dict[str, Any]:
        if self._container_identity is None:
            return super()._read_sample(index)
        sample = _docker_container_sample(
            self._container_identity,
            docker_run=self._docker_run,
        )
        return {**sample, "sample_index": index, "target_node": self.target_node}

    def finalize(self, context) -> dict[str, Any]:
        result = super().finalize(context)
        if self._container_identity is not None:
            result["target"] = dict(self._container_identity)
            result["source"]["kind"] = "docker-container-cgroup"
            result["source"]["scope"] = "container"
            context.write_json("result.json", result)
        return result


class CardanoResourceCollector(ManagedContainerResourceCollector):
    """Sample the exact managed Cardano container without sharing host PIDs."""
