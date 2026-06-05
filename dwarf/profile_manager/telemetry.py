import json
import os
import subprocess
import threading
import time
from pathlib import Path


def _utc_now_iso():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{datetime.now(timezone.utc).microsecond // 1000:03d}Z"


def _append_ndjson(path: Path, payload: dict):
    with path.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(payload, sort_keys=True, ensure_ascii=False) + "\n")


def _safe_getloadavg():
    try:
        one, five, fifteen = os.getloadavg()
        return {"load1": one, "load5": five, "load15": fifteen}
    except (AttributeError, OSError):
        return None


def _safe_process_sample(pid: int):
    sample = {
        "pid": pid,
        "rss_bytes": None,
        "cpu_percent": None,
        "fd_count": None,
        "socket_count": None,
    }
    try:
        ps = subprocess.run(
            ["ps", "-o", "rss=,pcpu=", "-p", str(pid)],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        values = ps.stdout.strip().split()
        if values:
            sample["rss_bytes"] = int(values[0]) * 1024
        if len(values) > 1:
            sample["cpu_percent"] = float(values[1])
    except (OSError, subprocess.SubprocessError, ValueError):
        pass

    fd_dir = Path("/proc") / str(pid) / "fd"
    if fd_dir.is_dir():
        try:
            entries = list(fd_dir.iterdir())
            sample["fd_count"] = len(entries)
            socket_count = 0
            for entry in entries:
                try:
                    target = os.readlink(entry)
                except OSError:
                    continue
                if "socket:" in target:
                    socket_count += 1
            sample["socket_count"] = socket_count
            return sample
        except OSError:
            return sample

    try:
        lsof = subprocess.run(
            ["lsof", "-n", "-P", "-p", str(pid)],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        lines = lsof.stdout.splitlines()[1:]
        if lines:
            sample["fd_count"] = len(lines)
            sample["socket_count"] = sum(1 for line in lines if "TCP" in line or "UDP" in line or "sock" in line.lower())
    except (OSError, subprocess.SubprocessError):
        pass
    return sample


class ObserverCollector:
    def __init__(self, *, metrics_dir: Path, pid: int | None = None, sample_interval_seconds: float = 0.5):
        self.metrics_dir = metrics_dir
        self.host_dir = metrics_dir / "host"
        self.process_dir = metrics_dir / "process"
        self.runtime_dir = metrics_dir / "runtime"
        self.pid = pid or os.getpid()
        self.sample_interval_seconds = sample_interval_seconds
        self._stop_event = threading.Event()
        self._thread = None
        self.host_samples_path = self.host_dir / "load.ndjson"
        self.process_samples_path = self.process_dir / "self.ndjson"

    def start(self):
        self.host_dir.mkdir(parents=True, exist_ok=True)
        self.process_dir.mkdir(parents=True, exist_ok=True)
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self._write_sample()
        self._thread = threading.Thread(target=self._run, name="dwarf-observer-collector", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._write_sample()

    def summarize(self):
        return {
            "sample_interval_seconds": self.sample_interval_seconds,
            "host_load_samples_path": "metrics/host/load.ndjson",
            "process_samples_path": "metrics/process/self.ndjson",
            "host_load_samples": _count_lines(self.host_samples_path),
            "process_samples": _count_lines(self.process_samples_path),
        }

    def _run(self):
        while not self._stop_event.wait(self.sample_interval_seconds):
            self._write_sample()

    def _write_sample(self):
        ts = _utc_now_iso()
        load = _safe_getloadavg()
        if load is not None:
            _append_ndjson(self.host_samples_path, {"ts": ts, "value": load})
        process_sample = _safe_process_sample(self.pid)
        _append_ndjson(self.process_samples_path, {"ts": ts, "value": process_sample})


def _count_lines(path: Path):
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as fp:
        return sum(1 for _ in fp)
