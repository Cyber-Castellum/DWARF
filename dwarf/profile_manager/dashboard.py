import html
import json
import mimetypes
import os
import socket
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from collections import Counter
from urllib.parse import parse_qs, quote, urlsplit

from profile_manager.config import config_exists, config_path, load_config
from profile_manager.data.commands import _command_cards, _command_rows
from profile_manager.data.config import (
    _config_payload,
    _discover_project_root,
    _local_interface_urls,
    _safe_project_file,
    default_dashboard_dir,
)
from profile_manager.data.deliverables import (
    _deliverable_catalog,
    _deliverable_entry,
    _deliverable_rows,
    _doc_links,
    _document_rows,
)
from profile_manager.data.packages import _package_rows
from profile_manager.data.profiles import _profile_rows
from profile_manager.data.health import (
    _extract_health_value,
    _extract_tip_json,
    _health_from_body,
    _latest_profile_health,
    _live_health,
)
from profile_manager.data.bundles import _forensic_bundles_dir, _latest_evidence_rows
from profile_manager.data.fuzz import _fuzz_rows, _smoke_rows
from profile_manager.data.lifecycle import (
    _live_testcase_lifecycle_summary,
    _local_testcase_lifecycle_summary,
    _read_ndjson_rows,
    _summarize_testcase_state,
)
from profile_manager.data.runs import (
    _forensic_runs_dir,
    _ssh_remote_lister,
    humanize_decode_error,
    list_recent_runs_with_remote,
    parse_remote_sources,
    recent_runs_payload,
)
from profile_manager.data.scenarios import (
    _humanize_scenario_id,
    _list_scenarios_for_compare,
    _scenarios_dir,
)
from profile_manager.data.files import (
    _attachment_headers,
    _best_existing,
    _download_url,
    _escape,
    _latest_files,
    _pdf_url,
    _read_text,
)
from profile_manager.evidence_packages import load_evidence_packages
from profile_manager.fuzz import load_fuzz_tests
from profile_manager.inspect import inspect_health_command
from profile_manager.moog import moog_health_summary, query_moog_health
from profile_manager.profiles import load_profiles
from profile_manager.remote import ssh_command
from profile_manager.smoke import load_smoke_tests
from profile_manager.wallets import wallet_statuses
from profile_manager.views.concepts import render_learn_concepts
from profile_manager.views.coverage import render_learn_coverage
from profile_manager.views.learn_cli import render_learn_cli
from profile_manager.views.architecture import render_learn_architecture
from profile_manager.views.compare import render_operate_compare
from profile_manager.views.operate_coverage import render_operate_coverage
from profile_manager.views.operate_crashes import render_operate_crashes
from profile_manager.views.operate_schedule import render_operate_schedule
from profile_manager.views.operate_audit import render_operate_audit
from profile_manager.views.operate_timeline import render_operate_timeline
from profile_manager.views.operate_static_analysis import render_operate_static_analysis
from profile_manager.views.operate_profiles import render_operate_profiles
from profile_manager.views.operate_bundles import render_operate_bundles
from profile_manager.views.operate_plugins import render_operate_plugins
from profile_manager.views.operate_config import render_operate_config
from profile_manager.views.operate_notifications import render_operate_notifications
from profile_manager.views.learn_examples import render_learn_examples, render_learn_getting_started
from profile_manager.views.learn_api import render_learn_api
from profile_manager.views.learn_docs import (
    render_learn_glossary, render_learn_faq, render_learn_troubleshooting,
)
from profile_manager.views.learn_runbooks import (
    render_learn_operator_runbook,
    render_learn_developer_onboarding,
    render_learn_plugin_authoring_guide,
)
from profile_manager.views.operate_contract import render_operate_contract
from profile_manager.views.operate_run import (
    render_operate_run,
    render_operate_run_not_found,
)
from profile_manager.views.operate_runs import render_operate_runs
from profile_manager.views.operate_status import render_operate_status
from profile_manager.views.operate_targets import render_operate_targets
from profile_manager.views.status import render_learn_status
from profile_manager.views.walkthroughs import render_learn_walkthroughs
from profile_manager.views.learn import render_learn_landing
from profile_manager.views.operate import render_operate_landing
from profile_manager.views.scenarios import render_operate_scenarios


PROJECT_ROOT = _discover_project_root(Path(__file__).resolve())


def _pick_dashboard_root(project_root: Path) -> Path:
    """Pick the dashboard root that actually contains the dashboard assets.

    Two candidate layouts:
    - ``project_root/dwarf/dashboard``  (local Mac dev checkout: dwarf/ is the
      app subdir under the parent ada2 repo)
    - ``project_root/dashboard``        (cardano-box flattened layout: rsync
      from sync-dwarf-fw.sh strips the dwarf/ wrapper, putting dashboard/ +
      profile_manager/ at top level)

    Pre-slice-19 logic checked only that ``project_root/dwarf`` *existed*,
    which on cardano-box was true but stale -- a leftover ``dwarf/`` directory
    from an old layout containing only an ``index.html``, no ``static/``.
    Result: the picker selected ``project_root/dwarf/dashboard`` (which had
    no ``static/``), so ``/static/css/base.css`` returned 404 and all
    theming broke.

    Hardened: prefer the candidate whose ``static/`` subdir actually exists.
    That's the load-bearing signal that the candidate is a real working
    dashboard root, not a stale leftover.

    Falls through to the first existing candidate if neither has ``static/``,
    so fresh checkouts that haven't materialized assets still resolve.
    """
    candidates = [
        project_root / "dwarf" / "dashboard",
        project_root / "dashboard",
    ]
    for candidate in candidates:
        if (candidate / "static").is_dir():
            return candidate
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


DASHBOARD_ROOT = _pick_dashboard_root(PROJECT_ROOT)


@dataclass(frozen=True)
class DashboardResult:
    path: Path
    url: str


def build_dashboard_status_payload(live=True, profile_id="profile-a-haskell-peersharing-disabled"):
    health_path, health_body = _latest_profile_health()
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": _config_payload(),
        "profiles": _profile_rows(),
        "evidence_packages": _package_rows(),
        "smoke_tests": _smoke_rows(),
        "fuzz_tests": _fuzz_rows(),
        "documents": _document_rows(),
        "deliverables": _deliverable_rows(),
        "commands": _command_rows(),
        "latest_evidence": _latest_evidence_rows(),
        "wallets": _wallet_status_rows_for_payload(),
        "moog": _moog_status_for_payload(),
        "testcase_lifecycle": _live_testcase_lifecycle_summary() if live else _local_testcase_lifecycle_summary(),
        "last_local_health": _health_from_body(health_body, evidence_path=health_path),
        "live": {"enabled": False, "profile_id": profile_id},
    }
    if live:
        payload["live"] = _live_health(profile_id)
    return payload


def _moog_status_for_payload():
    if not config_exists():
        return {
            "state": "unknown",
            "summary": {"state": "unknown", "check_count": 0, "ok_count": 0, "warn_count": 0, "error_count": 0},
            "checks": [],
            "wallets": {},
            "error": "Dwarf config is missing.",
        }
    try:
        health = query_moog_health(load_config(), timeout=10)
        return {
            **health,
            "summary": moog_health_summary(health),
        }
    except Exception as exc:
        return {
            "state": "error",
            "summary": {"state": "error", "check_count": 0, "ok_count": 0, "warn_count": 0, "error_count": 1},
            "checks": [],
            "wallets": {},
            "error": str(exc),
        }


def _wallet_status_rows_for_payload():
    if not config_exists():
        return []
    try:
        return wallet_statuses(load_config(), timeout=10)
    except Exception as exc:
        return [{
            "id": "wallet-config",
            "label": "Wallet config",
            "role": "unknown",
            "network": "unknown",
            "address": "unknown",
            "state": "error",
            "balance_lovelace": None,
            "balance_tada": "unknown",
            "recent_transactions": [],
            "queried_at": None,
            "error": str(exc),
        }]


def dashboard_status_text(output_dir=None):
    output = Path(output_dir).expanduser() if output_dir else default_dashboard_dir()
    return (
        "Dashboard\n"
        f"Output: {output / 'index.html'}\n"
        f"Config: {config_path()} ({'present' if config_exists() else 'missing'})\n"
        f"Profiles: {len(load_profiles())}\n"
        f"Evidence packages: {len(load_evidence_packages())}\n"
        f"Smoke tests: {len(load_smoke_tests())}\n"
        f"Fuzz tests: {len(load_fuzz_tests())}\n"
    )


