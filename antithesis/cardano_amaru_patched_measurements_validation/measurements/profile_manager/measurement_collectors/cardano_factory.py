"""Trusted runtime bindings for exact Cardano-node measurement collectors."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterable

from profile_manager.measurement_collectors.amaru_external import (
    RestartReadinessCollector,
    SyncSpeedCollector,
    WorkloadAccountingCollector,
)
from profile_manager.measurement_collectors.cardano_resources import CardanoResourceCollector
from profile_manager.measurement_collectors.cardano_patched import (
    build_cardano_patched_factories,
)
from profile_manager.measurement_collectors.cardano_stock import (
    build_cardano_stock_factories,
    CardanoHeaderValidationCollector,
)


def build_cardano_measurement_factories(
    *,
    runtime_metadata_path: str | Path,
    target_node: str,
    trace_paths: Iterable[str | Path],
    patched_trace_paths: Iterable[str | Path] = (),
    tip_probe: Callable[[], dict[str, Any]] | None,
    expected_start_height: int | None = None,
    expected_end_height: int | None = None,
    peer_policy: str = "unspecified",
    resource_sample_interval_seconds: float = 1.0,
    resource_resolve_pid: Callable[[Path, str], int] | None = None,
    resource_sample_reader: Callable[[int, int], dict[str, Any]] | None = None,
    resource_background: bool = True,
    allow_missing_trace_sources: bool = False,
    target_identity: dict[str, Any] | None = None,
) -> dict[str, Callable[[dict[str, Any]], Any]]:
    metadata_path = Path(runtime_metadata_path)
    paths = tuple(Path(path) for path in trace_paths)
    factories = build_cardano_stock_factories(
        trace_paths=paths,
        allow_missing_at_start=allow_missing_trace_sources,
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
    def build_resource_collector(entry):
        options = dict(resource_options)
        options["sample_interval_seconds"] = float(
            (entry.get("parameters") or {}).get(
                "sample_interval_seconds", resource_sample_interval_seconds
            )
        )
        return CardanoResourceCollector(entry, **options)

    factories["cardano-stock-resources"] = build_resource_collector
    factories["cardano-stock-header-validation"] = lambda entry: CardanoHeaderValidationCollector(
        entry, trace_paths=paths, allow_missing_at_start=allow_missing_trace_sources
    )
    factories["cardano-external-workload-accounting"] = (
        lambda entry: WorkloadAccountingCollector(entry)
    )
    factories["cardano-external-restart-readiness"] = (
        lambda entry: RestartReadinessCollector(entry, target_node=target_node)
    )
    if tip_probe is not None:
        factories["cardano-external-sync-speed"] = lambda entry: SyncSpeedCollector(
            entry,
            tip_probe=tip_probe,
            expected_start_height=expected_start_height,
            expected_end_height=expected_end_height,
            peer_policy=peer_policy,
            target_node=target_node,
        )
    if target_identity is not None and target_identity.get("mode") == "patched":
        factories.update(
            build_cardano_patched_factories(
                json_trace_paths=patched_trace_paths,
                target_identity=target_identity,
            )
        )
    return factories
