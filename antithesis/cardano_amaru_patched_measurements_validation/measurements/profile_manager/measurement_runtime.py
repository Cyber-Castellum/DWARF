"""Isolated lifecycle manager for optional, version-pinned measurements."""
from __future__ import annotations

import json
import math
import operator
import os
import platform
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping

from jsonschema import Draft202012Validator

from profile_manager.run_ledger import atomic_write_bytes


_RESULT_SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "spec" / "v1" / "measurement-result.schema.json"
)
_TARGET_RESULT_FIELDS = {
    "implementation",
    "version",
    "source_revision",
    "mode",
    "image_digest",
    "image_reference",
    "image_manifest_digest",
    "image_config_digest",
    "qualification_image_id",
    "source_image_reference",
    "transfer_manifest_sha256",
    "transfer_source_installation_id",
    "executable_digest",
    "build_result_sha256",
    "runtime_probe_log_sha256",
    "patch_set_sha256",
    "measurement_revision",
    "version_catalog_revision",
    "profile_id",
    "deployment_adapter",
    "qualification_status",
    "coverage_harness_sha256",
    "target_name",
    "engine",
    "performance_authority",
    "non_authoritative_performance",
    "schema_version",
    "coverage_target_ref",
    "working_dir",
    "fuzz_dir",
    "binary",
    "created_at",
    "identity_kind",
    "image_config_id",
    "platform",
    "operator_registration_id",
    "operator_overlay_revision",
    "controller_installation_id",
}


class MeasurementArtifactPathError(ValueError):
    """Raised when a collector attempts to write outside its evidence root."""