def _render_table(headers, rows):
    head = "".join(f"<th>{_escape(header)}</th>" for header in headers)
    body_rows = []
    for row in rows:
        body_rows.append("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>"


def _convert_project_file_to_pdf(path):
    suffix = path.suffix.lower()
    with tempfile.TemporaryDirectory(prefix="dwarf-pdf-") as tmp:
        tmpdir = Path(tmp)
        html_path = tmpdir / "source.html"
        pdf_path = tmpdir / "output.pdf"
        if suffix in {".md", ".html", ".htm", ".docx"}:
            try:
                html_result = subprocess.run(
                    [
                        "pandoc",
                        str(path),
                        "-s",
                        "--embed-resources",
                        "--standalone",
                        "-t",
                        "html",
                        "-o",
                        str(html_path),
                    ],
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=90,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                return None, f"PDF conversion requires pandoc for {suffix} files: {exc}\n"
            if html_result.returncode != 0:
                detail = html_result.stderr.strip() or html_result.stdout.strip() or "pandoc html conversion failed"
                return None, detail + "\n"
        else:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                return None, f"could not read file: {exc}\n"
            html_path.write_text(
                "<!doctype html><html><head><meta charset='utf-8'>"
                "<style>body{font:14px system-ui,sans-serif;margin:36px;}pre{white-space:pre-wrap;}</style>"
                f"</head><body><h1>{_escape(path.name)}</h1><pre>{_escape(text)}</pre></body></html>",
                encoding="utf-8",
            )
        try:
            pdf_result = subprocess.run(
                [
                    "npx",
                    "--yes",
                    "playwright",
                    "pdf",
                    "--paper-format",
                    "Letter",
                    html_path.as_uri(),
                    str(pdf_path),
                ],
                text=True,
                capture_output=True,
                check=False,
                timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return None, f"PDF rendering requires Playwright Chromium: {exc}\n"
        if pdf_result.returncode != 0:
            detail = pdf_result.stderr.strip() or pdf_result.stdout.strip() or "playwright pdf rendering failed"
            return None, detail + "\n"
        try:
            return pdf_path.read_bytes(), None
        except OSError as exc:
            return None, f"could not read rendered PDF: {exc}\n"


def _script_json(payload):
    return json.dumps(payload).replace("</", "<\\/")


def _bootstrap_payload():
    return _script_json(build_dashboard_status_payload(live=False))


def _nav(active_route):
    """Slice-21 CP1.5 retains this for legacy callers/tests; the rendered
    chrome now uses _legacy_subnav() instead. Same 8 items, same active-
    matching, but with mono-eyebrow forensic-noir styling applied via the
    .legacy-subnav CSS class."""
    return _legacy_subnav(active_route)


def _legacy_subnav(active_route):
    """Sub-nav row for the 8 preserved-legacy routes. Sits below the
    slice-20 OPERATE/LEARN primary nav so operators can cross-link
    between legacy surfaces (project, deliverables, scenarios, compare,
    architecture, settings, tests, raw) without losing the new shell."""
    items = [
        ("/scenarios", "Scenarios"),
        ("/tests", "Tests & Evidence"),
    ]
    return "".join(
        f'<a class="{"active" if route == active_route else ""}" href="{route}">{label}</a>'
        for route, label in items
    )


def _target_live_cards():
    return """
  <section class="grid basic-only">
    <div class="card notice"><h3>Test environment</h3><div id="target-env-basic">Loading...</div></div>
    <div class="card live"><h3>Status</h3><div id="live-summary-basic">Loading...</div></div>
  </section>
  <section class="grid adv-only">
    <div class="card notice"><h3>Target Environment</h3><div id="target-env">Loading...</div></div>
    <div class="card live"><h3>Live Runtime</h3><div id="live-summary">Waiting for /api/status...</div></div>
  </section>
"""


def _common_script(render_body):
    return f"""
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, ch => ({{"&":"&amp;","<":"&lt;",">":"&gt;","\\"":"&quot;","'":"&#39;"}}[ch]));
const code = (value) => `<code>${{esc(value)}}</code>`;
function table(headers, rows) {{
  const head = headers.map(h => `<th>${{esc(h)}}</th>`).join("");
  const body = rows.map(row => `<tr>${{row.map(cell => `<td>${{cell}}</td>`).join("")}}</tr>`).join("");
  return `<table><thead><tr>${{head}}</tr></thead><tbody>${{body}}</tbody></table>`;
}}
function liveHealth(payload) {{
  const live = payload.live || {{}};
  return live.health || payload.last_local_health || {{}};
}}
function renderTargetAndLive(payload) {{
  const cfg = payload.config || {{}};
  const adv = document.getElementById("target-env");
  if (adv) adv.innerHTML = cfg.present
    ? `<strong>${{esc(cfg.deployment_name)}}</strong><br>${{esc(cfg.ssh_user)}}@${{esc(cfg.host)}}<br>remote base: ${{code(cfg.remote_base_path)}}<br>config: ${{code(cfg.path)}}`
    : `${{esc(cfg.message || "Config missing")}}<br>${{code(cfg.path || "")}}`;
  const live = payload.live || {{}};
  const health = liveHealth(payload);
  const liveClass = live.enabled && Number(health.returncode) === 0 ? "live" : "stale";
  const advLive = document.getElementById("live-summary");
  if (advLive) {{
    advLive.parentElement.className = `card ${{liveClass}}`;
    advLive.innerHTML =
      `Source: ${{live.enabled ? "live SSH poll" : "local cached evidence"}}<br>` +
      `Profile: ${{code(live.profile_id || "profile-a-haskell-peersharing-disabled")}}<br>` +
      `SSH exit: ${{esc(health.returncode ?? "n/a")}}<br>` +
      `<span class="small">Last refresh: ${{esc(payload.generated_at)}}</span>`;
  }}
  // Basic-mode plain-English summaries
  const basicEnv = document.getElementById("target-env-basic");
  if (basicEnv) basicEnv.innerHTML = cfg.present
    ? `Connected to <strong>${{esc(cfg.deployment_name)}}</strong>.`
    : `<strong>Not connected.</strong> Run <code>cardano-profile intake</code> from a terminal.`;
  const basicLive = document.getElementById("live-summary-basic");
  if (basicLive) {{
    const ok = live.enabled && Number(health.returncode) === 0;
    basicLive.parentElement.className = `card ${{liveClass}}`;
    basicLive.innerHTML = ok
      ? `The test Cardano network is <strong>running</strong>.`
      : (live.enabled
          ? `The test Cardano network is <strong>not responding</strong>.`
          : `Showing the most recent saved status (no live poll).`);
  }}
}}
function activeProfile(payload) {{
  return (payload.profiles || []).find(p => p.id === (payload.live || {{}}).profile_id) || (payload.profiles || [])[0] || {{}};
}}
{render_body}
async function refresh() {{
  try {{
    const response = await fetch("/api/status", {{cache: "no-store"}});
    if (!response.ok) throw new Error(`HTTP ${{response.status}}`);
    render(await response.json());
  }} catch (error) {{
    const fallback = JSON.parse(document.getElementById("bootstrap").textContent);
    fallback.live = fallback.live || {{}};
    fallback.live.error = String(error);
    render(fallback);
  }}
}}
render(JSON.parse(document.getElementById("bootstrap").textContent));
refresh();
setInterval(refresh, 10000);
"""


def _page(title, active_route, body, render_script):
    """Slice-21 CP1.5: legacy chrome re-themed onto the slice-20 forensic-noir
    HUD shell. The remaining preserved-legacy nav routes (/scenarios,
    /tests) flow through this helper alongside the bundle inspector at
    /runs/<id> (which slice 26 ports to /operate/runs/<id>; /runs/<id>
    is kept as a 302-style alias via render_route_html so old links still
    resolve). /compare migrated to /operate/compare in slice 24;
    /architecture re-pointed to /learn/architecture and /status added as
    a /operate/status alias in slice 25 (legacy /architecture used to
    point at /operate/status, which was wrong — architecture content
    lives at /learn/architecture). /settings still maps to
    /operate/status (the legacy settings page WAS the status console);
    /project, /deliverables, /raw retired in slice 26 (redirects only).

    The shell head + body header now match _base.j2 (logo brand mark,
    OPERATE/LEARN primary nav, plex.css/tokens.css/base.css). A secondary
    legacy-subnav row preserves the 8 cross-links between legacy pages and
    keeps the basic/advanced mode toggle.

    The remaining inline <style> block is a dark-theme reskin for legacy
    primitives (.card, .grid, .flow-step, .zone, .viz, table, pre, etc.)
    so the existing per-route HTML bodies render legibly on dark without
    being rewritten. Anti-fabrication rails preserved: every operator data
    field on every legacy page still flows through unchanged.
    """
    payload = build_dashboard_status_payload(live=False)
    bootstrap_json = _script_json(payload)
    common_script = _common_script(render_script) if render_script.strip() else ""
    common_script_tag = f"<script>{common_script}</script>" if common_script else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Dwarf — {title}</title>
<link rel="icon" type="image/png" href="/static/dwarf-logo.png">
<link rel="apple-touch-icon" href="/static/dwarf-logo.png">
<meta name="theme-color" content="#06080B">
<link rel="stylesheet" href="/static/css/plex.css">
<link rel="stylesheet" href="/static/css/tokens.css">
<link rel="stylesheet" href="/static/css/base.css">
<style>
/* Slice-21 CP1.5: dark-theme reskin for legacy primitives. The slice-20
   tokens (--obsidian-N, --cyan-*, --ink-*, --crimson-*) come from
   tokens.css; this block maps the legacy class names onto them. */
:root {{
  /* Legacy aliases — point legacy code paths at slice-20 tokens. */
  --bg: var(--obsidian-0);
  --panel: var(--obsidian-2);
  --line: var(--cyan-trace);
  --accent: var(--cyan-primary);
  --muted: var(--ink-secondary);
  --warn: var(--cork);
  --danger: var(--crimson);
}}
/* Legacy main area opens up wider than the operate/learn surfaces; the
   legacy pages have wide tables and grids. */
body[data-shell="legacy"] .shell-main {{
  max-width: 1280px;
  padding-block: var(--space-8) var(--space-24);
}}

/* Legacy section / heading rhythm. */
body[data-shell="legacy"] section {{ margin: 0 0 var(--space-8); }}
body[data-shell="legacy"] section h2 {{
  font-size: 1.4rem; font-weight: 700; color: var(--ink-primary);
  letter-spacing: -0.005em; margin: 0 0 var(--space-4);
}}
body[data-shell="legacy"] section h3 {{
  font-size: 1rem; font-weight: 500; color: var(--ink-primary);
  margin: 0 0 var(--space-2);
}}

/* Card / metric / flow-step / zone / status-tile / node-box —
   surface-2 obsidian + cyan-trace edge. Replaces 1px line + cream paper. */
body[data-shell="legacy"] .card,
body[data-shell="legacy"] .metric,
body[data-shell="legacy"] .flow-step,
body[data-shell="legacy"] .zone,
body[data-shell="legacy"] .status-tile,
body[data-shell="legacy"] .node-box {{
  background: var(--obsidian-2);
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  padding: var(--space-4);
  box-shadow: var(--shadow-card);
}}
body[data-shell="legacy"] .card.live,
body[data-shell="legacy"] .notice {{
  border-left: 3px solid var(--cyan-primary);
}}
body[data-shell="legacy"] .card.bad,
body[data-shell="legacy"] .danger {{
  border-left: 3px solid var(--crimson);
}}
body[data-shell="legacy"] .card.stale {{
  border-left: 3px solid var(--cork);
}}

/* Grid / status-strip / node-lane — tighter on dark, no change in semantics. */
body[data-shell="legacy"] .grid {{
  display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: var(--space-3);
}}
body[data-shell="legacy"] .status-strip {{
  display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: var(--space-3);
}}
body[data-shell="legacy"] .node-lane {{
  display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: var(--space-3);
}}
body[data-shell="legacy"] .flow {{
  display: flex; flex-wrap: wrap; align-items: stretch; gap: var(--space-3);
}}
body[data-shell="legacy"] .flow-step {{ flex: 1 1 160px; }}

/* Metric / status-tile typography. */
body[data-shell="legacy"] .metric span,
body[data-shell="legacy"] .status-tile span {{
  display: block; color: var(--ink-tertiary);
  font-family: var(--font-mono); font-size: var(--type-eyebrow);
  letter-spacing: var(--tracking-eyebrow); text-transform: uppercase;
}}
body[data-shell="legacy"] .metric strong,
body[data-shell="legacy"] .status-tile strong {{
  display: block; margin-top: var(--space-1); color: var(--ink-primary);
  font-family: var(--font-mono); word-break: break-word;
}}

/* Tables — match the slice-20 dense-table style. */
body[data-shell="legacy"] table {{
  width: 100%; border-collapse: collapse;
  background: var(--obsidian-2); border: 1px solid transparent;
  border-radius: var(--radius-md); overflow: hidden;
  font-size: 0.95em;
}}
body[data-shell="legacy"] th, body[data-shell="legacy"] td {{
  text-align: left; vertical-align: top;
  padding: var(--space-2) var(--space-3);
  border-bottom: 1px solid var(--rule-soft);
}}
body[data-shell="legacy"] th {{
  background: var(--obsidian-3);
  font-family: var(--font-mono); font-weight: 400;
  font-size: var(--type-eyebrow);
  letter-spacing: var(--tracking-eyebrow); text-transform: uppercase;
  color: var(--cyan-primary);
}}
body[data-shell="legacy"] tbody tr:hover {{ background: var(--obsidian-3); }}
body[data-shell="legacy"] .table-wrap {{ overflow-x: auto; border-radius: var(--radius-md); }}
body[data-shell="legacy"] .small {{ color: var(--ink-tertiary); font-size: var(--type-small); }}

/* Pre / code blocks — terminal-grade. */
body[data-shell="legacy"] pre {{
  margin: 0; white-space: pre-wrap; overflow-wrap: anywhere;
  background: var(--obsidian-1); color: var(--ink-primary);
  border-left: 2px solid var(--cyan-trace);
  border-radius: var(--radius-md);
  padding: var(--space-3) var(--space-4);
  max-height: 420px; overflow: auto;
}}

/* Legacy span.pill — distinct from slice-20 .pill button. Static label. */
body[data-shell="legacy"] span.pill {{
  display: inline-block; border-radius: 999px;
  padding: 2px 8px; font-size: 0.78em; font-family: var(--font-mono);
  background: var(--obsidian-3); color: var(--ink-secondary);
  letter-spacing: 0.04em; text-transform: uppercase;
  border: 1px solid var(--cyan-trace); cursor: default;
}}

/* SVG diagrams: re-fill nodes/zones for dark backgrounds. */
body[data-shell="legacy"] .viz {{
  display: block; width: 100%; max-width: 100%;
  height: clamp(132px, 24vw, 210px);
  border: 1px solid transparent; border-radius: var(--radius-md);
  background: var(--obsidian-2); overflow: hidden;
}}
body[data-shell="legacy"] .viz-compact {{ height: clamp(118px, 20vw, 170px); }}
body[data-shell="legacy"] .viz-standard {{ height: clamp(150px, 28vw, 260px); }}
body[data-shell="legacy"] .viz text {{ font-family: var(--font-sans); fill: var(--ink-primary); }}
body[data-shell="legacy"] .viz .muted {{ fill: var(--ink-tertiary); font-size: 12px; }}
body[data-shell="legacy"] .viz .node {{ fill: var(--obsidian-3); stroke: var(--cyan-trace); stroke-width: 1; }}
body[data-shell="legacy"] .viz .ok {{ fill: rgba(43, 224, 224, 0.10); stroke: var(--cyan-primary); }}
body[data-shell="legacy"] .viz .warn {{ fill: rgba(181, 138, 79, 0.10); stroke: var(--cork); }}
body[data-shell="legacy"] .viz .bad {{ fill: rgba(192, 48, 48, 0.10); stroke: var(--crimson); }}
body[data-shell="legacy"] .viz .edge {{ stroke: var(--cyan-trace); stroke-width: 1.5; marker-end: url(#arrowhead); }}
body[data-shell="legacy"] .viz .zone-fill {{ fill: var(--obsidian-3); stroke: var(--cyan-trace); stroke-width: 1; }}
body[data-shell="legacy"] .viz .gate {{ fill: rgba(181, 138, 79, 0.10); stroke: var(--cork); stroke-width: 1; }}

/* Mode toggle — basic/advanced pill row. */
body[data-shell="legacy"] .mode-toggle {{
  display: inline-flex; gap: 0;
  border: 1px solid var(--cyan-trace); border-radius: 999px; overflow: hidden;
  background: var(--obsidian-2);
}}
body[data-shell="legacy"] .mode-toggle button {{
  background: transparent; color: var(--ink-secondary); border: 0;
  padding: var(--space-1) var(--space-4); font: inherit;
  font-family: var(--font-mono); font-size: var(--type-eyebrow);
  letter-spacing: var(--tracking-eyebrow); text-transform: uppercase;
  cursor: pointer;
}}
body[data-shell="legacy"] .mode-toggle button.active {{
  background: var(--cyan-tint); color: var(--cyan-glow);
}}
[data-mode="basic"] .adv-only {{ display: none !important; }}
[data-mode="advanced"] .basic-only {{ display: none !important; }}

/* Action buttons / download links — cyan-glow on dark. */
body[data-shell="legacy"] .action-row {{ display: flex; flex-wrap: wrap; gap: var(--space-3); margin: var(--space-2) 0 var(--space-4); }}
body[data-shell="legacy"] .action-button,
body[data-shell="legacy"] .download-link {{
  display: inline-block; background: var(--cyan-tint); color: var(--cyan-glow);
  border: 1px solid var(--cyan-primary); border-radius: var(--radius-md);
  padding: var(--space-2) var(--space-4); font: inherit;
  font-weight: 500; cursor: pointer; text-decoration: none;
}}
body[data-shell="legacy"] .action-button:hover,
body[data-shell="legacy"] .download-link:hover {{
  background: rgba(43, 224, 224, 0.18);
  border-color: var(--cyan-glow); color: var(--cyan-glow);
}}
body[data-shell="legacy"] .action-button.action-remove {{
  background: rgba(192, 48, 48, 0.10); border-color: var(--crimson); color: var(--crimson-glow);
}}
body[data-shell="legacy"] .action-button:disabled {{ opacity: .5; cursor: not-allowed; }}
body[data-shell="legacy"] .download-link.secondary {{ background: transparent; color: var(--ink-secondary); border-color: var(--ink-tertiary); margin-left: var(--space-2); }}
body[data-shell="legacy"] .download-link.missing {{ background: var(--obsidian-2); color: var(--ink-tertiary); border-color: transparent; cursor: default; }}
body[data-shell="legacy"] .deliverable-path {{ color: var(--ink-tertiary); font-size: var(--type-small); overflow-wrap: anywhere; }}
body[data-shell="legacy"] .milestone-heading {{ display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: var(--space-2); }}
body[data-shell="legacy"] .candidate-warning {{
  border: 1px solid rgba(181, 138, 79, 0.40); border-left: 3px solid var(--cork);
  border-radius: var(--radius-md); padding: var(--space-3) var(--space-4);
  background: rgba(181, 138, 79, 0.08); color: var(--ink-secondary);
}}

/* Modal — dark surface with cyan edge. */
body[data-shell="legacy"] .modal {{
  position: fixed; inset: 0; background: rgba(6, 8, 11, 0.75);
  display: flex; align-items: center; justify-content: center;
  z-index: 100; padding: var(--space-4);
}}
body[data-shell="legacy"] .modal[hidden] {{ display: none; }}
body[data-shell="legacy"] .modal-card {{
  background: var(--obsidian-2); border: 1px solid var(--cyan-trace);
  border-radius: var(--radius-lg); padding: var(--space-6);
  max-width: 520px; width: 100%;
  box-shadow: 0 16px 48px rgba(0,0,0,.55);
}}
body[data-shell="legacy"] .modal-card h3 {{ margin: 0 0 var(--space-2); color: var(--ink-primary); }}
body[data-shell="legacy"] .modal-card p {{ margin: 0 0 var(--space-3); color: var(--ink-secondary); }}
body[data-shell="legacy"] .modal-card input {{
  width: 100%; padding: var(--space-2); border: 1px solid var(--cyan-trace);
  border-radius: var(--radius-sm); font: inherit;
  background: var(--obsidian-1); color: var(--ink-primary);
  margin-top: var(--space-1);
}}
body[data-shell="legacy"] .modal-row {{ display: flex; justify-content: flex-end; gap: var(--space-2); margin-top: var(--space-4); }}
body[data-shell="legacy"] .modal-row button {{
  padding: var(--space-2) var(--space-4); border-radius: var(--radius-sm);
  border: 1px solid var(--cyan-trace); background: var(--obsidian-3);
  color: var(--ink-secondary); font: inherit; cursor: pointer;
}}
body[data-shell="legacy"] .modal-row button:last-child {{
  background: var(--cyan-tint); color: var(--cyan-glow);
  border-color: var(--cyan-primary);
}}
body[data-shell="legacy"] .modal-row button:disabled {{ opacity: .5; cursor: not-allowed; }}
body[data-shell="legacy"] textarea {{
  width: 100%; min-height: 180px;
  font: var(--type-small)/1.5 var(--font-mono);
  padding: var(--space-2); background: var(--obsidian-1);
  color: var(--ink-primary);
  border: 1px solid var(--cyan-trace); border-radius: var(--radius-sm);
}}

/* Legacy sub-nav — cross-link bar between the 8 legacy pages, sits below
   the slice-20 OPERATE/LEARN nav. Mono-pill row, single-line scroll. */
.legacy-subnav {{
  display: flex; flex-wrap: wrap; gap: var(--space-2);
  align-items: center;
  padding: var(--space-3) var(--space-12);
  background: var(--obsidian-1);
  box-shadow: inset 0 -1px 0 var(--cyan-trace);
}}
.legacy-subnav__label {{
  font-family: var(--font-mono); font-size: var(--type-eyebrow);
  letter-spacing: var(--tracking-eyebrow); text-transform: uppercase;
  color: var(--ink-tertiary); margin-right: var(--space-2);
}}
.legacy-subnav a {{
  font-family: var(--font-mono); font-size: var(--type-eyebrow);
  letter-spacing: 0.04em; text-transform: uppercase;
  color: var(--ink-secondary); text-decoration: none;
  padding: 4px var(--space-3);
  border: 1px solid var(--cyan-trace); border-radius: var(--radius-md);
  background: var(--obsidian-2);
  border-bottom: 1px solid var(--cyan-trace);
}}
.legacy-subnav a:hover {{
  color: var(--cyan-glow); border-color: var(--cyan-glow);
  background: var(--obsidian-3);
}}
.legacy-subnav a.active {{
  color: var(--cyan-glow); border-color: var(--cyan-primary);
  background: var(--cyan-tint);
}}
.legacy-subnav__toggle {{ margin-left: auto; }}

.legacy-strapline {{
  margin: var(--space-3) var(--space-12) 0;
  font-family: var(--font-mono);
  font-size: var(--type-eyebrow);
  letter-spacing: var(--tracking-eyebrow);
  text-transform: uppercase;
  color: var(--ink-tertiary);
  text-align: right;
}}

/* Mobile responsive — narrower padding. */
@media (max-width: 640px) {{
  body[data-shell="legacy"] .shell-main {{ padding-inline: var(--space-3); }}
  body[data-shell="legacy"] .grid {{ grid-template-columns: 1fr; gap: var(--space-2); }}
  body[data-shell="legacy"] .status-strip {{ grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--space-2); }}
  body[data-shell="legacy"] .flow-step {{ flex-basis: 100%; }}
  body[data-shell="legacy"] th, body[data-shell="legacy"] td {{ padding: var(--space-2); }}
  .legacy-subnav {{ padding-inline: var(--space-3); }}
}}
</style>
</head>
<body data-mode="basic" data-shell="legacy" data-density="dense">
<header class="shell-header">
  <a class="brand" href="/operate" aria-label="Dwarf — observability for the Cardano substrate">
    <img src="/static/dwarf-logo.png" alt="Dwarf">
  </a>
  <nav class="shell-nav" aria-label="Primary">
    <a href="/operate">Operate</a>
    <a href="/learn">Learn</a>
  </nav>
</header>
<nav class="legacy-subnav" aria-label="Legacy">
  <span class="legacy-subnav__label">Legacy</span>
  {_legacy_subnav(active_route)}
  <div id="mode-toggle" class="mode-toggle legacy-subnav__toggle" role="group" aria-label="View mode">
    <button type="button" data-target-mode="basic">Basic</button>
    <button type="button" data-target-mode="advanced">Advanced</button>
  </div>
</nav>
<p class="legacy-strapline">No browser action deploys, removes, fuzzes, or mutates runtime state.</p>
<main class="shell-main">
{body}
</main>
<script id="bootstrap" type="application/json">{bootstrap_json}</script>
<script>
(function () {{
  var key = "dwarf-ui-mode";
  var saved = null;
  try {{ saved = localStorage.getItem(key); }} catch (e) {{}}
  var initial = (saved === "advanced" || saved === "basic") ? saved : "basic";
  document.body.setAttribute("data-mode", initial);
  function setMode(mode) {{
    document.body.setAttribute("data-mode", mode);
    try {{ localStorage.setItem(key, mode); }} catch (e) {{}}
    document.querySelectorAll("#mode-toggle button").forEach(function (b) {{
      b.classList.toggle("active", b.getAttribute("data-target-mode") === mode);
    }});
  }}
  document.querySelectorAll("#mode-toggle button").forEach(function (b) {{
    b.addEventListener("click", function () {{ setMode(b.getAttribute("data-target-mode")); }});
  }});
  setMode(initial);
}})();
</script>
{common_script_tag}
</body>
</html>
"""


def render_command_center_html():
    intro = """
  <section class="basic-only">
    <div class="card notice">
      <h3>What is this?</h3>
      <p>Dwarf is a forensic Cardano testing framework. It feeds adversarial inputs to Amaru and cardano-node parsers and consensus code, records every test as a tamper-evident evidence bundle, and lets you run the same test against both implementations to find behavioural divergences.</p>
      <p>Every test produces a forensic bundle that is reproducible, signable, and replayable. Nothing here changes the live Cardano network &mdash; this is a read-only, isolated test harness.</p>
      <p><a href="/tests"><strong>Run a test &rarr;</strong></a> &middot; <a href="/operate/compare">Compare implementations</a></p>
    </div>
  </section>
  <section class="adv-only">
    <div class="card notice">
      <h3>Operator surface</h3>
      <p>Library-tier scenarios run via <code>cardano-profile scenario run &lt;path&gt;</code>; cross-impl comparison via <code>cardano-profile compare &lt;path&gt;</code>; bundles verifiable via <code>cardano-profile verify &lt;run-id&gt;</code>. Every mutating endpoint requires the dashboard token and is serialised by a global lock. <a href="/operate/runs">Recent runs</a> &middot; <a href="/operate/compare">Compare</a> &middot; <a href="/operate/status">Substrate status</a>.</p>
    </div>
  </section>
"""
    body = intro + _target_live_cards() + """
  <section>
    <h2>Actions</h2>
    <p class="small basic-only">Start or stop the Cardano test environment. Each action asks for typed confirmation and streams a live log below.</p>
    <p class="small adv-only">Each button runs the equivalent <code>cardano-profile</code> CLI command with <code>--approve</code>. The server serializes mutating actions; a second simultaneous action returns 409. A forensic bundle is produced for every run.</p>
    <div class="action-row">
      <button id="deploy-button" class="action-button action-deploy" data-action="deploy"
              data-profile="profile-a-haskell-peersharing-disabled">
        <span class="basic-only">Start the test environment</span>
        <span class="adv-only">cardano-profile deploy profile-a-haskell-peersharing-disabled --approve</span>
      </button>
      <button id="remove-button" class="action-button action-remove" data-action="remove">
        <span class="basic-only">Stop the test environment</span>
        <span class="adv-only">cardano-profile remove --approve</span>
      </button>
    </div>
    <pre id="action-log" class="action-log">No action running. Output will appear here live.</pre>
  </section>

  <div id="action-modal" class="modal" hidden>
    <div class="modal-card">
      <h3 id="action-modal-title">Confirm</h3>
      <p id="action-modal-body"></p>
      <pre id="action-modal-command" class="adv-only"></pre>
      <label for="action-modal-input">Type the confirmation word to proceed:</label>
      <input id="action-modal-input" type="text" autocomplete="off" spellcheck="false">
      <div class="modal-row">
        <button id="action-modal-cancel" type="button">Cancel</button>
        <button id="action-modal-confirm" type="button" disabled>Confirm</button>
      </div>
    </div>
  </div>

  <section>
    <h2>Command Center</h2>
    <svg class="viz viz-compact" id="status-gauge-svg" viewBox="0 0 860 170" role="img" aria-label="Live runtime gauges"></svg>
    <div class="status-strip" id="status-strip"></div>
  </section>

  <section>
    <h2>Deployment Flow</h2>
    <svg class="viz viz-compact" id="deployment-flow-svg" viewBox="0 0 920 260" role="img" aria-label="Deployment topology map"></svg>
    <div class="flow" id="deployment-flow">
      <div class="flow-step"><strong>Browser / CLI</strong>Local operator view and command entry point.</div>
      <div class="flow-step"><strong>SSH</strong>Read-only health polling and explicit CLI operations.</div>
      <div class="flow-step"><strong>cardano-box</strong>Ubuntu host for local Cardano profile runtime.</div>
      <div class="flow-step"><strong>Profile Runtime</strong>Managed local testnet profile under `/home/nigel/cardano-profiles`.</div>
      <div class="flow-step"><strong>node1 / node2 / node3</strong>Loopback node-to-node listeners, sockets, logs, and DB state.</div>
    </div>
  </section>

  <section>
    <h2>Current Deployment</h2>
    <div class="table-wrap" id="deployment-table"></div>
  </section>

  <section>
    <h2>Next Actions</h2>
    <div class="table-wrap" id="next-actions-table"></div>
  </section>
"""
    script = """
const ACTION_CONFIRM_WORDS = { deploy: "START", remove: "STOP" };
function actionModal(action, profile, mode) {
  const modal = document.getElementById("action-modal");
  const title = document.getElementById("action-modal-title");
  const bodyEl = document.getElementById("action-modal-body");
  const cmdEl = document.getElementById("action-modal-command");
  const input = document.getElementById("action-modal-input");
  const confirmBtn = document.getElementById("action-modal-confirm");
  const cancelBtn = document.getElementById("action-modal-cancel");
  const word = ACTION_CONFIRM_WORDS[action];
  if (action === "deploy") {
    title.textContent = "Start the test environment";
    bodyEl.innerHTML = `This will deploy <code>${esc(profile)}</code> to the test host. It can take a few minutes and replaces any active devnet. Type <strong>${word}</strong> to confirm.`;
    cmdEl.textContent = `cardano-profile deploy ${profile} --approve`;
  } else {
    title.textContent = "Stop the test environment";
    bodyEl.innerHTML = `This will stop active Cardano sessions and archive the runtime directory. Type <strong>${word}</strong> to confirm.`;
    cmdEl.textContent = `cardano-profile remove --approve`;
  }
  input.value = "";
  confirmBtn.disabled = true;
  modal.hidden = false;
  input.focus();
  return new Promise((resolve) => {
    function close(ok) {
      modal.hidden = true;
      input.oninput = null;
      confirmBtn.onclick = null;
      cancelBtn.onclick = null;
      resolve(ok);
    }
    input.oninput = () => { confirmBtn.disabled = input.value !== word; };
    confirmBtn.onclick = () => close(true);
    cancelBtn.onclick = () => close(false);
  });
}
function runAction(action, profile) {
  const log = document.getElementById("action-log");
  const deployBtn = document.getElementById("deploy-button");
  const removeBtn = document.getElementById("remove-button");
  deployBtn.disabled = true;
  removeBtn.disabled = true;
  log.textContent = "";
  const url = action === "deploy"
    ? `/api/deploy?token=${encodeURIComponent(getToken())}&profile=${encodeURIComponent(profile)}`
    : `/api/remove?token=${encodeURIComponent(getToken())}`;
  fetch(url, { method: "POST" }).then(async (response) => {
    if (!response.ok) {
      const text = await response.text();
      log.textContent = `HTTP ${response.status}: ${text}`;
      return;
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const parts = buf.split("\\n\\n");
      buf = parts.pop();
      for (const part of parts) {
        const lines = part.split("\\n");
        for (const line of lines) {
          if (line.startsWith("data: ")) {
            log.textContent += line.slice(6) + "\\n";
            log.scrollTop = log.scrollHeight;
          } else if (line.startsWith("event: done")) {
            log.textContent += "[done]\\n";
          }
        }
      }
    }
  }).catch((err) => {
    log.textContent += "\\n[error] " + String(err);
  }).finally(() => {
    deployBtn.disabled = false;
    removeBtn.disabled = false;
    refresh();
  });
}
function getToken() {
  const params = new URLSearchParams(window.location.search);
  return params.get("token") || "dwarf";
}
function bindActionButtons() {
  document.querySelectorAll(".action-button").forEach((btn) => {
    if (btn.dataset.bound) return;
    btn.dataset.bound = "1";
    btn.addEventListener("click", async () => {
      const action = btn.dataset.action;
      const profile = btn.dataset.profile || "";
      const ok = await actionModal(action, profile, document.body.getAttribute("data-mode"));
      if (ok) runAction(action, profile);
    });
  });
}
function render(payload) {
  renderTargetAndLive(payload);
  bindActionButtons();
  const health = liveHealth(payload);
  const parsed = health.parsed || {};
  const active = activeProfile(payload);
  drawStatusGauges(parsed);
  drawDeploymentFlow(payload, active, parsed);
  const metrics = [
    ["Profile", active.id],
    ["Node Type", active.node_type],
    ["Node Count", active.node_count],
    ["PeerSharing", active.peer_sharing ? "enabled" : "disabled"],
    ["Node Processes", parsed.cardano_node_processes],
    ["Tip Block", parsed.tip_block],
    ["Sync", parsed.sync_progress],
    ["Loopback Only", parsed.loopback_only],
  ];
  document.getElementById("status-strip").innerHTML = metrics.map(([label, value]) =>
    `<div class="status-tile"><span>${esc(label)}</span><strong>${esc(value ?? "unknown")}</strong></div>`
  ).join("");
  document.getElementById("deployment-table").innerHTML = table(
    ["Field", "Value"],
    [
      ["Runtime Root", code((payload.live || {}).runtime_root || active.remote_runtime_root || "unknown")],
      ["Target", `${esc((payload.config || {}).ssh_user || "unknown")}@${esc((payload.config || {}).host || "unknown")}`],
    ]
  );
  document.getElementById("next-actions-table").innerHTML = table(
    ["Action", "CLI"],
    (payload.commands || []).slice(0, 5).map(c => [esc(c.label), code(c.command)])
  );
}
function stateColor(value, expected) {
  return String(value) === String(expected) ? "ok" : "warn";
}
function drawStatusGauges(parsed) {
  const gauges = [
    ["Processes", parsed.cardano_node_processes, "3", 80],
    ["Sockets", parsed.socket_count, "3", 260],
    ["Listeners", parsed.listener_count, "3", 440],
    ["Sync", parsed.sync_progress, "100.00", 620],
  ];
  document.getElementById("status-gauge-svg").innerHTML = `
    <defs><marker id="arrowhead" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto"><path d="M0,0 L0,6 L8,3 z" fill="#6b7b88"/></marker></defs>
    ${gauges.map(([label, value, expected, x]) => `<g>
      <circle class="${stateColor(value, expected)}" cx="${x}" cy="70" r="46"></circle>
      <text x="${x}" y="66" text-anchor="middle" font-size="22" font-weight="700">${esc(value ?? "unknown")}</text>
      <text class="muted" x="${x}" y="91" text-anchor="middle">${esc(label)}</text>
      <text class="muted" x="${x}" y="132" text-anchor="middle">expected ${esc(expected)}</text>
    </g>`).join("")}
  `;
}
function drawDeploymentFlow(payload, active, parsed) {
  const processClass = stateColor(parsed.cardano_node_processes, active.node_count || 3);
  document.getElementById("deployment-flow-svg").innerHTML = `
    <defs><marker id="arrowhead" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto"><path d="M0,0 L0,6 L8,3 z" fill="#6b7b88"/></marker></defs>
    <rect class="node" x="24" y="86" width="130" height="66" rx="8"/><text x="89" y="114" text-anchor="middle" font-weight="700">Browser / CLI</text><text class="muted" x="89" y="136" text-anchor="middle">operator</text>
    <line class="edge" x1="154" y1="119" x2="238" y2="119"/>
    <rect class="node" x="248" y="86" width="110" height="66" rx="8"/><text x="303" y="114" text-anchor="middle" font-weight="700">SSH</text><text class="muted" x="303" y="136" text-anchor="middle">read-only poll</text>
    <line class="edge" x1="358" y1="119" x2="438" y2="119"/>
    <rect class="node" x="448" y="66" width="146" height="106" rx="8"/><text x="521" y="102" text-anchor="middle" font-weight="700">cardano-box</text><text class="muted" x="521" y="126" text-anchor="middle">${esc((payload.config || {}).host || "unknown")}</text><text class="muted" x="521" y="150" text-anchor="middle">Ubuntu host</text>
    <line class="edge" x1="594" y1="119" x2="674" y2="119"/>
    <rect class="node" x="684" y="46" width="190" height="146" rx="8"/><text x="779" y="76" text-anchor="middle" font-weight="700">${esc(active.id || "active profile")}</text>
    <circle class="${processClass}" cx="729" cy="124" r="22"/><text x="729" y="130" text-anchor="middle">n1</text>
    <circle class="${processClass}" cx="779" cy="124" r="22"/><text x="779" y="130" text-anchor="middle">n2</text>
    <circle class="${processClass}" cx="829" cy="124" r="22"/><text x="829" y="130" text-anchor="middle">n3</text>
    <text class="muted" x="779" y="170" text-anchor="middle">${esc(parsed.cardano_node_processes ?? "unknown")} live node processes</text>
  `;
}
"""
    return _page("Command Center", "/", body, script)






def _deliverable_download_cell(relative):
    if not relative:
        return '<span class="download-link missing">Not available yet</span>'
    filename = Path(relative).name
    pdf_filename = f"{Path(relative).stem}.pdf"
    return (
        f'<a class="download-link" href="{_escape(_download_url(relative))}" download="{_escape(filename)}">'
        f'Download {_escape(filename)}</a>'
        f'<a class="download-link secondary" href="{_escape(_pdf_url(relative))}" download="{_escape(pdf_filename)}">'
        f'Download PDF</a>'
        f'<div class="deliverable-path">{_escape(relative)}</div>'
    )




def _recent_runs_table_html():
    payload = recent_runs_payload(limit=20)
    runs = payload["recent_runs"]
    if not runs:
        return '<p class="small">No forensic runs recorded yet. Run a fuzz, smoke, evidence, or package command from the CLI to populate this list.</p>'
    rows = []
    for r in runs:
        rs = r.get("resource_snapshot") or {}
        rss = (rs.get("process_rss") or {}).get("delta_bytes")
        wall = rs.get("wall_time_seconds")
        source = r.get("source") or "local"
        is_local = source == "local"
        run_id = r["run_id"]
        # Local runs link to the inspector; remote runs link only to the source description.
        rid_cell = (
            f"<a href=\"/runs/{_escape(run_id)}\"><code>{_escape(run_id)}</code></a>"
            if is_local
            else f"<code>{_escape(run_id)}</code>"
        )
        actions_cell = (
            f"<a href=\"/runs/{_escape(run_id)}\">view</a> &middot; "
            f"<a href=\"/runs/{_escape(run_id)}/bundle\">bundle</a>"
            if is_local
            else f"<span class=\"small\">on <strong>{_escape(source)}</strong></span>"
        )
        rows.append(
            "<tr>"
            f"<td>{rid_cell}</td>"
            f"<td>{_escape(r.get('scenario_id') or '')}</td>"
            f"<td>{_escape(source)}</td>"
            f"<td>{_escape(r.get('runtime') or '')}</td>"
            f"<td>{_escape(r.get('exit_status') or '')}</td>"
            f"<td>{_escape(r.get('ended_at') or '')}</td>"
            f"<td>{_escape(wall if wall is not None else '')}</td>"
            f"<td>{_escape(rss if rss is not None else '')}</td>"
            f"<td>{actions_cell}</td>"
            "</tr>"
        )
    return (
        "<div class=\"table-wrap\"><table><thead><tr>"
        "<th>Run ID</th><th>Scenario</th><th>Source</th><th>Runtime</th><th>Result</th>"
        "<th>Ended</th><th>Wall (s)</th><th>RSS Δ (bytes)</th><th>Open</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )


DEFAULT_TOKEN = "dwarf"


import re as _re


import threading as _threading

_MUTATING_LOCK = _threading.Lock()


def try_acquire_mutating_lock():
    """Non-blocking attempt to acquire the global mutating-action lock.

    Returns True if acquired (caller must release), False if another mutating action is in progress.
    """
    return _MUTATING_LOCK.acquire(blocking=False)


def release_mutating_lock():
    try:
        _MUTATING_LOCK.release()
    except RuntimeError:
        pass


def stream_subprocess_sse(cmd, *, env=None, cwd=None):
    """Spawn a subprocess and yield SSE-formatted bytes chunks.

    Each stdout line becomes a `data: <line>\\n\\n` event. When the process exits, a final
    `event: done\\ndata: {"exit_code": N}\\n\\n` is emitted. stderr is merged into the stream
    so operators see everything the process said.
    """
    import subprocess as _sub
    proc = _sub.Popen(
        cmd,
        stdout=_sub.PIPE,
        stderr=_sub.STDOUT,
        env=env,
        cwd=cwd,
        text=True,
        bufsize=1,
    )
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            yield ("data: " + line.rstrip("\n") + "\n\n").encode("utf-8")
        proc.wait()
    finally:
        if proc.stdout is not None:
            proc.stdout.close()
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except _sub.TimeoutExpired:
                proc.kill()
    exit_code = proc.returncode if proc.returncode is not None else -1
    yield ("event: done\ndata: " + json.dumps({"exit_code": exit_code}) + "\n\n").encode("utf-8")


def resolve_token(*, token=None):
    """Pick the active token from arg / env / default.

    Precedence: explicit `token=` argument > ADA2_DWARF_TOKEN env var > "dwarf".
    """
    if token:
        return token
    env = os.environ.get("ADA2_DWARF_TOKEN")
    if env:
        return env
    return DEFAULT_TOKEN


def check_token(path, *, expected):
    """Validate that the given request path has ?token=<expected>.

    Returns (ok, error_message_or_None). Constant-time comparison.
    """
    import hmac
    from urllib.parse import urlsplit, parse_qs
    parts = urlsplit(path)
    qs = parse_qs(parts.query, keep_blank_values=True)
    provided = qs.get("token", [None])[0]
    if not provided:
        return (False, "missing token query parameter")
    if not hmac.compare_digest(provided, expected):
        return (False, "invalid token")
    return (True, None)


DEFAULT_CLI_ENTRYPOINT = Path(__file__).resolve().parents[1] / "cardano-profile"


def _default_cli_command_builder(action, *, profile=None, test_id=None, approve=False, scenario_path=None):
    entry = str(DEFAULT_CLI_ENTRYPOINT)
    if action == "deploy":
        if not profile:
            raise ValueError("deploy requires profile")
        return [entry, "deploy", profile, "--approve"]
    if action == "remove":
        return [entry, "remove", "--approve"]
    if action == "fuzz":
        if not test_id:
            raise ValueError("fuzz requires id")
        cmd = [entry, "fuzz", "run", test_id]
        if approve:
            cmd.append("--approve")
        return cmd
    if action == "smoke":
        if not test_id:
            raise ValueError("smoke requires id")
        return [entry, "test", "smoke", "run", test_id]
    if action == "compare":
        if not scenario_path:
            raise ValueError("compare requires path")
        return [entry, "compare", scenario_path]
    if action == "scenario_run":
        if not scenario_path:
            raise ValueError("scenario_run requires path")
        return [entry, "scenario", "run", scenario_path]
    raise ValueError(f"unknown action: {action}")


_MUTATING_ROUTES = {
    "/api/deploy": "deploy",
    "/api/remove": "remove",
    "/api/fuzz/run": "fuzz",
    "/api/test/smoke/run": "smoke",
    "/api/scenario/run": "scenario_run",
    "/api/scenario/compare": "compare",
}


def dispatch_schedule_request(*, method, path, body, expected_token,
                              cli_command_builder=None):
    """Item #19 — handle POST /api/schedule/* endpoints.

    Routes:
      POST /api/schedule/create        (form body: name, scenario_id, scenario_path, cron)
      POST /api/schedule/<id>/pause
      POST /api/schedule/<id>/resume
      POST /api/schedule/<id>/run-now
      POST /api/schedule/<id>/delete

    Returns (status, content_type, body_bytes) or None when path doesn't
    match. All endpoints are token-gated and POST-only; pause/resume/
    delete are idempotent (return 200 even on a no-op).
    """
    from urllib.parse import urlsplit, parse_qs
    from profile_manager.data import schedule_store
    parts = urlsplit(path)
    clean = parts.path
    if not clean.startswith("/api/schedule"):
        return None
    if method != "POST":
        return (405, "text/plain; charset=utf-8", b"method not allowed\n")
    ok, err = check_token(path, expected=expected_token)
    if not ok:
        return (401, "text/plain; charset=utf-8", (err + "\n").encode("utf-8"))

    if clean == "/api/schedule/create":
        form = parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True) if body else {}
        name = (form.get("name") or [""])[0]
        scenario_id = (form.get("scenario_id") or [""])[0]
        scenario_path = (form.get("scenario_path") or [""])[0]
        cron = (form.get("cron") or [""])[0]
        try:
            entry = schedule_store.create_entry(
                name=name, scenario_id=scenario_id,
                scenario_path=scenario_path, cron=cron,
            )
        except ValueError as exc:
            return (400, "text/plain; charset=utf-8", (str(exc) + "\n").encode("utf-8"))
        return (200, "application/json; charset=utf-8",
                json.dumps({"ok": True, "entry": entry}).encode("utf-8"))

    # /api/schedule/<id>/<action>
    rest = clean[len("/api/schedule/"):]
    if "/" not in rest:
        return (404, "text/plain; charset=utf-8", b"not found\n")
    entry_id, _, action = rest.partition("/")
    if not entry_id or "/" in entry_id or ".." in entry_id:
        return (400, "text/plain; charset=utf-8", b"invalid entry id\n")
    if action == "pause":
        result = schedule_store.pause_entry(entry_id)
    elif action == "resume":
        result = schedule_store.resume_entry(entry_id)
    elif action == "delete":
        result = {"deleted": schedule_store.delete_entry(entry_id)}
    elif action == "run-now":
        entry = schedule_store.get_entry(entry_id)
        if entry is None:
            return (404, "text/plain; charset=utf-8", b"unknown entry\n")
        # Synchronous fire — uses the same builder dispatch_mutating_request
        # would use, with the global mutating lock honored.
        if not try_acquire_mutating_lock():
            return (409, "text/plain; charset=utf-8",
                    b"another mutating action is already in progress\n")
        try:
            from profile_manager.data import scheduler
            builder = cli_command_builder or _default_cli_command_builder
            updated = scheduler.fire_entry(entry, command_builder=builder)
            result = updated
        finally:
            release_mutating_lock()
    else:
        return (404, "text/plain; charset=utf-8", b"unknown action\n")
    if result is None:
        return (404, "text/plain; charset=utf-8", b"unknown entry\n")
    return (200, "application/json; charset=utf-8",
            json.dumps({"ok": True, "result": result}).encode("utf-8"))


def dispatch_mutating_request(*, method, path, expected_token, cli_command_builder=None):
    """Dispatch a mutating HTTP request.

    Returns (status, content_type, body_or_generator) or None if the path isn't a mutating route.
    For the SSE path the body is a bytes generator; callers must iterate it.
    """
    from urllib.parse import urlsplit, parse_qs
    parts = urlsplit(path)
    clean = parts.path
    action = _MUTATING_ROUTES.get(clean)
    if action is None:
        return None
    if method != "POST":
        return (405, "text/plain; charset=utf-8", b"method not allowed\n")
    ok, err = check_token(path, expected=expected_token)
    if not ok:
        return (401, "text/plain; charset=utf-8", (err + "\n").encode("utf-8"))
    qs = parse_qs(parts.query, keep_blank_values=True)
    profile = (qs.get("profile") or [None])[0]
    test_id = (qs.get("id") or [None])[0]
    scenario_path = (qs.get("path") or [None])[0]
    approve_raw = (qs.get("approve") or [None])[0]
    approve = approve_raw in ("1", "true", "yes")
    if action == "deploy" and not profile:
        return (400, "text/plain; charset=utf-8", b"missing profile query parameter\n")
    if action in ("fuzz", "smoke") and not test_id:
        return (400, "text/plain; charset=utf-8", b"missing id query parameter\n")
    if action in ("compare", "scenario_run") and not scenario_path:
        return (400, "text/plain; charset=utf-8", b"missing path query parameter\n")
    if not try_acquire_mutating_lock():
        return (409, "text/plain; charset=utf-8", b"another mutating action is already in progress\n")
    builder = cli_command_builder or _default_cli_command_builder
    try:
        cmd = builder(action, profile=profile, test_id=test_id, approve=approve, scenario_path=scenario_path)
    except Exception as exc:
        release_mutating_lock()
        return (400, "text/plain; charset=utf-8", (f"bad request: {exc}\n").encode("utf-8"))

    def _gen():
        try:
            yield from stream_subprocess_sse(cmd)
        finally:
            release_mutating_lock()
    return (200, "text/event-stream; charset=utf-8", _gen())


_STATIC_CONTENT_TYPES = {
    ".css":   "text/css; charset=utf-8",
    ".js":    "application/javascript; charset=utf-8",
    ".woff2": "font/woff2",
    ".woff":  "font/woff",
    ".png":   "image/png",
    ".svg":   "image/svg+xml",
    ".ico":   "image/x-icon",
    ".txt":   "text/plain; charset=utf-8",
    ".html":  "text/html; charset=utf-8",
    ".json":  "application/json; charset=utf-8",
}


def dispatch_static_request(path):
    """Dispatch a /static/<subpath> GET. Returns (status, ctype, body) or None.

    Pure function over the request path so it is unit-testable without a server.
    Rejects traversal and absolute-path subpaths with 400 before resolving;
    follows up with a containment check after resolving symlinks. Unknown
    file suffixes fall through to application/octet-stream.
    """
    if not path.startswith("/static/"):
        return None
    subpath = path[len("/static/"):]
    if not subpath or ".." in subpath or subpath.startswith("/"):
        return (400, "text/plain; charset=utf-8", b"invalid path\n")
    static_root = (DASHBOARD_ROOT / "static").resolve()
    target = (static_root / subpath).resolve()
    if static_root not in target.parents and target != static_root:
        return (400, "text/plain; charset=utf-8", b"invalid path\n")
    if not target.is_file():
        return (404, "text/plain; charset=utf-8", b"not found\n")
    ctype = _STATIC_CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream")
    return (200, ctype, target.read_bytes())


def dispatch_scenario_request(*, method, path, body, expected_token, scenarios_dir=None):
    """Handle /api/scenario/paste and /api/scenario/promote.

    Pure function over (method, path, body) so it's unit-testable without a server.
    Returns (status, content_type, body_bytes) or None when path doesn't match.
    """
    from urllib.parse import urlsplit, parse_qs
    from profile_manager import scenario as scen
    parts = urlsplit(path)
    clean = parts.path
    if clean not in ("/api/scenario/paste", "/api/scenario/promote",
                     "/api/scenario/validate", "/api/scenario/save"):
        return None
    if method != "POST":
        return (405, "text/plain; charset=utf-8", b"method not allowed\n")
    ok, err = check_token(path, expected=expected_token)
    if not ok:
        return (401, "text/plain; charset=utf-8", (err + "\n").encode("utf-8"))
    scenarios_dir = Path(scenarios_dir) if scenarios_dir is not None else _scenarios_dir()
    if clean == "/api/scenario/validate":
        # Item #15 — read-only validation endpoint for the editor.
        report = scen.validate_scenario_body(body or b"")
        # Always return 200 — the body carries the structured errors,
        # so the client can render them inline without juggling 4xx.
        return (200, "application/json; charset=utf-8",
                json.dumps(report).encode("utf-8"))
    if clean == "/api/scenario/save":
        # Item #15 — validate, then persist to <scenarios_dir>/<id>.yaml.
        report = scen.save_scenario_body(body or b"", scenarios_dir=scenarios_dir)
        status = 200 if report.get("ok") else 400
        return (status, "application/json; charset=utf-8",
                json.dumps(report).encode("utf-8"))
    if clean == "/api/scenario/paste":
        report = scen.handle_paste(body or b"", scenarios_dir=scenarios_dir)
        status = 200 if report.get("ok") else 400
        return (status, "application/json; charset=utf-8", json.dumps(report).encode("utf-8"))
    qs = parse_qs(parts.query, keep_blank_values=True)
    sid = (qs.get("id") or [None])[0]
    report = scen.handle_promote(sid or "", scenarios_dir=scenarios_dir)
    if not report.get("ok"):
        return (404, "application/json; charset=utf-8", json.dumps(report).encode("utf-8"))
    return (200, "application/json; charset=utf-8", json.dumps(report).encode("utf-8"))


def dispatch_api_request(path, *, runs_dir=None, bundles_dir=None):
    """Return (status, content_type, body_bytes) for API routes, or None if path isn't an API route.

    Separated from the HTTP handler so it can be unit-tested without starting a server.
    """
    from profile_manager import forensic
    runs_dir = Path(runs_dir) if runs_dir is not None else _forensic_runs_dir()
    bundles_dir = Path(bundles_dir) if bundles_dir is not None else _forensic_bundles_dir()
    parsed = urlsplit(path)

    if parsed.path == "/deliverables/download":
        values = parse_qs(parsed.query).get("path", [])
        if not values:
            return (400, "text/plain; charset=utf-8", b"missing path\n")
        target, error = _safe_project_file(values[0])
        if error:
            return error
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix.lower() == ".docx":
            content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        body = target.read_bytes()
        return (200, content_type, body, _attachment_headers(target.name))

    if parsed.path == "/deliverables/pdf":
        values = parse_qs(parsed.query).get("path", [])
        if not values:
            return (400, "text/plain; charset=utf-8", b"missing path\n")
        target, error = _safe_project_file(values[0])
        if error:
            return error
        body, conversion_error = _convert_project_file_to_pdf(target)
        if conversion_error:
            return (503, "text/plain; charset=utf-8", conversion_error.encode("utf-8"))
        return (200, "application/pdf", body, _attachment_headers(f"{target.stem}.pdf"))

    if path == "/api/runs":
        payload = {"recent_runs": forensic.list_recent_runs(runs_dir=runs_dir, limit=50)}
        body = json.dumps(payload, indent=2).encode("utf-8")
        return (200, "application/json; charset=utf-8", body)

    if path.startswith("/runs/") and path.endswith("/bundle"):
        run_id = path[len("/runs/"):-len("/bundle")]
        if not run_id or "/" in run_id or ".." in run_id:
            return (400, "text/plain; charset=utf-8", b"invalid run id\n")
        run_dir = runs_dir / run_id
        if not run_dir.is_dir():
            return (404, "text/plain; charset=utf-8", b"not found\n")
        bundle_path = forensic.export_bundle(run_id, runs_dir=runs_dir, bundles_dir=bundles_dir)
        body = bundle_path.read_bytes()
        return (200, "application/gzip", body)

    # Slice 30: per-artifact download. /runs/<id>/output?path=outputs/...
    # serves a single file from inside the bundle so SARIF / attestation
    # JSON / replay result.json can be linked from the inspector page.
    if parsed.path.startswith("/runs/") and parsed.path.endswith("/output"):
        prefix = "/runs/"
        run_id = parsed.path[len(prefix):-len("/output")]
        if not run_id or "/" in run_id or ".." in run_id:
            return (400, "text/plain; charset=utf-8", b"invalid run id\n")
        relpath_values = parse_qs(parsed.query).get("path", [])
        if not relpath_values:
            return (400, "text/plain; charset=utf-8", b"missing path\n")
        relpath = relpath_values[0]
        if not relpath or relpath.startswith("/") or ".." in Path(relpath).parts:
            return (400, "text/plain; charset=utf-8", b"invalid path\n")
        run_dir = (runs_dir / run_id).resolve()
        if not run_dir.is_dir():
            return (404, "text/plain; charset=utf-8", b"not found\n")
        target = (run_dir / relpath).resolve()
        if run_dir != target and run_dir not in target.parents:
            return (400, "text/plain; charset=utf-8", b"path escapes run dir\n")
        if not target.is_file():
            return (404, "text/plain; charset=utf-8", b"not found\n")
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix.lower() == ".sarif":
            ctype = "application/sarif+json"
        body = target.read_bytes()
        return (200, ctype, body, _attachment_headers(target.name))

    return None


def _recent_runs_summary_basic():
    payload = recent_runs_payload(limit=20)
    runs = payload["recent_runs"]
    if not runs:
        return ('<p>No tests have been run yet. Open <a href="/tests">Tests &amp; Evidence</a> '
                'to run one.</p>')
    total = len(runs)
    passed = sum(1 for r in runs if r.get("exit_status") == "pass")
    failed = sum(1 for r in runs if r.get("exit_status") == "fail")
    last = runs[0]
    summary = _basic_run_summary(last)
    headline = (
        f'<p>{passed} of {total} recent tests passed'
        + (f', {failed} failed' if failed else '')
        + '. ' + ('No crashes or panics in any of them. ' if failed == 0 else '')
        + f'<a href="/runs/{_escape(last.get("run_id"))}">Latest test &rarr;</a></p>'
    )
    detail = f'<p>{summary}</p>' if summary else ''
    return (
        headline + detail +
        '<p class="small">Switch to Advanced view to see the full evidence table and download bundles.</p>'
    )


def _basic_run_summary(run_entry):
    """One-sentence plain-English summary of a single run for non-experts."""
    scenario_id = run_entry.get("scenario_id") or ""
    runtime = run_entry.get("runtime") or ""
    exit_status = run_entry.get("exit_status")
    impl_human, parser_human = _humanize_scenario_id(scenario_id)
    target_phrase = (
        f"<strong>{_escape(impl_human)}</strong>'s <strong>{_escape(parser_human)}</strong> parser"
        if impl_human and parser_human
        else f"scenario <code>{_escape(scenario_id)}</code>"
    )
    outcome_phrase = {
        "pass": "all inputs were rejected cleanly with no crashes",
        "fail": "at least one input crashed or behaved unexpectedly &mdash; see the bundle",
        "error": "the test runner errored before producing a verdict",
    }.get(exit_status, "outcome unknown")
    return (
        f"Latest run tested {target_phrase} (runtime: <code>{_escape(runtime)}</code>) &mdash; "
        f"{outcome_phrase}."
    )


def render_tests_html():
    runs_section = (
        '<section class="basic-only" id="recent-runs-summary">'
        '<h2>Recent Tests</h2>' + _recent_runs_summary_basic() +
        '</section>'
        '<section class="adv-only" id="recent-runs-detailed">'
        '<h2>Recent Runs</h2>'
        '<p class="small">Forensic bundles produced by every fuzz, smoke, evidence, and package run. '
        'Each row is a self-contained tamper-evident evidence package; click <em>bundle</em> to download a tar.gz.</p>'
        + _recent_runs_table_html() +
        '</section>'
    )
    body = _target_live_cards() + runs_section + """
  <section class="adv-only">
    <h2>Tests & Evidence</h2>
    <svg class="viz viz-compact" id="test-pipeline-svg" viewBox="0 0 940 270" role="img" aria-label="Test and evidence pipeline"></svg>
    <div class="flow" id="test-pipeline">
      <div class="flow-step"><strong>Evidence Packages</strong>A/B/C/D candidate coverage and blockers.</div>
      <div class="flow-step"><strong>Smoke Tests</strong>Bounded repeatable checks.</div>
      <div class="flow-step"><strong>Fuzz Tests</strong>Safe offline and approval-required live tests.</div>
      <div class="flow-step"><strong>Review Gate</strong>No accepted risks or findings without human approval.</div>
    </div>
    <div class="table-wrap" id="packages-table"></div>
  </section>

  <section class="adv-only">
    <h2>Smoke Tests</h2>
    <div class="table-wrap" id="smoke-table"></div>
  </section>

  <section class="adv-only">
    <h2>Fuzz Tests</h2>
    <div class="table-wrap" id="fuzz-table"></div>
  </section>

  <section class="adv-only">
    <h2>Run Output</h2>
    <pre id="run-log" class="action-log">No run in progress.</pre>
  </section>

  <section class="adv-only">
    <h2>Issue Families</h2>
    <div class="status-strip" id="lifecycle-summary-strip"></div>
    <h3>Runtime Anomalies</h3>
    <div class="table-wrap" id="lifecycle-runtime-buckets-table"></div>
    <div class="table-wrap" id="lifecycle-runtime-cases-table"></div>
    <h3>Fuzz Queue Families</h3>
    <div class="table-wrap" id="lifecycle-fuzz-buckets-table"></div>
    <div class="table-wrap" id="lifecycle-fuzz-cases-table"></div>
  </section>

  <section>
    <h2>Paste a scenario</h2>
    <p class="small basic-only">Paste a scenario YAML to add it to the framework. The server will check it for you and (if valid) you can promote it into the runnable corpus.</p>
    <p class="small adv-only">POST /api/scenario/paste writes to dwarf/scenarios/pending/&lt;id&gt;.yaml and re-validates against spec/v1/schema.json. POST /api/scenario/promote moves a pending scenario into dwarf/scenarios/.</p>
    <textarea id="paste-textarea" rows="14" placeholder='{ "spec_version": "v1", "id": "your-id", ... }'></textarea>
    <div class="action-row">
      <button id="paste-validate-button" class="action-button">Validate</button>
      <button id="paste-promote-button" class="action-button" disabled>Promote</button>
    </div>
    <pre id="paste-report" class="action-log">No paste in progress.</pre>
  </section>

  <section class="adv-only">
    <h2>Latest Evidence Feed</h2>
    <div class="table-wrap" id="evidence-table"></div>
    <p class="small">Evidence remains candidate-only unless a separate human-approved promotion/scoring pass says otherwise.</p>
  </section>
"""
    script = """
function getToken() {
  const params = new URLSearchParams(window.location.search);
  return params.get("token") || "dwarf";
}
function streamRun(url, log) {
  log.textContent = "";
  fetch(url, { method: "POST" }).then(async (response) => {
    if (!response.ok) {
      log.textContent = `HTTP ${response.status}: ${await response.text()}`;
      return;
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const parts = buf.split("\\n\\n");
      buf = parts.pop();
      for (const part of parts) {
        for (const line of part.split("\\n")) {
          if (line.startsWith("data: ")) {
            log.textContent += line.slice(6) + "\\n";
            log.scrollTop = log.scrollHeight;
          } else if (line.startsWith("event: done")) {
            log.textContent += "[done]\\n";
          }
        }
      }
    }
  }).catch((err) => { log.textContent += "\\n[error] " + String(err); })
    .finally(() => { refresh(); });
}
let _pastedScenarioId = null;
function bindPasteButtons() {
  const validateBtn = document.getElementById("paste-validate-button");
  const promoteBtn = document.getElementById("paste-promote-button");
  const textarea = document.getElementById("paste-textarea");
  const report = document.getElementById("paste-report");
  if (!validateBtn || validateBtn.dataset.bound) return;
  validateBtn.dataset.bound = "1";
  validateBtn.addEventListener("click", async () => {
    promoteBtn.disabled = true;
    _pastedScenarioId = null;
    report.textContent = "Validating...";
    try {
      const res = await fetch(`/api/scenario/paste?token=${encodeURIComponent(getToken())}`,
                              { method: "POST", body: textarea.value });
      const data = await res.json();
      report.textContent = JSON.stringify(data, null, 2);
      if (data.ok) {
        _pastedScenarioId = data.scenario_id;
        promoteBtn.disabled = false;
      }
    } catch (err) {
      report.textContent = "[error] " + String(err);
    }
  });
  promoteBtn.addEventListener("click", async () => {
    if (!_pastedScenarioId) return;
    promoteBtn.disabled = true;
    report.textContent = "Promoting " + _pastedScenarioId + "...";
    try {
      const res = await fetch(`/api/scenario/promote?token=${encodeURIComponent(getToken())}&id=${encodeURIComponent(_pastedScenarioId)}`,
                              { method: "POST" });
      const data = await res.json();
      report.textContent = JSON.stringify(data, null, 2);
      if (data.ok) {
        textarea.value = "";
        _pastedScenarioId = null;
        refresh();
      }
    } catch (err) {
      report.textContent = "[error] " + String(err);
    }
  });
}
function bindRunButtons() {
  const log = document.getElementById("run-log");
  document.querySelectorAll(".run-fuzz-button").forEach((btn) => {
    if (btn.dataset.bound) return;
    btn.dataset.bound = "1";
    btn.addEventListener("click", () => {
      const id = btn.dataset.id;
      const needsApproval = btn.dataset.needsApproval === "1";
      if (needsApproval) {
        const ok = window.prompt(`Fuzz ${id} requires --approve. Type APPROVE to proceed.`) === "APPROVE";
        if (!ok) return;
      }
      const url = `/api/fuzz/run?token=${encodeURIComponent(getToken())}&id=${encodeURIComponent(id)}` + (needsApproval ? "&approve=1" : "");
      streamRun(url, log);
    });
  });
  document.querySelectorAll(".run-smoke-button").forEach((btn) => {
    if (btn.dataset.bound) return;
    btn.dataset.bound = "1";
    btn.addEventListener("click", () => {
      const id = btn.dataset.id;
      const url = `/api/test/smoke/run?token=${encodeURIComponent(getToken())}&id=${encodeURIComponent(id)}`;
      streamRun(url, log);
    });
  });
}
function render(payload) {
  renderTargetAndLive(payload);
  drawTestPipeline(payload);
  document.getElementById("packages-table").innerHTML = table(
    ["Package", "Label", "Run State", "Status", "Candidates", "Blockers"],
    (payload.evidence_packages || []).map(p => [code(p.id), esc(p.label), esc(p.run_state), esc(p.status), (p.candidate_ids || []).map(code).join("<br>"), (p.blockers || []).map(esc).join("<br>") || "none"])
  );
  document.getElementById("smoke-table").innerHTML = table(
    ["Smoke ID", "Label", "Category", "Working Directory", "Timeout", "Run"],
    (payload.smoke_tests || []).map(s => [
      code(s.id), esc(s.label), esc(s.category), esc(s.working_directory), esc(s.timeout_seconds),
      `<button class="run-smoke-button" data-id="${esc(s.id)}">Run smoke</button>`
    ])
  );
  document.getElementById("fuzz-table").innerHTML = table(
    ["Fuzz ID", "Label", "Category", "Package", "Safety", "Needs Testnet", "Candidates", "Run"],
    (payload.fuzz_tests || []).map(f => [
      code(f.id), esc(f.label), esc(f.category), esc(f.target_package), esc(f.safety_level),
      f.requires_deployed_testnet ? "yes" : "no", (f.related_candidates || []).map(code).join("<br>"),
      `<button class="run-fuzz-button" data-id="${esc(f.id)}" data-needs-approval="${f.requires_deployed_testnet ? "1" : "0"}">Run fuzz</button>`
    ])
  );
  bindRunButtons();
  bindPasteButtons();
  document.getElementById("evidence-table").innerHTML = table(
    ["Evidence File", "Size"],
    (payload.latest_evidence || []).map(e => [code(e.path), `${esc(e.size_kib)} KiB`])
  );
  const lifecycle = payload.testcase_lifecycle || {};
  const lifecycleSummary = [
    `Source: ${esc(lifecycle.source || "none")}`,
    `Cases: ${esc(lifecycle.case_count || 0)}`,
    `Buckets: ${esc(lifecycle.bucket_count || 0)}`,
    `Runtime anomalies: ${esc(lifecycle.runtime_anomaly_count || 0)}`,
    `Pending replay: ${esc(lifecycle.pending_replay_count || 0)}`,
    `Pending compare: ${esc(lifecycle.pending_compare_count || 0)}`
  ];
  document.getElementById("lifecycle-summary-strip").innerHTML = lifecycleSummary.map(item => `<span class="status-pill">${item}</span>`).join("");
  document.getElementById("lifecycle-runtime-buckets-table").innerHTML = table(
    ["Bucket", "Cases", "Reason", "Target", "Pending Replay", "Pending Compare", "Minimized"],
    (lifecycle.runtime_buckets || []).map(b => [
      code(b.bucket_id), esc(b.case_count), esc(b.triage_reason), esc(b.target_implementation), esc(b.pending_replay_count || 0), esc(b.pending_compare_count || 0), esc(b.complete_minimization_count || 0)
    ])
  );
  document.getElementById("lifecycle-runtime-cases-table").innerHTML = table(
    ["Case", "Run", "Scenario", "Reason", "Target"],
    (lifecycle.recent_runtime_cases || []).map(c => [
      code(c.case_id), code(c.source_run_id || "-"), esc(c.scenario_id || "-"), esc(c.triage_reason), esc(c.target_implementation)
    ])
  );
  document.getElementById("lifecycle-fuzz-buckets-table").innerHTML = table(
    ["Bucket", "Cases", "Classification", "Reason", "Target", "Pending Replay", "Pending Compare", "Minimized"],
    (lifecycle.fuzz_buckets || []).map(b => [
      code(b.bucket_id), esc(b.case_count), esc(b.classification), esc(b.triage_reason), esc(b.target_implementation), esc(b.pending_replay_count || 0), esc(b.pending_compare_count || 0), esc(b.complete_minimization_count || 0)
    ])
  );
  document.getElementById("lifecycle-fuzz-cases-table").innerHTML = table(
    ["Case", "Run", "Classification", "Reason", "Target"],
    (lifecycle.recent_fuzz_cases || []).map(c => [
      code(c.case_id), code(c.source_run_id || "-"), esc(c.classification), esc(c.triage_reason), esc(c.target_implementation)
    ])
  );
}
function drawTestPipeline(payload) {
  const packages = payload.evidence_packages || [];
  const packageNodes = packages.map((p, idx) => {
    const x = 70 + idx * 150;
    const klass = p.run_state === "runnable" ? "ok" : "warn";
    return `<g><rect class="${klass}" x="${x}" y="42" width="118" height="54" rx="8"/><text x="${x + 59}" y="65" text-anchor="middle" font-weight="700">${esc(p.id)}</text><text class="muted" x="${x + 59}" y="84" text-anchor="middle">${esc(p.run_state)}</text><line class="edge" x1="${x + 59}" y1="96" x2="${x + 59}" y2="140"/></g>`;
  }).join("");
  document.getElementById("test-pipeline-svg").innerHTML = `
    <defs><marker id="arrowhead" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto"><path d="M0,0 L0,6 L8,3 z" fill="#6b7b88"/></marker></defs>
    ${packageNodes}
    <rect class="node" x="110" y="144" width="180" height="54" rx="8"/><text x="200" y="166" text-anchor="middle" font-weight="700">Smoke Tests</text><text class="muted" x="200" y="185" text-anchor="middle">${esc((payload.smoke_tests || []).length)} registered</text>
    <rect class="warn" x="370" y="144" width="180" height="54" rx="8"/><text x="460" y="166" text-anchor="middle" font-weight="700">Fuzz Tests</text><text class="muted" x="460" y="185" text-anchor="middle">${esc((payload.fuzz_tests || []).length)} registered</text>
    <rect class="gate" x="650" y="132" width="190" height="78" rx="8"/><text x="745" y="160" text-anchor="middle" font-weight="700">Review Gate</text><text class="muted" x="745" y="182" text-anchor="middle">candidate-only</text>
    <line class="edge" x1="290" y1="171" x2="370" y2="171"/><line class="edge" x1="550" y1="171" x2="650" y2="171"/>
  `;
}
"""
    return _page("Tests & Evidence", "/tests", body, script)




def render_dashboard_html():
    return render_command_center_html()


def _scenario_trace_summary(row):
    parts = []
    milestones = row.get("related_milestones") or []
    if milestones:
        parts.append("Milestones: " + ", ".join(_escape(x) for x in milestones))
    intent = row.get("evidence_intent")
    if intent:
        parts.append("Intent: " + _escape(intent))
    trace = row.get("m1_trace") or {}
    for key in ("threat_ids", "gap_ids", "architecture_ids", "risk_candidate_ids"):
        values = trace.get(key) or []
        if values:
            parts.append(_escape(key) + ": " + ", ".join(f"<code>{_escape(v)}</code>" for v in values))
    blockers = row.get("promotion_blockers") or []
    if blockers:
        parts.append("Blockers: " + "; ".join(_escape(v) for v in blockers))
    return "<br>".join(parts) if parts else '<span class="small">No M1 trace metadata.</span>'


def render_scenarios_html():
    rows = _list_scenarios_for_compare()
    rows_html = "".join(
        "<tr>"
        f"<td><code>{_escape(row['id'])}</code><br><span class=\"small\">{_escape(row.get('runtime') or '')}</span></td>"
        f"<td>{_escape(row['title'])}<br><span class=\"small\">{_scenario_trace_summary(row)}</span></td>"
        f"<td><button class=\"run-scenario-button action-button\" data-path=\"{_escape(row['path'])}\">Run</button> "
        f"<a href=\"/operate/compare\" class=\"small\">compare both</a></td>"
        "</tr>"
        for row in rows
    ) or '<tr><td colspan="3"><em>No scenarios in dwarf/scenarios/. Paste one on the Tests page.</em></td></tr>'
    body = (
        '<section class="basic-only">'
        '<div class="card notice">'
        '<h3>Scenarios</h3>'
        '<p>Each scenario is a single test recipe &mdash; what to feed which parser, how many times, what to check. Click <strong>Run</strong> to execute one against a single implementation. The dashboard streams the live output and writes a forensic bundle you can inspect from <a href="/tests">Recent Runs</a>.</p>'
        '<p>Want to run the same scenario against both implementations and diff the outcomes? Use <a href="/operate/compare">Compare</a>.</p>'
        '</div>'
        '</section>'
        '<section>'
        '<h2>Scenarios</h2>'
        '<p class="adv-only small">POST /api/scenario/run?token=&amp;path=&lt;scenario&gt; runs <code>cardano-profile scenario run</code> end-to-end. Bundle lands in the configured runs directory; chain head updated atomically.</p>'
        '<div class="table-wrap"><table><thead><tr>'
        '<th>Scenario id</th><th>Title</th><th>Action</th>'
        f'</tr></thead><tbody>{rows_html}</tbody></table></div>'
        '<pre id="scenario-log" class="action-log">No run in progress.</pre>'
        '</section>'
    )
    script = """
function getToken() {
  const params = new URLSearchParams(window.location.search);
  return params.get("token") || "dwarf";
}
function streamRun(url, log) {
  log.textContent = "";
  fetch(url, { method: "POST" }).then(async (response) => {
    if (!response.ok) {
      log.textContent = `HTTP ${response.status}: ${await response.text()}`;
      return;
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const parts = buf.split("\\n\\n");
      buf = parts.pop();
      for (const part of parts) {
        for (const line of part.split("\\n")) {
          if (line.startsWith("data: ")) {
            log.textContent += line.slice(6) + "\\n";
            log.scrollTop = log.scrollHeight;
          } else if (line.startsWith("event: done")) {
            log.textContent += "[done]\\n";
          }
        }
      }
    }
  }).catch((err) => { log.textContent += "\\n[error] " + String(err); });
}
function render(payload) {
  renderTargetAndLive(payload);
  const log = document.getElementById("scenario-log");
  document.querySelectorAll(".run-scenario-button").forEach((btn) => {
    if (btn.dataset.bound) return;
    btn.dataset.bound = "1";
    btn.addEventListener("click", () => {
      const path = btn.dataset.path;
      const url = `/api/scenario/run?token=${encodeURIComponent(getToken())}&path=${encodeURIComponent(path)}`;
      streamRun(url, log);
    });
  });
}
"""
    return _page("Scenarios", "/scenarios", body, script)


def render_compare_html():
    """DEPRECATED — slice 24 retired this from the dispatch table; the
    canonical compare surface lives at /operate/compare via
    profile_manager.views.compare.render_operate_compare. This function
    is unreachable from any live route but retained because
    test_forensic.test_scenarios_route_in_navigation iterates it as a
    render-coverage rail. Slated for removal once that test moves to
    the modern view's HTML output."""
    rows = _list_scenarios_for_compare()
    rows_html = "".join(
        "<tr>"
        f"<td><code>{_escape(row['id'])}</code></td>"
        f"<td>{_escape(row['title'])}</td>"
        f"<td><button class=\"run-compare-button action-button\" data-path=\"{_escape(row['path'])}\">Run compare</button></td>"
        "</tr>"
        for row in rows
    ) or '<tr><td colspan="3"><em>No scenarios in dwarf/scenarios/.</em></td></tr>'
    body = (
        '<section class="basic-only">'
        '<div class="card notice">'
        '<h3>Compare implementations</h3>'
        '<p>Run the same test against both Amaru and cardano-node, side by side. The framework feeds identical inputs to both implementations using the same random seed, then compares the outcomes. If the two parsers ever disagree &mdash; one accepts what the other rejects &mdash; that&rsquo;s a candidate finding.</p>'
        '<p>Pick a scenario below and click <strong>Run compare</strong>. When it finishes you&rsquo;ll see the comparison report on each run\'s page.</p>'
        '</div>'
        '</section>'
        '<section>'
        '<h2>Compare implementations</h2>'
        '<p class="adv-only small">POST /api/scenario/compare?token=&amp;path=&lt;scenario&gt; runs <code>cardano-profile compare</code> end-to-end and emits cross-impl-comparison.md inside the second run\'s bundle.</p>'
        '<div class="table-wrap"><table><thead><tr>'
        '<th>Scenario id</th><th>Title</th><th>Action</th>'
        f'</tr></thead><tbody>{rows_html}</tbody></table></div>'
        '<pre id="compare-log" class="action-log">No compare in progress.</pre>'
        '</section>'
    )
    script = """
function getToken() {
  const params = new URLSearchParams(window.location.search);
  return params.get("token") || "dwarf";
}
function streamRun(url, log) {
  log.textContent = "";
  fetch(url, { method: "POST" }).then(async (response) => {
    if (!response.ok) {
      log.textContent = `HTTP ${response.status}: ${await response.text()}`;
      return;
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const parts = buf.split("\\n\\n");
      buf = parts.pop();
      for (const part of parts) {
        for (const line of part.split("\\n")) {
          if (line.startsWith("data: ")) {
            log.textContent += line.slice(6) + "\\n";
            log.scrollTop = log.scrollHeight;
          } else if (line.startsWith("event: done")) {
            log.textContent += "[done]\\n";
          }
        }
      }
    }
  }).catch((err) => { log.textContent += "\\n[error] " + String(err); });
}
function render(payload) {
  renderTargetAndLive(payload);
  const log = document.getElementById("compare-log");
  document.querySelectorAll(".run-compare-button").forEach((btn) => {
    if (btn.dataset.bound) return;
    btn.dataset.bound = "1";
    btn.addEventListener("click", () => {
      const path = btn.dataset.path;
      const url = `/api/scenario/compare?token=${encodeURIComponent(getToken())}&path=${encodeURIComponent(path)}`;
      streamRun(url, log);
    });
  });
}
"""
    return _page("Compare", "/compare", body, script)


_PROJECT_MAP_PATH = PROJECT_ROOT / "user" / "PROJECT-MAP.md"


def _md_inline(text):
    import html as _html
    import re as _re
    out = _html.escape(text)
    out = _re.sub(r"\[([^\]]+)\]\(([^)]+)\)",
                  lambda m: f'<a href="{_html.escape(m.group(2), quote=True)}">{m.group(1)}</a>',
                  out)
    out = _re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    out = _re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    return out


def _md_to_html(md_text):
    lines = md_text.splitlines()
    out = []
    i = 0
    in_list = False

    def close_list():
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            close_list()
            i += 1
            continue

        if stripped.startswith("### "):
            close_list()
            out.append(f"<h3>{_md_inline(stripped[4:])}</h3>")
            i += 1
            continue
        if stripped.startswith("## "):
            close_list()
            out.append(f"<h2>{_md_inline(stripped[3:])}</h2>")
            i += 1
            continue
        if stripped.startswith("# "):
            close_list()
            out.append(f"<h1>{_md_inline(stripped[2:])}</h1>")
            i += 1
            continue
        if stripped == "---":
            close_list()
            out.append("<hr>")
            i += 1
            continue

        # Table: header row | sep row | body rows
        if stripped.startswith("|") and i + 1 < len(lines) and set(lines[i + 1].strip().replace("|", "").replace(":", "").replace("-", "").replace(" ", "")) == set():
            close_list()
            header_cells = [c.strip() for c in stripped.strip("|").split("|")]
            i += 2
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                row_cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                rows.append(row_cells)
                i += 1
            tbl = ['<div class="table-wrap"><table>']
            tbl.append("<thead><tr>" + "".join(f"<th>{_md_inline(c)}</th>" for c in header_cells) + "</tr></thead>")
            tbl.append("<tbody>")
            for r in rows:
                tbl.append("<tr>" + "".join(f"<td>{_md_inline(c)}</td>" for c in r) + "</tr>")
            tbl.append("</tbody></table></div>")
            out.append("".join(tbl))
            continue

        if stripped.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_md_inline(stripped[2:])}</li>")
            i += 1
            continue

        close_list()
        # Paragraph: gather consecutive non-empty, non-special lines
        para = [stripped]
        j = i + 1
        while j < len(lines):
            nxt = lines[j].strip()
            if not nxt or nxt.startswith(("#", "-", "|", "---")):
                break
            para.append(nxt)
            j += 1
        out.append(f"<p>{_md_inline(' '.join(para))}</p>")
        i = j

    close_list()
    return "\n".join(out)




# Slice-15: cutover from legacy / to /operate. Single source of truth for
# all redirects. Keys are paths (post query-string strip); values are
# Location header values.
REDIRECTS = {
    "/": "/operate",
    "/index.html": "/operate",
    "/compare": "/operate/compare",
    # Slice 25: legacy /architecture targeted /operate/status (substrate
    # health) — wrong destination because /learn/architecture carries the
    # actual architecture diagram + prose. Re-pointed to the semantic
    # match. /status added (was 404) so the operator-typed shorthand
    # also lands on the live console. /settings still maps to
    # /operate/status because the legacy "settings" page WAS the live
    # status console (config + serving info).
    "/architecture": "/learn/architecture",
    "/status": "/operate/status",
    "/settings": "/operate/status",
    # Slice 26(a): /cli + /cli-docs alias the operator CLI reference.
    # Both were 404 before — operators typing the shorthand form would
    # land on a not-found page. The canonical surface is /learn/cli.
    "/cli": "/learn/cli",
    "/cli-docs": "/learn/cli",
    # Slice 26(b): bundle / inspector aliases. The bundles catalog
    # lives at /operate/bundles; the per-run inspector lives at
    # /operate/runs/<id> (which requires a run-id). Operators typing
    # /bundle, /bundles, /inspect, or /inspector should land
    # somewhere useful — the catalog is the natural starting point.
    "/bundle": "/operate/bundles",
    "/bundles": "/operate/bundles",
    "/inspect": "/operate/runs",
    "/inspector": "/operate/runs",
    "/project": "/operate",
    "/deliverables": "/operate/contract",
    "/raw": "/api/health",
}


def render_route_html(route):
    # Slice 42: /operate/compare/runs is the only path that consumes query
    # args (left=<id>&right=<id>). Branch off before stripping the query.
    if route.startswith("/operate/compare/runs"):
        from urllib.parse import urlsplit, parse_qs
        from profile_manager.views.operate_run_compare import render_operate_run_compare
        parts = urlsplit(route)
        qs = parse_qs(parts.query, keep_blank_values=True)
        left = (qs.get("left") or [""])[0]
        right = (qs.get("right") or [""])[0]
        return render_operate_run_compare(left, right)
    # Slice 7 of dispatch 7 — new-scenario template picker (GET).
    if route.split("?", 1)[0] == "/operate/scenarios/new":
        from urllib.parse import urlsplit
        from profile_manager.views.operate_scenarios_new import render_operate_scenarios_new
        return render_operate_scenarios_new(urlsplit(route).query)
    # Item #15 — scenario editor. /operate/scenarios/edit/<id> opens an
    # existing scenario for editing; /operate/scenarios/edit (no id)
    # opens a blank-state authoring buffer. Pre-empts the routes-dict
    # lookup so the trailing path-segment is preserved.
    if route.split("?", 1)[0].startswith("/operate/scenarios/edit"):
        from profile_manager.views.operate_scenarios_edit import render_operate_scenarios_edit
        path_only = route.split("?", 1)[0]
        rest = path_only[len("/operate/scenarios/edit"):]
        sid = rest.lstrip("/").rstrip("/") if rest else ""
        return render_operate_scenarios_edit(sid)
    # Item E (Phase 4.3 D-1) — /operate/audit accepts
    # ?classification=&family= server-side filters.
    if route.split("?", 1)[0] == "/operate/audit":
        from urllib.parse import urlsplit
        return render_operate_audit(urlsplit(route).query)
    # Slice 46: /operate/runs accepts ?outcome=&q= server-side filters.
    if route.split("?", 1)[0] == "/operate/runs":
        from urllib.parse import urlsplit, parse_qs
        parts = urlsplit(route)
        qs = parse_qs(parts.query, keep_blank_values=True)
        outcome = (qs.get("outcome") or [""])[0]
        q = (qs.get("q") or [""])[0]
        if outcome not in ("", "pass", "fail", "error"):
            outcome = ""
        return render_operate_runs(outcome=outcome, q=q[:128])
    # Strip query string before lookup; routes are paths only.
    route = route.split("?", 1)[0]
    routes = {
        "/": render_command_center_html,
        "/index.html": render_command_center_html,
        "/tests": render_tests_html,
        "/scenarios": render_scenarios_html,
        "/operate": render_operate_landing,
        "/operate/scenarios": render_operate_scenarios,
        "/learn": render_learn_landing,
        "/learn/concepts": render_learn_concepts,
        "/learn/coverage": render_learn_coverage,
        "/learn/status": render_learn_status,
        "/operate/compare": render_operate_compare,
        "/learn/architecture": render_learn_architecture,
        "/operate/profiles": render_operate_profiles,
        "/operate/runs": render_operate_runs,
        "/operate/status": render_operate_status,
        "/operate/targets": render_operate_targets,
        "/operate/bundles": render_operate_bundles,
        "/operate/plugins": render_operate_plugins,
        "/operate/config": render_operate_config,
        "/operate/notifications": render_operate_notifications,
        "/learn/getting-started": render_learn_getting_started,
        "/learn/examples": render_learn_examples,
        "/learn/api": render_learn_api,
        "/learn/glossary": render_learn_glossary,
        "/learn/faq": render_learn_faq,
        "/learn/troubleshooting": render_learn_troubleshooting,
        "/learn/operator-runbook": render_learn_operator_runbook,
        "/learn/developer-onboarding": render_learn_developer_onboarding,
        "/learn/plugin-authoring": render_learn_plugin_authoring_guide,
        "/operate/contract": render_operate_contract,
        "/operate/coverage": render_operate_coverage,
        "/operate/crashes": render_operate_crashes,
        "/operate/schedule": render_operate_schedule,
        "/operate/timeline": render_operate_timeline,
        "/operate/static-analysis": render_operate_static_analysis,
        "/learn/walkthroughs": render_learn_walkthroughs,
        "/learn/cli": render_learn_cli,
    }
    renderer = routes.get(route)
    if renderer is not None:
        return renderer()
    # Slice 47 — /operate/runs/<id>/live serves the streaming HTML view.
    # The /tail SSE endpoint is dispatched from do_GET (needs streaming),
    # not from this HTML-only route function.
    if route.startswith("/operate/runs/") and route.endswith("/live"):
        rid = route[len("/operate/runs/"):-len("/live")]
        if rid and "/" not in rid and ".." not in rid:
            from profile_manager.views.operate_run_live import render_operate_run_live
            return render_operate_run_live(rid)
    # /operate/runs/<id> — slice-26 current-gen bundle inspector.
    if route.startswith("/operate/runs/"):
        rid = route[len("/operate/runs/"):]
        if rid and "/" not in rid and ".." not in rid:
            html = render_operate_run(rid)
            if html is not None:
                return html
            return render_operate_run_not_found(rid)
    # /runs/<id> kept as a legacy alias serving the same current-gen
    # inspector body (avoids breaking already-shared run links).
    if route.startswith("/runs/") and not route.endswith("/bundle"):
        rid = route[len("/runs/"):]
        if rid and "/" not in rid and ".." not in rid:
            html = render_operate_run(rid)
            if html is not None:
                return html
            return render_operate_run_not_found(rid)
    return None


def _read_ndjson_lines(path, limit=None):
    if not path.exists():
        return []
    out = []
    with path.open("r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
            if limit is not None and len(out) >= limit:
                break
    return out




def generate_dashboard(output_dir=None):
    directory = Path(output_dir).expanduser() if output_dir else default_dashboard_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "index.html"
    path.write_text(render_dashboard_html(), encoding="utf-8")
    return DashboardResult(path=path, url=f"file://{path}")


def dashboard_serve_text(output_dir=None, port=8787, bind="0.0.0.0", token=None):
    directory = Path(output_dir).expanduser() if output_dir else default_dashboard_dir()
    urls = "\n".join(f"URL: {url}" for url in _local_interface_urls(port))
    token_active = resolve_token(token=token)
    token_source = (
        "explicit --token"
        if token
        else ("ADA2_DWARF_TOKEN env var" if os.environ.get("ADA2_DWARF_TOKEN") else "default")
    )
    return (
        f"Serving live dashboard from: {directory}\n"
        f"Bind: {bind}:{port}\n"
        f"{urls}\n"
        "Live API: /api/status\n"
        f"Token gate active for any future mutating endpoint (source: {token_source}; length: {len(token_active)} chars).\n"
        "Read-only routes do not require a token.\n"
        "No browser action deploys, removes, fuzzes, or mutates runtime state.\n"
    )


def serve_dashboard_handler_factory(expected_token, *, serving_port=None, serving_bind=None):
    """Build a DashboardHandler class bound to expected_token. Extracted from
    serve_dashboard so tests can spin up their own ThreadingHTTPServer
    without invoking the full serve flow (which prints, generates HTML, and
    blocks on serve_forever).

    serving_port/serving_bind are passed through to /operate/status so the
    "Dashboard serving" tile reflects the live process. Optional — tests
    that don't care can omit them and the tile renders dashes.
    """

    class DashboardHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            print(f"{self.address_string()} - {format % args}")

        def _send(self, status, content_type, body, extra_headers=None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            for key, value in (extra_headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)
            # Slice 44: feed the Prometheus request counter. Path-bucketing
            # bounds cardinality (see data/health_metrics._path_bucket).
            try:
                from profile_manager.data.health_metrics import record_request
                record_request(self.command, self.path, status)
            except Exception:  # noqa: BLE001 — telemetry never breaks responses
                pass

        def _send_stream(self, status, content_type, generator):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            for chunk in generator:
                try:
                    self.wfile.write(chunk)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    break

        def _send_redirect(self, location):
            """Slice-15: 302 with Location header and empty body. Reuses
            _send so cache headers and Content-Length stay consistent."""
            self._send(302, "text/plain; charset=utf-8", b"", extra_headers={"Location": location})

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            body_bytes = self.rfile.read(length) if length > 0 else b""
            if self.path.split("?", 1)[0] == "/api/moog/setup":
                ok, err = check_token(self.path, expected=expected_token)
                if not ok:
                    self._send(403, "text/plain; charset=utf-8", f"{err}\n".encode("utf-8"))
                    return
                from profile_manager.config import DeploymentConfig, config_exists, load_config, save_config
                from profile_manager.data.operate_config import apply_moog_setup_form
                form = parse_qs(body_bytes.decode("utf-8", errors="replace"), keep_blank_values=True)
                try:
                    current_config = load_config() if config_exists() else DeploymentConfig.from_dict({})
                    updated = apply_moog_setup_form(current_config, form)
                    save_config(updated)
                except Exception as exc:
                    self._send(500, "text/plain; charset=utf-8", f"failed to save Moog setup: {exc}\n".encode("utf-8"))
                    return
                self._send(303, "text/plain; charset=utf-8", b"", extra_headers={"Location": "/operate/config?saved=moog"})
                return
            # Slice 3 of dispatch 7 — multipart bundle import.
            if self.path.split("?", 1)[0] == "/api/bundle/import":
                from profile_manager.data.bundle_import import handle_bundle_import_post
                ctype = self.headers.get("Content-Type") or ""
                status, body = handle_bundle_import_post(ctype, body_bytes)
                self._send(status, "text/html; charset=utf-8", body.encode("utf-8"))
                return
            # Slice 7 of dispatch 7 — create scenario from template.
            if self.path.split("?", 1)[0] == "/operate/scenarios/new":
                from profile_manager.data.operate_scenarios_new import handle_create_post
                status, body = handle_create_post(body_bytes)
                self._send(status, "text/html; charset=utf-8", body.encode("utf-8"))
                return
            sched_result = dispatch_schedule_request(
                method="POST", path=self.path, body=body_bytes,
                expected_token=expected_token,
            )
            if sched_result is not None:
                status, ctype, body = sched_result
                self._send(status, ctype, body if isinstance(body, (bytes, bytearray)) else b"".join(body))
                return
            scen_result = dispatch_scenario_request(
                method="POST", path=self.path, body=body_bytes,
                expected_token=expected_token,
            )
            if scen_result is not None:
                status, ctype, body = scen_result
                self._send(status, ctype, body if isinstance(body, (bytes, bytearray)) else b"".join(body))
                return
            result = dispatch_mutating_request(
                method="POST", path=self.path, expected_token=expected_token,
            )
            if result is None:
                self._send(404, "text/plain; charset=utf-8", b"not found\n")
                return
            status, ctype, body = result
            if status == 200 and "event-stream" in ctype:
                self._send_stream(status, ctype, body)
            else:
                self._send(status, ctype, body if isinstance(body, (bytes, bytearray)) else b"".join(body))

        def do_GET(self):
            # Allow GET to show a 405 for every mutating endpoint (more informative than 404).
            mutating_paths = {
                "/api/deploy", "/api/remove", "/api/fuzz/run", "/api/test/smoke/run",
                "/api/scenario/paste", "/api/scenario/promote", "/api/scenario/compare",
                "/api/scenario/run",
            }
            path_only = self.path.split("?", 1)[0]
            if path_only in mutating_paths:
                self._send(405, "text/plain; charset=utf-8", b"use POST for mutating endpoints\n")
                return
            target = REDIRECTS.get(path_only)
            if target is not None:
                self._send_redirect(target)
                return
            if path_only == "/operate/status":
                html_body = render_operate_status(
                    port=serving_port,
                    bind=serving_bind,
                    token=expected_token,
                )
                self._send(200, "text/html; charset=utf-8", html_body.encode("utf-8"))
                return
            if path_only == "/operate/config":
                html_body = render_operate_config(token=expected_token)
                self._send(200, "text/html; charset=utf-8", html_body.encode("utf-8"))
                return
            html_body = render_route_html(self.path)
            if html_body is not None:
                self._send(200, "text/html; charset=utf-8", html_body.encode("utf-8"))
                return
            static_result = dispatch_static_request(self.path)
            if static_result is not None:
                status, ctype, body = static_result
                self._send(status, ctype, body)
                return
            if self.path == "/favicon.ico":
                self._send(204, "image/x-icon", b"")
                return
            if self.path == "/api/status":
                payload = build_dashboard_status_payload(live=True)
                body = json.dumps(payload, indent=2).encode("utf-8")
                self._send(200, "application/json; charset=utf-8", body)
                return
            if self.path == "/api/health":
                # Slice 26: legacy /raw HTML page retired; /api/health is
                # the JSON-only successor. Identical payload to /api/status
                # so existing tooling can pivot without re-shaping callers.
                payload = build_dashboard_status_payload(live=True)
                body = json.dumps(payload, indent=2).encode("utf-8")
                self._send(200, "application/json; charset=utf-8", body)
                return
            if path_only in ("/healthz", "/health"):
                # Slice 44: light-weight liveness probe. Distinct from
                # /api/health (full substrate payload) — designed for
                # load-balancer / supervisor probes where the 200-or-503
                # signal is all that's needed.
                from profile_manager.data.health_metrics import healthz_payload
                payload = healthz_payload()
                body = json.dumps(payload, indent=2).encode("utf-8")
                status_code = 200 if payload.get("status") == "ok" else 503
                self._send(status_code, "application/json; charset=utf-8", body)
                return
            if path_only == "/metrics":
                # Slice 44: Prometheus text-exposition endpoint.
                from profile_manager.data.health_metrics import prometheus_exposition
                body = prometheus_exposition()
                self._send(200, "text/plain; version=0.0.4; charset=utf-8", body)
                return
            # Slice 47 — /operate/runs/<id>/tail SSE stream of log.ndjson.
            if path_only.startswith("/operate/runs/") and path_only.endswith("/tail"):
                rid = path_only[len("/operate/runs/"):-len("/tail")]
                if rid and "/" not in rid and ".." not in rid:
                    from profile_manager.data.operate_run_tail import stream_run_tail
                    self._send_stream(200, "text/event-stream; charset=utf-8", stream_run_tail(rid))
                    return
            api = dispatch_api_request(self.path)
            if api is not None:
                status, ctype, body = api[:3]
                extra_headers = api[3] if len(api) > 3 else None
                self._send(status, ctype, body, extra_headers=extra_headers)
                return
            self._send(404, "text/plain; charset=utf-8", b"not found\n")

    return DashboardHandler


_SCHEDULER_THREAD_STARTED = False


def _start_scheduler_thread():
    """Item #19 — fire-and-forget daemon thread that polls the schedule
    store every 30 seconds, runs due entries via dispatch_mutating_request's
    builder, and honors the global mutating lock.

    Idempotent: a process-wide flag ensures the thread is started once
    even if serve_dashboard is invoked multiple times in tests.
    """
    global _SCHEDULER_THREAD_STARTED
    if _SCHEDULER_THREAD_STARTED:
        return
    _SCHEDULER_THREAD_STARTED = True

    import threading
    import time as _time

    def _loop():
        from profile_manager.data import scheduler
        while True:
            try:
                scheduler.tick(
                    command_builder=_default_cli_command_builder,
                    lock_acquire=try_acquire_mutating_lock,
                    lock_release=release_mutating_lock,
                )
            except Exception:  # noqa: BLE001
                # Never let an error kill the loop — the dashboard
                # process must keep serving HTTP regardless of the
                # scheduler's health.
                pass
            _time.sleep(30)

    t = threading.Thread(target=_loop, name="dwarf-scheduler", daemon=True)
    t.start()


def serve_dashboard(output_dir=None, port=8787, bind="0.0.0.0", token=None):
    directory = Path(output_dir).expanduser() if output_dir else default_dashboard_dir()
    expected_token = resolve_token(token=token)
    if not (directory / "index.html").exists():
        generate_dashboard(directory)

    DashboardHandler = serve_dashboard_handler_factory(
        expected_token,
        serving_port=int(port),
        serving_bind=bind,
    )

    print(dashboard_serve_text(directory, port, bind, token=token), end="")
    _start_scheduler_thread()
    server = ThreadingHTTPServer((bind, int(port)), DashboardHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
    finally:
        server.server_close()


def port_available(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", int(port))) != 0
