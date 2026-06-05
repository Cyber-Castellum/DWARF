"""Pure data-extraction helpers for the dwarf dashboard.

Each submodule returns plain Python data (dicts, lists, scalars). No
HTML, no Flask state, no module-level mutable state. Render-side code
lives in profile_manager.dashboard.
"""
from profile_manager.data.bundles import _forensic_bundles_dir, _latest_evidence_rows
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
from profile_manager.data.files import (
    _attachment_headers,
    _best_existing,
    _download_url,
    _escape,
    _latest_files,
    _pdf_url,
    _read_text,
)
from profile_manager.data.fuzz import _fuzz_rows, _smoke_rows
from profile_manager.data.health import (
    _extract_health_value,
    _extract_tip_json,
    _health_from_body,
    _latest_profile_health,
    _live_health,
)
from profile_manager.data.lifecycle import (
    _live_testcase_lifecycle_summary,
    _local_testcase_lifecycle_summary,
    _read_ndjson_rows,
    _summarize_testcase_state,
)
from profile_manager.data.packages import _package_rows
from profile_manager.data.profiles import _profile_rows
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