@dataclass(frozen=True)
class CollectorContext:
    measurement_id: str
    definition: dict[str, Any]
    parameters: dict[str, Any]
    run_dir: Path
    collector_dir: Path

    def artifact_path(self, relative_path: str) -> Path:
        if not isinstance(relative_path, str) or not relative_path:
            raise MeasurementArtifactPathError("collector artifact path is required")
        posix = PurePosixPath(relative_path)
        if posix.is_absolute() or ".." in posix.parts or "\\" in relative_path:
            raise MeasurementArtifactPathError(
                f"collector artifact path escapes its evidence root: {relative_path}"
            )
        destination = self.collector_dir.joinpath(*posix.parts)
        try:
            destination.resolve().relative_to(self.collector_dir.resolve())
        except ValueError as exc:
            raise MeasurementArtifactPathError(
                f"collector artifact path escapes its evidence root: {relative_path}"
            ) from exc
        return destination

    def write_json(self, relative_path: str, payload: Any) -> str:
        destination = self.artifact_path(relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return destination.relative_to(self.run_dir).as_posix()


@dataclass
class _CollectorRecord:
    entry: dict[str, Any]
    collector: Any
    context: CollectorContext
    state: str = "selected"
    start_attempted: bool = False


class MeasurementRuntime:
    """Run selected measurement taps without changing scenario semantics by default."""

    def __init__(
        self,
        *,
        run_dir: str | Path,
        resolution: dict[str, Any],
        collector_factories: Mapping[str, Callable[[dict[str, Any]], Any]],
        scenario_id: str | None = None,
        wall_clock: Callable[[], float] = time.time,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.run_dir = Path(run_dir)
        self.measurements_dir = self.run_dir / "measurements"
        self.collectors_dir = self.measurements_dir / "collectors"
        self._resolution = json.loads(json.dumps(resolution))
        self._scenario_id = scenario_id
        self._factories = dict(collector_factories)
        self._wall_clock = wall_clock
        self._monotonic_clock = monotonic_clock
        self._run_started_mono: float | None = None
        self._records: dict[str, _CollectorRecord] = {}
        self._errors: list[dict[str, Any]] = []
        self._results: dict[str, dict[str, Any]] = {}
        self._threshold_gates: dict[str, dict[str, Any]] = {}
        self._prepared = False
        self._started = False
        self._finalized: dict[str, Any] | None = None
        self._duration_seconds: float | None = None

        for entry in self._resolution.get("resolved", []):
            measurement_id = entry["id"]
            factory = self._factories.get(measurement_id)
            if factory is None:
                self._errors.append(
                    {
                        "measurement_id": measurement_id,
                        "phase": "construct",
                        "type": "CollectorUnavailable",
                        "message": "no collector factory is registered",
                    }
                )
                continue
            collector_dir = self.collectors_dir / measurement_id
            context = CollectorContext(
                measurement_id=measurement_id,
                definition=dict(entry.get("definition") or {}),
                parameters=dict(entry.get("parameters") or {}),
                run_dir=self.run_dir,
                collector_dir=collector_dir,
            )
            try:
                collector = factory(entry)
            except Exception as exc:  # noqa: BLE001 - optional tap isolation
                self._errors.append(self._error(measurement_id, "construct", exc))
                continue
            self._records[measurement_id] = _CollectorRecord(
                entry=entry, collector=collector, context=context
            )

    @staticmethod
    def _error(measurement_id: str, phase: str, exc: Exception) -> dict[str, Any]:
        return {
            "measurement_id": measurement_id,
            "phase": phase,
            "type": type(exc).__name__,
            "message": str(exc).replace("\n", " ")[:512],
        }

    def _record_error(self, record: _CollectorRecord, phase: str, exc: Exception) -> None:
        record.state = "error"
        self._errors.append(self._error(record.entry["id"], phase, exc))

    def _write_marker(
        self, phase_id: str, state: str, *, phase_index: int | None = None
    ) -> dict[str, Any]:
        now_wall = float(self._wall_clock())
        now_mono = float(self._monotonic_clock())
        if self._run_started_mono is None:
            self._run_started_mono = now_mono
        marker = {
            "phase_id": phase_id,
            "state": state,
            "epoch_seconds": now_wall,
            "elapsed_seconds": round(now_mono - self._run_started_mono, 9),
        }
        if phase_index is not None:
            marker["phase_index"] = phase_index
        self.measurements_dir.mkdir(parents=True, exist_ok=True)
        with (self.measurements_dir / "windows.ndjson").open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(marker, sort_keys=True) + "\n")
        return marker

    def elapsed_seconds(self) -> float:
        """Return run-relative elapsed time from the measurement monotonic clock."""
        now_mono = float(self._monotonic_clock())
        if self._run_started_mono is None:
            self._run_started_mono = now_mono
        return round(now_mono - self._run_started_mono, 9)

    def prepare(self) -> None:
        if self._prepared:
            return
        self.collectors_dir.mkdir(parents=True, exist_ok=True)
        self._write_marker("run", "start")
        for record in self._records.values():
            record.context.collector_dir.mkdir(parents=True, exist_ok=True)
            try:
                record.collector.prepare(record.context)
                record.state = "prepared"
            except Exception as exc:  # noqa: BLE001 - optional tap isolation
                self._record_error(record, "prepare", exc)
        self._prepared = True

    def start(self) -> None:
        if self._started:
            return
        if not self._prepared:
            self.prepare()
        for record in self._records.values():
            if record.state != "prepared":
                continue
            record.start_attempted = True
            try:
                record.collector.start(record.context)
                record.state = "started"
            except Exception as exc:  # noqa: BLE001 - optional tap isolation
                self._record_error(record, "start", exc)
        self._started = True

    def mark_phase(
        self, phase_id: str, state: str, *, phase_index: int | None = None
    ) -> dict[str, Any]:
        if state not in {"start", "end"}:
            raise ValueError("measurement phase marker state must be start or end")
        marker = self._write_marker(phase_id, state, phase_index=phase_index)
        for record in self._records.values():
            if record.state != "started":
                continue
            try:
                record.collector.on_marker(marker, record.context)
            except Exception as exc:  # noqa: BLE001 - optional tap isolation
                self._record_error(record, "marker", exc)
        return marker

    def _stop(self) -> None:
        if not self._started:
            return
        marker = self._write_marker("run", "end")
        self._duration_seconds = marker["elapsed_seconds"]
        for record in self._records.values():
            if record.state == "started":
                try:
                    record.collector.on_marker(marker, record.context)
                except Exception as exc:  # noqa: BLE001
                    self._record_error(record, "marker", exc)
            if not record.start_attempted:
                continue
            try:
                record.collector.stop(record.context)
                if record.state != "error":
                    record.state = "stopped"
            except Exception as exc:  # noqa: BLE001 - optional tap isolation
                self._record_error(record, "stop", exc)
        self._started = False

    @staticmethod
    def _metric_map(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
        mapped = {}
        for metric in result.get("metrics") or []:
            if isinstance(metric, dict) and isinstance(metric.get("name"), str):
                mapped[metric["name"]] = metric
        measurements = result.get("measurements")
        if isinstance(measurements, dict):
            for name, metric in measurements.items():
                if isinstance(name, str) and isinstance(metric, dict):
                    mapped[name] = {"name": name, **metric}
        return mapped

    @staticmethod
    def _evaluate_threshold(actual: Any, op: str, expected: Any) -> bool:
        operations = {
            "gte": operator.ge,
            "gt": operator.gt,
            "lte": operator.le,
            "lt": operator.lt,
            "eq": operator.eq,
        }
        if op not in operations:
            return False
        if isinstance(actual, float) and not math.isfinite(actual):
            return False
        try:
            return bool(operations[op](actual, expected))
        except (TypeError, ValueError):
            return False

    def _evaluate_gate(
        self, record: _CollectorRecord, result: dict[str, Any]
    ) -> dict[str, Any]:
        gate = record.entry.get("threshold_gate") or {
            "enabled": False,
            "thresholds": [],
        }
        if not gate.get("enabled"):
            return {"enabled": False, "result": "not-gated", "checks": []}
        metrics = self._metric_map(result)
        checks = []
        for threshold in gate.get("thresholds") or []:
            name = threshold.get("metric")
            metric = metrics.get(name)
            actual = metric.get("value") if metric is not None else None
            unit = metric.get("unit") if metric is not None else None
            expected_unit = threshold.get("unit")
            passed = (
                metric is not None
                and (expected_unit is None or unit == expected_unit)
                and self._evaluate_threshold(
                    actual, threshold.get("operator"), threshold.get("value")
                )
            )
            checks.append(
                {
                    "metric": name,
                    "operator": threshold.get("operator"),
                    "expected": threshold.get("value"),
                    "unit": expected_unit,
                    "actual": actual,
                    "actual_unit": unit,
                    "result": "pass" if passed else "fail",
                }
            )
        return {
            "enabled": True,
            "result": "pass" if checks and all(c["result"] == "pass" for c in checks) else "fail",
            "checks": checks,
        }

    def _tap_result_destination(
        self, entry: dict[str, Any], claimed: set[str]
    ) -> tuple[str, Path]:
        measurement_id = entry["id"]
        declared = (entry.get("definition") or {}).get("emitted_artifacts") or []
        result_paths: list[str] = []
        for value in declared:
            if not isinstance(value, str) or not value:
                raise MeasurementArtifactPathError(
                    f"{measurement_id} emitted artifact path is required"
                )
            posix = PurePosixPath(value)
            if posix.is_absolute() or ".." in posix.parts or "\\" in value:
                raise MeasurementArtifactPathError(
                    f"{measurement_id} emitted artifact escapes the run bundle: {value}"
                )
            if tuple(posix.parts[:2]) != ("measurements", measurement_id):
                owner = posix.parts[1] if len(posix.parts) > 1 else "unknown"
                raise MeasurementArtifactPathError(
                    f"{measurement_id} emitted artifact belongs to {owner}: {value}"
                )
            if posix.name == "result.json":
                result_paths.append(value)
        if len(result_paths) != 1:
            raise MeasurementArtifactPathError(
                f"{measurement_id} must declare exactly one contained result.json"
            )
        relative_path = result_paths[0]
        if relative_path in claimed:
            raise MeasurementArtifactPathError(
                f"duplicate per-tap result artifact: {relative_path}"
            )
        claimed.add(relative_path)
        destination = self.run_dir.joinpath(*PurePosixPath(relative_path).parts)
        current = self.run_dir
        for part in PurePosixPath(relative_path).parts:
            current = current / part
            if current.is_symlink():
                raise MeasurementArtifactPathError(
                    f"{measurement_id} result path contains a symlink: {relative_path}"
                )
        if destination.exists():
            raise MeasurementArtifactPathError(
                f"{measurement_id} result artifact already exists: {relative_path}"
            )
        try:
            destination.resolve().relative_to(self.run_dir.resolve())
        except ValueError as exc:
            raise MeasurementArtifactPathError(
                f"{measurement_id} result path escapes the run bundle: {relative_path}"
            ) from exc
        return relative_path, destination

    @staticmethod
    def _result_records(result: dict[str, Any]) -> list[dict[str, Any]]:
        measurements = result.get("measurements")
        if not isinstance(measurements, dict):
            return []
        return [
            {"name": name, **value}
            for name, value in measurements.items()
            if isinstance(name, str) and isinstance(value, dict)
        ]

    def _result_artifacts(
        self,
        *,
        record: _CollectorRecord | None,
        result: dict[str, Any],
        result_path: str,
    ) -> list[str]:
        if record is None:
            return [result_path]
        candidates: set[str] = set()
        declared = result.get("artifacts")
        if isinstance(declared, dict):
            declared_values = list(declared.values())
        elif isinstance(declared, list):
            declared_values = list(declared)
        elif declared is None:
            declared_values = []
        else:
            raise MeasurementArtifactPathError(
                f"{record.entry['id']} collector artifacts must be an object or list"
            )
        for value in declared_values:
            if not isinstance(value, str) or not value:
                raise MeasurementArtifactPathError(
                    f"{record.entry['id']} collector artifact path is required"
                )
            candidates.add(value)
        if record.context.collector_dir.is_dir():
            for path in record.context.collector_dir.rglob("*"):
                if path.is_symlink():
                    raise MeasurementArtifactPathError(
                        f"{record.entry['id']} collector artifact contains a symlink: "
                        f"{path.relative_to(self.run_dir).as_posix()}"
                    )
                if path.is_file():
                    candidates.add(path.relative_to(self.run_dir).as_posix())
        retained = []
        collector_root = record.context.collector_dir
        for value in sorted(candidates):
            posix = PurePosixPath(value)
            if posix.is_absolute() or ".." in posix.parts or "\\" in value:
                raise MeasurementArtifactPathError(
                    f"{record.entry['id']} collector artifact escapes its evidence root: {value}"
                )
            path = self.run_dir.joinpath(*posix.parts)
            try:
                path.relative_to(collector_root)
                path.resolve().relative_to(collector_root.resolve())
            except ValueError as exc:
                raise MeasurementArtifactPathError(
                    f"{record.entry['id']} collector artifact belongs outside its evidence root: {value}"
                ) from exc
            current = self.run_dir
            for part in posix.parts:
                current = current / part
                if current.is_symlink():
                    raise MeasurementArtifactPathError(
                        f"{record.entry['id']} collector artifact contains a symlink: {value}"
                    )
            if not path.is_file():
                raise MeasurementArtifactPathError(
                    f"{record.entry['id']} collector artifact is missing: {value}"
                )
            retained.append(value)
        return sorted({result_path, *retained})

    @staticmethod
    def _metric_sample_count(record: dict[str, Any]) -> int | None:
        candidates = [record.get("sample_count")]
        if isinstance(record.get("value"), dict):
            candidates.append(record["value"].get("sample_count"))
        for value in candidates:
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                return value
        return None

    @classmethod
    def _metric_is_available(cls, record: dict[str, Any]) -> bool:
        status = record.get("status")
        if isinstance(status, str):
            return status in {"available", "collected"}
        count = cls._metric_sample_count(record)
        return (count is not None and count > 0) or record.get("value") is not None

    def _write_per_tap_result(
        self,
        *,
        entry: dict[str, Any],
        target: dict[str, Any],
        validator: Draft202012Validator,
        hardware: Any,
        claimed: set[str],
    ) -> str:
        measurement_id = entry["id"]
        compatibility = (entry.get("definition") or {}).get("compatibility") or {}
        declared_implementation = compatibility.get("implementation")
        resolved_implementation = target.get("implementation")
        if (
            declared_implementation is not None
            and declared_implementation != resolved_implementation
        ):
            raise ValueError(
                f"measurement {measurement_id} targets {declared_implementation}, "
                f"but resolved target is {resolved_implementation}"
            )
        relative_path, destination = self._tap_result_destination(entry, claimed)
        collector_result = self._results.get(measurement_id) or {}
        record = self._records.get(measurement_id)
        records = self._result_records(collector_result)
        errors = [
            item
            for item in self._errors
            if item.get("measurement_id") == measurement_id
        ]
        available = [item for item in records if self._metric_is_available(item)]
        if errors:
            status = "failed"
        elif available:
            status = "collected"
        else:
            status = "unavailable"
        sample_counts = [
            value
            for value in (self._metric_sample_count(item) for item in available)
            if value is not None
        ]
        reasons = []
        for item in records:
            reason = item.get("reason") or item.get("unavailable_reason")
            if isinstance(reason, str) and reason and reason not in reasons:
                reasons.append(reason)
        for item in errors:
            reason = item.get("message")
            if isinstance(reason, str) and reason and reason not in reasons:
                reasons.append(reason)
        if status != "collected" and not reasons:
            reasons.append("collector produced no available measurement evidence")
        definition = entry.get("definition") or {}
        collector_mode = str(definition.get("collection_mode") or "external")
        result = {
            "schema_version": "v1",
            "measurement_id": measurement_id,
            "run_id": self.run_dir.name,
            "status": status,
            "result_kind": (
                "coverage"
                if collector_mode == "compiler-coverage"
                else "resource"
                if measurement_id.endswith("resources")
                else "summary"
            ),
            "sample_count": max(sample_counts, default=0),
            "records": records,
            "target": target,
            "provenance": {
                "scenario_id": self._scenario_id or "unknown",
                "collector_mode": collector_mode,
                "experimental_consent": bool(entry.get("experimental_consent")),
                "experimental_context_digest": (
                    (self._resolution.get("experimental_consent") or {}).get(
                        "context_digest"
                    )
                    if entry.get("experimental_consent")
                    else None
                ),
                "compatibility": entry.get("compatibility"),
                "capability": entry.get("capability"),
                "hardware": (
                    collector_result.get("hardware")
                    if isinstance(collector_result.get("hardware"), dict)
                    else {
                        "system": hardware.system,
                        "kernel_release": hardware.release,
                        "architecture": hardware.machine,
                        "logical_cpu_count": os.cpu_count(),
                    }
                ),
                "dataset": (
                    collector_result.get("dataset")
                    if isinstance(collector_result.get("dataset"), dict)
                    else {
                        "definition_digest": entry.get("definition_digest"),
                        "profile_id": (self._resolution.get("profile") or {}).get("id"),
                    }
                ),
                "observer_overhead": collector_result.get("observer_overhead"),
            },
            "correlation": {
                "definition_digest": entry.get("definition_digest"),
                "profile_id": (self._resolution.get("profile") or {}).get("id"),
            },
            "artifacts": self._result_artifacts(
                record=record,
                result=collector_result,
                result_path=relative_path,
            ),
            "warnings": (
                [
                    "No per-metric sample count was reported; collected status derives from available scalar evidence."
                ]
                if status == "collected" and not sample_counts
                else []
            ),
        }
        if status != "collected":
            result["unavailable_reason"] = "; ".join(reasons)
        validator.validate(result)
        destination.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(
            destination,
            (json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode(
                "utf-8"
            ),
        )
        return relative_path

    def _record_result_artifact_error(self, measurement_id: str, exc: Exception) -> None:
        record = self._records.get(measurement_id)
        if record is not None:
            record.state = "error"
        self._errors.append(
            {
                "measurement_id": measurement_id,
                "phase": "result-artifact",
                "type": type(exc).__name__,
                "message": str(exc).replace("\n", " ")[:512],
            }
        )
        gate = self._threshold_gates.get(measurement_id)
        if gate and gate.get("enabled"):
            gate["result"] = "fail"

    def _write_per_tap_results(self) -> dict[str, str]:
        schema = json.loads(_RESULT_SCHEMA_PATH.read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)
        target = {
            key: value
            for key, value in (self._resolution.get("target_identity") or {}).items()
            if key in _TARGET_RESULT_FIELDS
            and (value is not None or key == "source_revision")
        }
        hardware = platform.uname()
        claimed: set[str] = set()
        written: dict[str, str] = {}
        for entry in self._resolution.get("resolved", []):
            measurement_id = entry["id"]
            try:
                written[measurement_id] = self._write_per_tap_result(
                    entry=entry,
                    target=target,
                    validator=validator,
                    hardware=hardware,
                    claimed=claimed,
                )
            except Exception as exc:  # noqa: BLE001 - optional tap isolation
                self._record_result_artifact_error(measurement_id, exc)
        return written

    def finalize(self) -> dict[str, Any]:
        if self._finalized is not None:
            return json.loads(json.dumps(self._finalized))
        self._stop()
        for measurement_id, record in self._records.items():
            try:
                result = record.collector.finalize(record.context) or {}
                if not isinstance(result, dict):
                    raise TypeError("collector finalize result must be an object")
                json.dumps(result, allow_nan=False)
                self._results[measurement_id] = result
                if record.state != "error":
                    record.state = "finalized"
            except Exception as exc:  # noqa: BLE001 - optional tap isolation
                self._record_error(record, "finalize", exc)
                result = {}
            self._threshold_gates[measurement_id] = self._evaluate_gate(record, result)
        for entry in self._resolution.get("resolved", []):
            measurement_id = entry["id"]
            if measurement_id in self._threshold_gates:
                continue
            gate = entry.get("threshold_gate") or {
                "enabled": False,
                "thresholds": [],
            }
            if not gate.get("enabled"):
                self._threshold_gates[measurement_id] = {
                    "enabled": False,
                    "result": "not-gated",
                    "checks": [],
                }
                continue
            self._threshold_gates[measurement_id] = {
                "enabled": True,
                "result": "fail",
                "checks": [
                    {
                        "metric": threshold.get("metric"),
                        "operator": threshold.get("operator"),
                        "expected": threshold.get("value"),
                        "unit": threshold.get("unit"),
                        "actual": None,
                        "actual_unit": None,
                        "result": "fail",
                    }
                    for threshold in gate.get("thresholds") or []
                ],
            }
        from profile_manager.measurement_report import write_measurement_reports

        measurements: dict[str, Any] = {}
        for measurement_id, collector_result in self._results.items():
            for name, summary in (collector_result.get("measurements") or {}).items():
                key = name if name not in measurements else f"{measurement_id}.{name}"
                measurements[key] = summary
        report = {
            "schema_version": "v1",
            "scenario": self._scenario_id,
            "duration_seconds": self._duration_seconds,
            "target_identity": self._resolution.get("target_identity"),
            "measurements": measurements,
        }
        report_artifacts = write_measurement_reports(self.run_dir, report)
        result_artifacts = self._write_per_tap_results()
        snapshot = self.snapshot()
        snapshot["report_artifacts"] = report_artifacts
        snapshot["result_artifacts"] = result_artifacts
        snapshot["gate_failed"] = any(
            gate["enabled"] and gate["result"] == "fail"
            for gate in self._threshold_gates.values()
        )
        (self.measurements_dir / "runtime.json").write_text(
            json.dumps(snapshot, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        self._finalized = snapshot
        return json.loads(json.dumps(snapshot))

    def snapshot(self) -> dict[str, Any]:
        states = {
            entry["id"]: (
                self._records[entry["id"]].state
                if entry["id"] in self._records
                else "error"
            )
            for entry in self._resolution.get("resolved", [])
        }
        return {
            "resolution": json.loads(json.dumps(self._resolution)),
            "collector_states": states,
            "collector_errors": json.loads(json.dumps(self._errors)),
            "collector_results": json.loads(json.dumps(self._results)),
            "threshold_gates": json.loads(json.dumps(self._threshold_gates)),
            "gate_failed": False,
        }

    def scenario_exit_status(self, scenario_status: str) -> str:
        """Compatibility API: measurements do not rewrite operational outcome."""
        return scenario_status

    def scenario_verdict(self, scenario_status: str, assertions: Mapping[str, Any]) -> str:
        """Evaluate this runtime's finalized evidence through the shared contract."""
        from profile_manager.scenario_verdict import evaluate_scenario_verdict

        result = self._finalized or self.snapshot()
        verdict = evaluate_scenario_verdict(
            exit_status=scenario_status,
            assertions=assertions,
            measurements=result,
            run_kind="scenario",
        ).security_verdict
        assert verdict is not None
        return verdict
