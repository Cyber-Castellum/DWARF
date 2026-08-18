"""Payload for the /operate/antithesis page.

Discovers the inputs the Antithesis build/validate/preflight/launch forms
pre-fill from: available profiles, CBOR fuzz scenarios, already-built bundle
directories under the writable runtime state root, and whether Moog/Antithesis
credentials look configured. All discovery is best-effort — a failure degrades
to an empty list rather than breaking the page.
"""

from __future__ import annotations

import os
from pathlib import Path

_PKG = Path(__file__).resolve().parents[1]          # .../dwarf/profile_manager
_DWARF = _PKG.parent                                 # .../dwarf
_SCENARIOS = _DWARF / "scenarios"


def _state_root() -> Path:
    return Path(os.environ.get("ADA2_DWARF_STATE_DIR", "/var/dwarf/state"))


def antithesis_state_dir() -> Path:
    """Writable directory GUI builds are emitted into."""
    return _state_root() / "antithesis"


def _profiles() -> list[str]:
    try:
        from profile_manager.profiles import load_profiles
        ids = []
        for p in load_profiles():
            pid = getattr(p, "id", None) or getattr(p, "profile_id", None) or getattr(p, "name", None)
            if pid:
                ids.append(str(pid))
        return sorted(ids)
    except Exception:
        return []


def _cbor_scenarios() -> list[str]:
    try:
        out = []
        for path in sorted(_SCENARIOS.glob("*cbor*.yaml")):
            out.append(f"dwarf/scenarios/{path.name}")
        return out
    except Exception:
        return []


def _asset_dirs() -> list[str]:
    """Bundle directories that already exist and can be validated/launched."""
    dirs: list[str] = []
    try:
        built = antithesis_state_dir()
        if built.is_dir():
            for d in sorted(built.iterdir()):
                if (d / "config" / "docker-compose.yaml").exists() or d.is_dir():
                    dirs.append(str(d))
    except Exception:
        pass
    return dirs


def _moog_configured() -> bool:
    try:
        from profile_manager.config import config_exists, load_config
        if not config_exists():
            return False
        cfg = load_config()
        raw = getattr(cfg, "moog", None)
        if raw is None and hasattr(cfg, "to_dict"):
            raw = cfg.to_dict().get("moog")
        if not isinstance(raw, dict):
            return False
        return bool(raw.get("github_user") or raw.get("antithesis_launch_url") or raw.get("antithesis_api_key"))
    except Exception:
        # Unknown → don't nag.
        return True


def operate_antithesis_payload() -> dict:
    return {
        "profiles": _profiles(),
        "scenarios": _cbor_scenarios(),
        "asset_dirs": _asset_dirs(),
        "moog_configured": _moog_configured(),
        "build_out_root": str(antithesis_state_dir()),
    }
