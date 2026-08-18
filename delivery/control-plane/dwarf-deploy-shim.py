#!/usr/bin/env python3
"""DWARF substrate control-channel shim (forced-command target).

This is the host-side half of the web-driven substrate control channel. It is
referenced from a restricted `authorized_keys` entry as:

    command="/path/to/dwarf-deploy-shim",no-pty,no-port-forwarding,... <pubkey>

so that a client presenting the paired key can ONLY invoke a fixed set of
whitelisted DWARF verbs — never an arbitrary shell command. sshd hands us the
client's requested command in $SSH_ORIGINAL_COMMAND; we parse it as
`<verb> [arg] [--dry-run]`, validate it hard, then generate the real command
via DWARF's own `profiles` module and run it locally on this (the deploy) host.

Because generation lives in DWARF (single source of truth) and only the verb
crosses the wire, a compromised dashboard cannot smuggle shell through this key:
anything that is not an allowed verb + well-formed profile id is rejected before
any command is built.

Config is read from `dwarf-control.conf` next to this script:
    DWARF_ROOT=/abs/path/to/<checkout>/dwarf
    REMOTE_BASE_PATH=/home/<user>/cardano-profiles
    AUDIT_LOG=/abs/path/to/dwarf-control.log   # optional
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SHIM_DIR = Path(__file__).resolve().parent
CONF_PATH = SHIM_DIR / "dwarf-control.conf"

# Verbs the key is permitted to invoke. Everything else is rejected.
READ_VERBS = {"status", "active"}
WRITE_VERBS = {"deploy", "remove", "coverage"}
ALLOWED_VERBS = READ_VERBS | WRITE_VERBS

# A profile id / view token: starts alnum, then alnum/-/_ , bounded length.
_ARG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,80}$")


def _load_conf() -> dict:
    conf: dict[str, str] = {}
    if CONF_PATH.exists():
        for raw in CONF_PATH.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            conf[key.strip()] = val.strip()
    return conf


def _audit(conf: dict, original: str, decision: str) -> None:
    log_path = conf.get("AUDIT_LOG")
    if not log_path:
        return
    ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    src = os.environ.get("SSH_CONNECTION", "?").split(" ")[0]
    try:
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(f"{ts}\t{src}\t{decision}\t{original!r}\n")
    except OSError:
        pass


def _reject(conf: dict, original: str, reason: str) -> int:
    _audit(conf, original, f"REJECT:{reason}")
    sys.stderr.write(f"dwarf-deploy-shim: refused ({reason})\n")
    return 77  # EX_NOPERM


def _run_script(script: str) -> int:
    proc = subprocess.run(["bash", "-c", script], text=True)
    return proc.returncode


def main() -> int:
    conf = _load_conf()
    original = os.environ.get("SSH_ORIGINAL_COMMAND", "").strip()

    dwarf_root = conf.get("DWARF_ROOT")
    if not dwarf_root or not Path(dwarf_root).is_dir():
        return _reject(conf, original, "misconfigured-dwarf-root")
    if str(dwarf_root) not in sys.path:
        sys.path.insert(0, str(dwarf_root))

    # Point DWARF's profile loader + runtime-root normalizer at the same roots
    # the dashboard uses, BEFORE importing profiles. PROFILES_DIR = the writable
    # overlay (so GUI-created profiles are visible to the generator);
    # REMOTE_BASE_PATH = the single runtime-root base (deploy == remove).
    if conf.get("PROFILES_DIR"):
        os.environ["ADA2_DWARF_PROFILES_DIR"] = conf["PROFILES_DIR"]
    if conf.get("REMOTE_BASE_PATH"):
        os.environ["ADA2_DWARF_REMOTE_BASE"] = conf["REMOTE_BASE_PATH"]

    if not original:
        return _reject(conf, original, "empty-command")

    # Parse strictly: verb, then at most two further tokens (arg, --dry-run).
    try:
        tokens = shlex.split(original)
    except ValueError:
        return _reject(conf, original, "unparseable")
    if not tokens or len(tokens) > 3:
        return _reject(conf, original, "bad-token-count")

    verb = tokens[0]
    rest = tokens[1:]
    dry_run = False
    if "--dry-run" in rest:
        dry_run = True
        rest = [t for t in rest if t != "--dry-run"]
    arg = rest[0] if rest else None

    if verb not in ALLOWED_VERBS:
        return _reject(conf, original, "verb-not-allowed")
    if arg is not None and not _ARG_RE.match(arg):
        return _reject(conf, original, "bad-arg")

    try:
        from profile_manager.profiles import (
            active_profile_command,
            deploy_command,
            find_profile,
            remove_command,
            status_command,
        )
    except Exception as exc:  # import surface is host-controlled, not client
        return _reject(conf, original, f"import-failed:{type(exc).__name__}")

    # Build the real command from the verb. Generation is DWARF's, not the
    # client's — the client only chose which verb+profile.
    if verb == "status":
        script = status_command()
    elif verb == "active":
        script = active_profile_command()
    elif verb == "deploy":
        if not arg:
            return _reject(conf, original, "deploy-requires-profile")
        try:
            profile = find_profile(arg)
        except KeyError:
            return _reject(conf, original, "unknown-profile")
        script = deploy_command(profile)
    elif verb == "remove":
        base = conf.get("REMOTE_BASE_PATH")
        if not base:
            return _reject(conf, original, "missing-remote-base-path")
        script = remove_command(base)
    elif verb == "coverage":
        # Run an AFL coverage scenario on the HOST (the hardened dashboard
        # container can't run AFL's forkserver). arg is a scenario id; we run
        # DWARF's own CLI locally with the provisioned harness env exported.
        if not arg:
            return _reject(conf, original, "coverage-requires-scenario")
        scen_dir = conf.get("SCENARIOS_DIR")
        if not scen_dir:
            return _reject(conf, original, "missing-scenarios-dir")
        scen_path = Path(scen_dir) / f"{arg}.yaml"
        if not scen_path.exists():
            return _reject(conf, original, "unknown-scenario")
        harness = conf.get("AFL_HARNESS", "/opt/dwarf/afl-harness/dwarf-decode-any")
        aflfuzz = conf.get("AFL_FUZZ", "/opt/dwarf/afl-harness/afl-fuzz")
        script = (
            f"cd {shlex.quote(str(dwarf_root))} && "
            f"PYTHONPATH=. "
            f"DWARF_AFL_HARNESS={shlex.quote(harness)} "
            f"DWARF_AFL_FUZZ={shlex.quote(aflfuzz)} "
            f"python3 cardano-profile scenario run {shlex.quote(str(scen_path))}"
        )
    else:  # unreachable — guarded above
        return _reject(conf, original, "verb-not-allowed")

    if dry_run:
        _audit(conf, original, "DRYRUN")
        sys.stdout.write(script if script.endswith("\n") else script + "\n")
        return 0

    _audit(conf, original, "RUN")
    return _run_script(script)


if __name__ == "__main__":
    raise SystemExit(main())
