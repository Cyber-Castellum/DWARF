"""Install built dwarf-cardano-shims executables to their manifest-declared
binary paths. Run on build-host after `cabal build all`."""
import glob
import json
import os
import shutil
from pathlib import Path

BASE = Path("${DWARF_ROOT}")
ROOT = BASE / "dwarf"
SHIMS = ROOT / "targets" / "cardano-node"
MANIFESTS = ROOT / "targets" / "manifests"


def built_path(exe_name):
    """Locate the compiled exe under dist-newstyle (arch/ghc/profile agnostic)."""
    matches = glob.glob(
        str(SHIMS / "dist-newstyle" / "build" / "*" / "*"
            / "dwarf-cardano-shims-*" / "x" / exe_name / "*" / "build"
            / exe_name / exe_name)
    )
    return matches[0] if matches else None


def main():
    installed, missing = [], []
    for mpath in sorted(glob.glob(str(MANIFESTS / "cardano-node-*.yaml"))):
        m = json.load(open(mpath))
        exe = m["id"]
        dst_rel = m.get("binary")
        if not dst_rel:
            continue
        src = built_path(exe)
        if not src:
            missing.append(exe)
            continue
        dst = BASE / dst_rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        os.chmod(dst, 0o755)
        installed.append((exe, dst_rel))
    print(f"installed={len(installed)} missing-build={len(missing)}")
    for exe, rel in installed:
        print(f"  {exe} -> {rel}")
    if missing:
        print("MISSING BUILDS:", missing)


if __name__ == "__main__":
    main()
