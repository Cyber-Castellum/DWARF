"""Read-only data extractor for /operate/plugins (slice 2 of dispatch 7).

Walks every plugin root configured via DWARF_PLUGINS_DIR (and the
default ~/.dwarf/plugins), reads each plugin's plugin.json + the
registry it contributes, and projects a render-ready row per plugin.

No mutation. No fabrication. Plugins that fail to load surface as
rows with an ``error`` field instead of being silently dropped.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def operate_plugins_payload() -> dict[str, Any]:
    """Return ``{plugins, plugin_roots, total_primitives}`` for the
    plugins page. Each plugin row carries enough metadata for an
    operator to identify it and decide whether to trust it."""
    from profile_manager.plugin_loader import (
        DEFAULT_PLUGIN_ROOT,
        DWARF_API_VERSION,
        PLUGINS_ENV,
        plugin_roots_from_env,
        discover_plugin_manifests,
    )

    plugin_roots = [str(p) for p in plugin_roots_from_env()]

    rows: list[dict[str, Any]] = []
    total_primitives = 0
    try:
        manifests = discover_plugin_manifests()
    except Exception as exc:  # noqa: BLE001
        # A bad plugin.json (e.g. wrong api version) raises during
        # discover. Surface that as an error row rather than 500ing.
        rows.append({
            "plugin_id": "(discovery failed)",
            "plugin_root": "",
            "dwarf_api_version": "",
            "entrypoint": None,
            "registry_path": None,
            "primitive_count": 0,
            "primitives": [],
            "error": str(exc),
        })
        return {
            "plugins": rows,
            "plugin_roots": plugin_roots,
            "default_plugin_root": str(DEFAULT_PLUGIN_ROOT),
            "plugins_env": PLUGINS_ENV,
            "expected_api_version": DWARF_API_VERSION,
            "total_primitives": 0,
        }

    for manifest in manifests:
        primitives: list[str] = []
        error: str | None = None
        registry_path = manifest.registry_path
        if registry_path is not None and Path(registry_path).is_file():
            try:
                payload = json.loads(Path(registry_path).read_text(encoding="utf-8"))
                primitives = sorted((payload.get("primitives") or {}).keys())
            except (OSError, json.JSONDecodeError) as exc:
                error = f"registry parse: {exc}"
        # Pull plugin.json again for a few display-only fields the
        # PluginManifest dataclass doesn't carry (description / version).
        manifest_payload: dict[str, Any] = {}
        manifest_path = Path(manifest.plugin_root) / "plugin.json"
        if manifest_path.is_file():
            try:
                manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                manifest_payload = {}
        rows.append({
            "plugin_id": manifest.plugin_id,
            "plugin_root": str(manifest.plugin_root),
            "dwarf_api_version": manifest.dwarf_api_version,
            "version": manifest_payload.get("version") or "",
            "description": manifest_payload.get("description") or "",
            "entrypoint": str(manifest.entrypoint) if manifest.entrypoint else None,
            "registry_path": str(manifest.registry_path) if manifest.registry_path else None,
            "primitive_count": len(primitives),
            "primitives": primitives,
            "error": error,
        })
        total_primitives += len(primitives)

    return {
        "plugins": rows,
        "plugin_roots": plugin_roots,
        "default_plugin_root": str(DEFAULT_PLUGIN_ROOT),
        "plugins_env": PLUGINS_ENV,
        "expected_api_version": DWARF_API_VERSION,
        "total_primitives": total_primitives,
    }
