"""Plugin discovery and registry extension loading for Dwarf."""

from __future__ import annotations

import importlib.util
import json
import os
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Iterable


DWARF_API_VERSION = "v1"
DEFAULT_PLUGIN_ROOT = Path.home() / ".dwarf" / "plugins"
PLUGINS_ENV = "DWARF_PLUGINS_DIR"


@dataclass(frozen=True)
class PluginManifest:
    plugin_id: str
    plugin_root: Path
    dwarf_api_version: str
    entrypoint: Path | None
    registry_path: Path | None


def plugin_roots_from_env() -> list[Path]:
    env_value = os.environ.get(PLUGINS_ENV, "")
    roots: list[Path] = []
    if env_value:
        for raw in env_value.split(os.pathsep):
            candidate = Path(raw).expanduser()
            if raw.strip():
                roots.append(candidate)
    roots.append(DEFAULT_PLUGIN_ROOT)
    deduped: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        resolved = root.resolve()
        if resolved not in seen:
            seen.add(resolved)
            deduped.append(resolved)
    return deduped


def discover_plugin_manifests(plugin_roots: Iterable[Path] | None = None) -> list[PluginManifest]:
    roots = list(plugin_roots) if plugin_roots is not None else plugin_roots_from_env()
    manifests: list[PluginManifest] = []
    for root in roots:
        if not root.exists():
            continue
        for plugin_root in sorted(path for path in root.iterdir() if path.is_dir()):
            manifest_path = plugin_root / "plugin.json"
            if not manifest_path.exists():
                continue
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            api_version = payload.get("dwarf_api_version")
            if api_version != DWARF_API_VERSION:
                raise ValueError(
                    f"plugin {payload.get('plugin_id', plugin_root.name)!r} targets {api_version!r}, "
                    f"expected {DWARF_API_VERSION!r}"
                )
            entrypoint = payload.get("entrypoint")
            registry = payload.get("registry")
            manifests.append(
                PluginManifest(
                    plugin_id=payload.get("plugin_id", plugin_root.name),
                    plugin_root=plugin_root,
                    dwarf_api_version=api_version,
                    entrypoint=(plugin_root / entrypoint).resolve() if entrypoint else None,
                    registry_path=(plugin_root / registry).resolve() if registry else None,
                )
            )
    return manifests


def _load_module(module_name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"unable to load plugin module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _normalize_params_schema(entry: dict, plugin_root: Path) -> dict:
    params_schema = entry.get("params_schema")
    if params_schema and not Path(params_schema).is_absolute():
        entry = dict(entry)
        entry["params_schema"] = str((plugin_root / params_schema).resolve())
    return entry


def load_plugin_entries(manifests: Iterable[PluginManifest]) -> dict[str, dict]:
    merged: dict[str, dict] = {}
    for manifest in manifests:
        if manifest.registry_path:
            payload = json.loads(manifest.registry_path.read_text(encoding="utf-8"))
            for name, entry in (payload.get("primitives") or {}).items():
                merged[name] = _normalize_params_schema(dict(entry), manifest.plugin_root)
        if manifest.entrypoint:
            module = _load_module(f"dwarf_plugin_{manifest.plugin_id}", manifest.entrypoint)
            register = getattr(module, "register", None)
            if register is None:
                raise ValueError(f"plugin {manifest.plugin_id!r} missing register(registry) entrypoint")
            register(merged)
            for name, entry in list(merged.items()):
                if isinstance(entry, dict):
                    merged[name] = _normalize_params_schema(entry, manifest.plugin_root)
    return merged
