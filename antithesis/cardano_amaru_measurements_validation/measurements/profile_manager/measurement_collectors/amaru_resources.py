"""Run-scoped resource measurements for the actual Amaru process."""
from __future__ import annotations

import json
import math
import threading
from pathlib import Path
from typing import Any, Callable

from profile_manager.measurement_report import distribution_summary
from scripts.runtime_resource_profile import collect_samples, resolve_target_pid


def _distribution(samples: list[dict[str, Any]], field: str, unit: str) -> dict[str, Any]:
    return distribution_summary(
        [
            {"value": sample.get(field), "unit": unit}
            for sample in samples
            if sample.get(field) is not None
        ],
        unit=unit,
    )


def _counter_delta(
    samples: list[dict[str, Any]], field: str, unit: str, *, scope: str | None = None
) -> dict[str, Any]:
    points = [
        (sample.get("monotonic_seconds"), sample.get(field))
        for sample in samples
        if sample.get("monotonic_seconds") is not None and sample.get(field) is not None
    ]
    if len(points) < 2:
        return {
            "status": "unavailable",
            "unit": unit,
            "start": None,
            "end": None,
            "delta": None,
            "rate_per_second": None,
            **({"scope": scope} if scope else {}),
            "reason": "at least two monotonic counter samples are required",
        }
    start_time, start = points[0]
    end_time, end = points[-1]
    duration = end_time - start_time
    delta = end - start
    valid = duration > 0 and delta >= 0
    return {
        "status": "available" if valid else "unavailable",
        "unit": unit,
        "start": start,
        "end": end,
        "delta": delta if valid else None,
        "duration_seconds": duration,
        "rate_per_second": delta / duration if valid else None,
        **({"scope": scope} if scope else {}),
        **({} if valid else {"reason": "counter reset or non-positive sample duration"}),
    }


