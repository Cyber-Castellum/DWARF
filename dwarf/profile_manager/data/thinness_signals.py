"""Item A (Phase 4.3 D-1) — thinness-suspicion detection rules.

Pure detection logic, callable from the dashboard data layer at row-render
time and at single-bundle-inspector time. Each rule returns a structured
match record (rule code, short title, human reason, cited evidence
fields) or ``None`` if the rule does not fire.

Design intent (per the 2026-04-30 dashboard rendering audit):

- Conservative defaults — under-fire is fine, over-fire is bad. The
  badge is additive next to the existing pass/fail color; it does NOT
  change pass-rate semantics (that's deferred design-option D).
- Rules are derived from concrete known-thin bundles cited in the
  Phase 1 receipts (audit-fix-tracker.md), not from a hypothetical
  taxonomy. Each rule names the finding it generalises.
- Independent rules: every rule fires on its own merit; multiple may
  fire on the same bundle; ordering is the order rules are evaluated.
- Source-discipline: every match record cites the source file/path the
  evidence came from so the inspector banner can deep-link.

Rules implemented:
  R-F007 — singleton-completed: load completed exactly once with zero
           per-iteration / per-case rows.
  R-F008 — single-node clean-start: ``all_nodes_started_clean`` passed
           on a devnet scenario with ``completed == 1``.
  R-F009 — missing-bytes roundtrip: ``roundtrip_equals_original``
           passed but no log iteration row carries ``input_hex`` or
           ``reencoded_hex``.
  R-F003 — zero-cycle AFL++: ``outputs/aflpp/default/fuzzer_stats``
           reports ``cycles_done = 0``.
  R-AFLNET — hollow-exec AFLNet: ``outputs/aflnet/default/fuzzer_stats``
             reports ``execs_done < 100`` (sentinel for the 27-bundle
             false-positive lane).

Intentionally NOT implemented here (separate dispatches): live floor
re-evaluation against tightened evaluators (option C); thinness rolled
into pass_rate aggregation (option D).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


_ITER_EVENT_NAMES = frozenset({"iteration_outcome", "iteration", "case"})


def _read_json(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _iter_log_events(run_dir: Path):
    log = run_dir / "log.ndjson"
    if not log.is_file():
        return
    with log.open("r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _detect_singleton_completed(run_dir: Path, manifest: dict) -> dict | None:
    """R-F007 — load completed exactly once with no per-iteration events.

    Pre-floor F-007 pass shape (e.g. bundle 4be131f5 in the Phase 1
    receipts). The bundle's only load-phase signal is the ``completed``
    event itself; no ``iteration``, ``iteration_outcome``, or ``case``
    rows exist to constrain the assertion.
    """
    if (manifest.get("runtime") or "") != "library":
        return None
    aflpp_stats = _read_fuzzer_stats(run_dir, "aflpp")
    if aflpp_stats is not None and int(aflpp_stats.get("execs_done", 0) or 0) > 0:
        return None
    completed_load = 0
    iter_rows = 0
    for ev in _iter_log_events(run_dir):
        evt = ev.get("event")
        phase = ev.get("phase")
        if evt == "completed" and phase == "load":
            completed_load += 1
        if evt in _ITER_EVENT_NAMES:
            iter_rows += 1
    if completed_load == 1 and iter_rows == 0:
        return {
            "rule": "R-F007",
            "title": "Singleton-completed",
            "reason": (
                "Load phase completed once with zero per-iteration / per-case "
                "rows. Pre-floor F-007 pass shape — assertion accepted on a "
                "single completed event."
            ),
            "evidence": [
                {"label": "completed events (phase=load)", "value": str(completed_load),
                 "source": "log.ndjson"},
                {"label": "iteration / case rows",
                 "value": str(iter_rows), "source": "log.ndjson"},
            ],
            "finding": "F-007",
        }
    return None


def _detect_single_node_clean(run_dir: Path, assertions: list, manifest: dict) -> dict | None:
    """R-F008 — all_nodes_started_clean passed on a devnet run with
    completed==1.

    F-018 noted 0/38 consumers override the floor; substrate scenarios
    declare multi-node topology but the assertion's evaluated_value
    reports observed completed=1 for the bundle that ran. The floor is
    vacuous in practice.
    """
    if (manifest.get("runtime") or "") != "devnet":
        return None
    for a in assertions:
        if a.get("primitive") != "all_nodes_started_clean":
            continue
        if a.get("result") != "pass":
            continue
        ev = a.get("evaluated_value") or {}
        completed = ev.get("completed")
        observed_node_count = ev.get("observed_node_count")
        if not isinstance(observed_node_count, int):
            data_points_used = a.get("data_points_used") or []
            for point in data_points_used:
                node_count = point.get("node_count")
                if isinstance(node_count, int):
                    observed_node_count = node_count
                    break
        if not isinstance(observed_node_count, int):
            node_started_count = 0
            for log_event in _iter_log_events(run_dir):
                if log_event.get("event") == "node_started" and log_event.get("phase") == "setup":
                    node_started_count += 1
            if node_started_count > 0:
                observed_node_count = node_started_count
        if (
            isinstance(completed, int)
            and completed <= 1
            and (not isinstance(observed_node_count, int) or observed_node_count <= 0)
        ):
            return {
                "rule": "R-F008",
                "title": "Single-node clean-start",
                "reason": (
                    "all_nodes_started_clean passed with a single compose "
                    "completion event and no observed node-count evidence. "
                    "This is the vacuous F-008 shape, not a normal "
                    "compose-substrate receipt."
                ),
                "evidence": [
                    {"label": "primitive", "value": "all_nodes_started_clean",
                     "source": "assertions.json"},
                    {"label": "evaluated.completed", "value": str(completed),
                     "source": "assertions.json"},
                    {"label": "observed node_count",
                     "value": "missing" if not isinstance(observed_node_count, int) else str(observed_node_count),
                     "source": "assertions.json"},
                    {"label": "params", "value": json.dumps(a.get("params") or {}),
                     "source": "assertions.json"},
                ],
                "finding": "F-008",
            }
    return None


def _detect_missing_bytes_roundtrip(run_dir: Path, assertions: list) -> dict | None:
    """R-F009 — roundtrip_equals_original passed but no log iteration
    row carries ``input_hex`` or ``reencoded_hex``.

    Bundle 8b7bf69f exemplar: 1649 iteration rows with outcome="ok" and
    ``input_hex=ABSENT, reencoded_hex=ABSENT``. The assertion's equality
    floor passes on the equality of two missing values.
    """
    has_pass_assertion = any(
        a.get("primitive") == "roundtrip_equals_original" and a.get("result") == "pass"
        for a in assertions
    )
    if not has_pass_assertion:
        return None
    ok_rows = 0
    rows_with_bytes = 0
    for ev in _iter_log_events(run_dir):
        if ev.get("event") not in _ITER_EVENT_NAMES:
            continue
        payload = ev.get("payload") or {}
        if payload.get("outcome") != "ok":
            continue
        ok_rows += 1
        if "input_hex" in payload or "reencoded_hex" in payload:
            rows_with_bytes += 1
    if ok_rows >= 1 and rows_with_bytes == 0:
        return {
            "rule": "R-F009",
            "title": "Missing-bytes roundtrip",
            "reason": (
                "roundtrip_equals_original passed but no log iteration row "
                "persists input_hex or reencoded_hex. F-009 floor passes on "
                "equality of missing fields."
            ),
            "evidence": [
                {"label": "ok rows in log.ndjson", "value": str(ok_rows),
                 "source": "log.ndjson"},
                {"label": "ok rows carrying input_hex / reencoded_hex",
                 "value": str(rows_with_bytes), "source": "log.ndjson"},
            ],
            "finding": "F-009",
        }
    return None


_BITMAP_RE = re.compile(r"^cycles_done\s*:\s*(\d+)\s*$", re.M)
_EXECS_RE = re.compile(r"^execs_done\s*:\s*(\d+)\s*$", re.M)


def _read_fuzzer_stats(run_dir: Path, engine: str) -> dict | None:
    fs = run_dir / "outputs" / engine / "default" / "fuzzer_stats"
    if not fs.is_file():
        return None
    try:
        text = fs.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    out: dict = {"path": f"outputs/{engine}/default/fuzzer_stats"}
    cm = _BITMAP_RE.search(text)
    if cm:
        try:
            out["cycles_done"] = int(cm.group(1))
        except ValueError:
            pass
    em = _EXECS_RE.search(text)
    if em:
        try:
            out["execs_done"] = int(em.group(1))
        except ValueError:
            pass
    return out


def _detect_zero_cycle_aflpp(run_dir: Path) -> dict | None:
    """R-F003 — outputs/aflpp/default/fuzzer_stats with cycles_done==0.

    Bundle 66e753e3 exemplar: cycles_done=0 with bitmap_cvg=2.86%. The
    F-003 tightened floor (min_cycles_done=1) would reject; today's
    dashboard renders it identical to a 283-cycle pass.
    """
    stats = _read_fuzzer_stats(run_dir, "aflpp")
    if stats is None:
        return None
    if stats.get("cycles_done") == 0:
        return {
            "rule": "R-F003",
            "title": "Zero-cycle AFL++",
            "reason": (
                "AFL++ smoke completed without finishing one full bitmap "
                "cycle. F-003 tightened floor (min_cycles_done=1) would "
                "reject; pre-floor pass."
            ),
            "evidence": [
                {"label": "cycles_done", "value": "0", "source": stats["path"]},
                {"label": "execs_done",
                 "value": str(stats.get("execs_done", "—")),
                 "source": stats["path"]},
            ],
            "finding": "F-003",
        }
    return None


def _detect_hollow_aflnet(run_dir: Path) -> dict | None:
    """R-AFLNET — outputs/aflnet/default/fuzzer_stats with execs_done<100.

    Sentinel for the original 27-bundle false-positive lane. <100
    execs is the hollow-runner shape that triggered the audit-cascade
    discipline rewrite.
    """
    stats = _read_fuzzer_stats(run_dir, "aflnet")
    if stats is None:
        return None
    execs = stats.get("execs_done")
    if isinstance(execs, int) and execs < 100:
        return {
            "rule": "R-AFLNET",
            "title": "Hollow-exec AFLNet",
            "reason": (
                f"AFLNet campaign reported execs_done={execs} (<100). "
                "Sentinel for the original 27-bundle false-positive lane."
            ),
            "evidence": [
                {"label": "execs_done", "value": str(execs),
                 "source": stats["path"]},
            ],
            "finding": "AFLNET",
        }
    return None


def detect_thinness(run_dir: Path) -> list[dict]:
    """Return the list of fired thinness-suspicion rules for one run.

    Each entry carries (rule, title, reason, evidence, finding). Empty
    list means the bundle is clean under the current rule set — that
    does NOT mean the bundle is a strong proof, only that no defined
    rule fired on it (audit option E will provide the broader retro
    classification surface).
    """
    if not run_dir.is_dir():
        return []
    manifest = _read_json(run_dir / "manifest.json") or {}
    assertions = _read_json(run_dir / "assertions.json") or []
    if not isinstance(assertions, list):
        assertions = []
    matches: list[dict] = []
    for fn in (
        lambda: _detect_singleton_completed(run_dir, manifest),
        lambda: _detect_single_node_clean(run_dir, assertions, manifest),
        lambda: _detect_missing_bytes_roundtrip(run_dir, assertions),
        lambda: _detect_zero_cycle_aflpp(run_dir),
        lambda: _detect_hollow_aflnet(run_dir),
    ):
        try:
            m = fn()
        except Exception:  # noqa: BLE001 — defensive; never crash the row render
            m = None
        if m is not None:
            matches.append(m)
    return matches
