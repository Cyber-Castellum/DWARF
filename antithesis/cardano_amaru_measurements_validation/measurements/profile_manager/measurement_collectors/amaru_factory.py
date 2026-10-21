"""Trusted runtime bindings for the version-pinned Amaru collectors."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterable

from profile_manager.measurement_collectors.amaru_external import (
    RestartReadinessCollector,
    SyncSpeedCollector,
    WorkloadAccountingCollector,
)
from profile_manager.measurement_collectors.amaru_resources import AmaruResourceCollector
from profile_manager.measurement_collectors.cardano_resources import (
    ManagedContainerResourceCollector,
)
from profile_manager.measurement_collectors.amaru_patched import (
    build_amaru_patched_factories,
)
from profile_manager.measurement_collectors.amaru_stock import (
    build_amaru_stock_factories,
    AmaruHeaderValidationCollector,
)


def build_amaru_measurement_factories(
    *,
    runtime_metadata_path: str | Path,
    target_node: str,
    json_trace_paths: Iterable[str | Path],
    otlp_trace_paths: Iterable[str | Path],
    tip_probe: Callable[[], dict[str, Any]] | None,
    expected_start_height: int | None = None,
    expected_end_height: int | None = None,
    peer_policy: str = "unspecified",
    resource_sample_interval_seconds: float = 1.0,
    resource_resolve_pid: Callable[[Path, str], int] | None = None,
    resource_sample_reader: Callable[[int, int], dict[str, Any]] | None = None,
    resource_docker_run: Callable[..., Any] | None = None,
    resource_background: bool = True,
    target_identity: dict[str, Any] | None = None,
    allow_missing_trace_sources: bool = False,
) -> dict[str, Callable[[dict[str, Any]], Any]]:
    """Bind deployment-owned paths and probes; scenario parameters cannot replace them."""
    metadata_path = Path(runtime_metadata_path)
    json_paths = tuple(Path(path) for path in json_trace_paths)
    otlp_paths = tuple(Path(path) for path in otlp_trace_paths)
    stock_options: dict[str, Any] = {}
    if target_identity is not None:
        revision = target_identity.get("source_revision")
        stock_options["source_revision"] = str(revision) if revision is not None else None
    factories = build_amaru_stock_factories(
        json_trace_paths=json_paths,
        otlp_trace_paths=otlp_paths,
        allow_missing_at_start=allow_missing_trace_sources,
        **stock_options,
    )
    if target_identity is not None and target_identity.get("mode") == "patched":
        factories.update(
            build_amaru_patched_factories(
                json_trace_paths=json_paths,
                target_identity=target_identity,
                allow_missing_at_start=allow_missing_trace_sources,
            )
        )

    resource_options: dict[str, Any] = {
        "runtime_metadata_path": metadata_path,
        "target_node": target_node,
        "sample_interval_seconds": resource_sample_interval_seconds,
        "sample_reader": resource_sample_reader,
        "background": resource_background,
    }
    if resource_resolve_pid is not None:
        resource_options["resolve_pid"] = resource_resolve_pid
    if resource_docker_run is not None:
        resource_options["docker_run"] = resource_docker_run

    def build_resource_collector(entry):
        options = dict(resource_options)
        options["sample_interval_seconds"] = float(
            (entry.get("parameters") or {}).get(
                "sample_interval_seconds", resource_sample_interval_seconds
            )
        )
        return ManagedContainerResourceCollector(entry, **options)

    factories["amaru-stock-resources"] = build_resource_collector
    factories["amaru-stock-header-validation"] = lambda entry: AmaruHeaderValidationCollector(
        entry, trace_paths=json_paths, allow_missing_at_start=allow_missing_trace_sources,
        **stock_options,
    )
    factories["amaru-external-workload-accounting"] = (
        lambda entry: WorkloadAccountingCollector(entry)
    )
    factories["amaru-external-restart-readiness"] = (
        lambda entry: RestartReadinessCollector(entry, target_node=target_node)
    )
    if tip_probe is not None:
        factories["amaru-external-sync-speed"] = lambda entry: SyncSpeedCollector(
            entry,
            tip_probe=tip_probe,
            expected_start_height=expected_start_height,
            expected_end_height=expected_end_height,
            peer_policy=peer_policy,
            target_node=target_node,
        )
    return factories