def _cpu_percent_samples(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    values = []
    previous = None
    for sample in samples:
        point = (sample.get("monotonic_seconds"), sample.get("cpu_time_seconds"))
        if point[0] is None or point[1] is None:
            continue
        if previous is not None:
            elapsed = point[0] - previous[0]
            cpu_delta = point[1] - previous[1]
            if elapsed > 0 and cpu_delta >= 0:
                values.append({"value": cpu_delta / elapsed * 100, "unit": "%"})
        previous = point
    if values:
        return values
    return [
        {"value": sample["cpu_percent"], "unit": "%"}
        for sample in samples
        if isinstance(sample.get("cpu_percent"), (int, float))
        and not isinstance(sample.get("cpu_percent"), bool)
    ]


def _network_scope(samples: list[dict[str, Any]]) -> str:
    scopes = sorted(
        {
            str(sample["network_scope"])
            for sample in samples
            if sample.get("network_scope") not in {None, "unavailable"}
        }
    )
    return scopes[0] if len(scopes) == 1 else ("mixed" if scopes else "unavailable")


def _resource_measurements(samples: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    network_scope = _network_scope(samples)
    measurements = {
        "cpu_percent": distribution_summary(_cpu_percent_samples(samples), unit="%"),
        "cpu_time_seconds": _counter_delta(samples, "cpu_time_seconds", "s"),
        "rss_bytes": _distribution(samples, "rss_bytes", "bytes"),
        "network_rx_bytes": _counter_delta(
            samples, "network_rx_bytes", "bytes", scope=network_scope
        ),
        "network_tx_bytes": _counter_delta(
            samples, "network_tx_bytes", "bytes", scope=network_scope
        ),
        "disk_read_bytes": _counter_delta(samples, "disk_read_bytes", "bytes"),
        "disk_write_bytes": _counter_delta(samples, "disk_write_bytes", "bytes"),
        "fd_count": _distribution(samples, "fd_count", "fds"),
        "threads": _distribution(samples, "threads", "threads"),
    }
    if any("container_memory_bytes" in sample for sample in samples):
        measurements["container_memory_bytes"] = _distribution(
            samples, "container_memory_bytes", "bytes"
        )
    if any("container_tasks" in sample for sample in samples):
        measurements["container_tasks"] = _distribution(
            samples, "container_tasks", "tasks"
        )
    return measurements


_LEGACY_RESOURCE_WINDOW_NAMES = ("baseline", "hostile", "recovery")
_CONTROLLED_RESOURCE_WINDOW_NAMES = (
    "controlled-chain-progress",
    "controlled-sync-range",
    "restart-recovery",
)
_RESOURCE_WINDOW_NAMES = _LEGACY_RESOURCE_WINDOW_NAMES + _CONTROLLED_RESOURCE_WINDOW_NAMES


def _read_resource_windows(path: Path) -> dict[str, tuple[float, float]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"measurement window markers are unavailable: {path}") from exc

    markers = {
        name: {"start": [], "end": []} for name in _RESOURCE_WINDOW_NAMES
    }
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            marker = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"malformed measurement window marker at line {line_number}"
            ) from exc
        if not isinstance(marker, dict):
            raise ValueError(f"malformed measurement window marker at line {line_number}")
        name = marker.get("phase_id")
        if name not in markers:
            continue
        state = marker.get("state")
        epoch = marker.get("epoch_seconds")
        if (
            state not in {"start", "end"}
            or not isinstance(epoch, (int, float))
            or not math.isfinite(float(epoch))
        ):
            raise ValueError(
                f"malformed {name} measurement window marker at line {line_number}"
            )
        markers[name][state].append(float(epoch))

    observed = tuple(
        name
        for name in _RESOURCE_WINDOW_NAMES
        if markers[name]["start"] or markers[name]["end"]
    )
    controlled = tuple(name for name in observed if name in _CONTROLLED_RESOURCE_WINDOW_NAMES)
    required_names = controlled or _LEGACY_RESOURCE_WINDOW_NAMES
    windows = {}
    for name in required_names:
        starts = markers[name]["start"]
        ends = markers[name]["end"]
        if len(starts) != 1 or len(ends) != 1:
            raise ValueError(
                f"{name} measurement window requires exactly one start and one end marker"
            )
        start, end = starts[0], ends[0]
        if end < start:
            raise ValueError(f"{name} measurement window is reversed")
        windows[name] = (start, end)

    ordered = sorted((start, end, name) for name, (start, end) in windows.items())
    for previous, current in zip(ordered, ordered[1:]):
        if (
            current[0] <= previous[1]
            and current[2] in _LEGACY_RESOURCE_WINDOW_NAMES
            and previous[2] in _LEGACY_RESOURCE_WINDOW_NAMES
        ):
            raise ValueError(
                f"measurement windows overlap: {previous[2]} and {current[2]}"
            )
    return windows


def _attribute_resource_windows(
    samples: list[dict[str, Any]], path: Path
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    try:
        ranges = _read_resource_windows(path)
    except ValueError as exc:
        reason = str(exc)
        unavailable = {
            name: {
                "status": "unavailable",
                "reason": reason,
                "sample_count": 0,
                "measurements": _resource_measurements([]),
            }
            for name in _LEGACY_RESOURCE_WINDOW_NAMES
        }
        return unavailable, {"status": "unavailable", "reason": reason}

    attributed = {}
    for name in ranges:
        start, end = ranges[name]
        selected = [
            sample
            for sample in samples
            if isinstance(sample.get("ts_epoch_s"), (int, float))
            and start <= float(sample["ts_epoch_s"]) <= end
        ]
        attributed[name] = {
            "status": "available" if selected else "unavailable",
            **({} if selected else {"reason": "no resource samples fall inside the window"}),
            "start_epoch_seconds": start,
            "end_epoch_seconds": end,
            "duration_seconds": end - start,
            "sample_count": len(selected),
            "measurements": _resource_measurements(selected),
        }
    return attributed, {"status": "available"}


class AmaruResourceCollector:
    """Sample Linux process counters for the resolved Amaru child process."""

    def __init__(
        self,
        entry: dict[str, Any],
        *,
        runtime_metadata_path: str | Path,
        target_node: str,
        sample_interval_seconds: float = 1.0,
        resolve_pid: Callable[[Path, str], int] = resolve_target_pid,
        sample_reader: Callable[[int, int], dict[str, Any]] | None = None,
        background: bool = True,
        capture_timeout_seconds: float = 1.0,
    ) -> None:
        if sample_interval_seconds <= 0 or sample_interval_seconds > 60:
            raise ValueError("sample_interval_seconds must be within (0, 60]")
        if capture_timeout_seconds <= 0 or capture_timeout_seconds > 60:
            raise ValueError("capture_timeout_seconds must be within (0, 60]")
        self.entry = entry
        self.runtime_metadata_path = Path(runtime_metadata_path)
        self.target_node = target_node
        self.sample_interval_seconds = sample_interval_seconds
        self._resolve_pid = resolve_pid
        self._sample_reader = sample_reader
        self._background = background
        self._capture_timeout_seconds = capture_timeout_seconds
        self._pid: int | None = None
        self._samples: list[dict[str, Any]] = []
        self._sample_errors: list[str] = []
        self._lock = threading.Lock()
        self._capture_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def prepare(self, context) -> None:
        if not self.runtime_metadata_path.is_file():
            raise FileNotFoundError(
                f"runtime metadata is unavailable: {self.runtime_metadata_path}"
            )
        context.collector_dir.mkdir(parents=True, exist_ok=True)
        self._pid = int(self._resolve_pid(self.runtime_metadata_path, self.target_node))

    def _read_sample(self, index: int) -> dict[str, Any]:
        if self._pid is None:
            raise RuntimeError("Amaru resource collector was not prepared")
        if self._sample_reader is not None:
            sample = self._sample_reader(self._pid, index)
        else:
            sample = collect_samples(
                pid=self._pid,
                sample_count=1,
                sample_interval_seconds=0,
            )[0]
        return {**sample, "sample_index": index, "target_node": self.target_node}

    def _capture(self) -> None:
        with self._capture_lock:
            self._capture_serialized()

    def _capture_serialized(self) -> None:
        with self._lock:
            index = len(self._samples)
        try:
            sample = self._read_sample(index)
        except (FileNotFoundError, ProcessLookupError):
            try:
                previous_pid = self._pid
                self._pid = int(
                    self._resolve_pid(self.runtime_metadata_path, self.target_node)
                )
                if self._pid == previous_pid:
                    raise ProcessLookupError("target PID did not change after process exit")
                sample = self._read_sample(index)
            except Exception as exc:
                with self._lock:
                    self._sample_errors.append(f"{type(exc).__name__}: {exc}")
                return
        except Exception as exc:  # optional telemetry must not stop the scenario
            with self._lock:
                self._sample_errors.append(f"{type(exc).__name__}: {exc}")
            return
        with self._lock:
            self._samples.append(sample)

    def _sample_loop(self) -> None:
        while not self._stop_event.wait(self.sample_interval_seconds):
            self._capture()

    def start(self, context) -> None:
        self._capture()
        if self._background:
            self._thread = threading.Thread(
                target=self._sample_loop,
                name=f"dwarf-resource-{self.target_node}",
                daemon=True,
            )
            self._thread.start()

    def on_marker(self, marker, context) -> None:
        return None

    def stop(self, context) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(
                timeout=max(
                    1.0,
                    self.sample_interval_seconds * 2,
                    self._capture_timeout_seconds + 1,
                )
            )
            if self._thread.is_alive():
                raise RuntimeError("resource sampler did not terminate before finalization")
        self._capture()

    def finalize(self, context) -> dict[str, Any]:
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("resource sampler is still active during finalization")
        with self._lock:
            samples = list(self._samples)
            sample_errors = list(self._sample_errors)
        measurements = _resource_measurements(samples)
        windows, window_attribution = _attribute_resource_windows(
            samples, context.run_dir / "measurements" / "windows.ndjson"
        )
        result = {
            "schema_version": "v1",
            "measurement_id": self.entry["id"],
            "target": {"node": self.target_node, "pid": self._pid},
            "measurements": measurements,
            "windows": windows,
            "window_attribution": window_attribution,
            "source": {
                "kind": "linux-proc",
                "sample_count": len(samples),
                "sample_interval_seconds": self.sample_interval_seconds,
                "errors": sample_errors,
            },
        }
        context.write_json("samples.json", samples)
        context.write_json("result.json", result)
        return result
