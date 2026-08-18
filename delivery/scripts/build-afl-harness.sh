#!/bin/sh
# build-afl-harness.sh — build the coverage-guided cardano-node (Haskell) fuzz
# harness `dwarf-decode-any` and install it + the vendored AFL 4.40c runtime to
# a stable location that the aflpp coverage scenarios point at by default.
#
# Why this isn't just "in the repo": the harness is a ~268 MB instrumented GHC
# binary (a 1.7 GB dist-newstyle build tree) — a build artifact, not source. The
# repo ships the SOURCE (this package) + the vendored AFL runtime; this script
# turns them into the runnable harness. Run it once per host, like the Cardano
# toolchain or the amaru decoders.
#
# After running, the 9 `cardano-node-cov-*-aflpp-smoke` scenarios can execute.
# Without it they SKIP with a clear "harness not provisioned" note (never a
# cryptic failure). Override the install location with DWARF_AFL_HARNESS_DIR.
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
HARNESS_PKG="$REPO_ROOT/antithesis/components/dwarf-adversary"
INSTALL_DIR=${DWARF_AFL_HARNESS_DIR:-/opt/dwarf/afl-harness}
VENDORED_AFL="$HARNESS_PKG/coverage-docker/sancov/afl-fuzz"

echo "==> AFL coverage harness build"
echo "    package     : $HARNESS_PKG"
echo "    install dir : $INSTALL_DIR"

if [ ! -d "$HARNESS_PKG" ]; then
  echo "!! harness package not found at $HARNESS_PKG" >&2
  exit 1
fi
if ! command -v cabal >/dev/null 2>&1; then
  echo "!! cabal not found. Install GHC 9.6.7 + cabal (see antithesis/components/dwarf-adversary/COVERAGE-HARNESS.md)." >&2
  exit 1
fi

echo "==> building dwarf-decode-any (heavy: GHC + cardano-node deps; minutes on a cold build)"
( cd "$HARNESS_PKG" && cabal build dwarf-decode-any )

BIN=$( cd "$HARNESS_PKG" && cabal list-bin dwarf-decode-any 2>/dev/null || true )
if [ -z "$BIN" ] || [ ! -x "$BIN" ]; then
  echo "!! build did not produce a runnable dwarf-decode-any (cabal list-bin returned: '$BIN')" >&2
  exit 1
fi

# Install dir (may need elevation for /opt).
if ! mkdir -p "$INSTALL_DIR" 2>/dev/null; then
  echo "==> $INSTALL_DIR needs elevated creation; using sudo"
  sudo mkdir -p "$INSTALL_DIR"
  sudo chown -R "$(id -un)" "$INSTALL_DIR"
fi

cp "$BIN" "$INSTALL_DIR/dwarf-decode-any"
chmod 755 "$INSTALL_DIR/dwarf-decode-any"

if [ -x "$VENDORED_AFL" ]; then
  cp "$VENDORED_AFL" "$INSTALL_DIR/afl-fuzz"
  chmod 755 "$INSTALL_DIR/afl-fuzz"
  AFL_VER=$("$INSTALL_DIR/afl-fuzz" --version 2>&1 | head -1 || echo "?")
  echo "==> installed vendored afl-fuzz ($AFL_VER)"
else
  echo "!! vendored afl-fuzz not found at $VENDORED_AFL — the system afl-fuzz must be 4.40c" >&2
  echo "   (a mismatched version fails the forkserver handshake)." >&2
fi

# Seed corpora the aflpp scenarios feed to AFL. They're in the repo but the
# scenarios reference them at the stable install path so a locked-down
# dashboard container can mount them in.
CORPORA_SRC="$HARNESS_PKG/corpora"
if [ -d "$CORPORA_SRC" ]; then
  cp -a "$CORPORA_SRC" "$INSTALL_DIR/corpora"
  echo "==> installed seed corpora ($(ls "$INSTALL_DIR/corpora" | wc -l | tr -d ' ') sets)"
else
  echo "!! seed corpora not found at $CORPORA_SRC" >&2
fi

echo ""
echo "==> DONE."
echo "    harness  : $INSTALL_DIR/dwarf-decode-any  ($(du -h "$INSTALL_DIR/dwarf-decode-any" | cut -f1))"
echo "    afl-fuzz : $INSTALL_DIR/afl-fuzz"
echo ""
echo "The 9 cardano-node-cov-*-aflpp-smoke scenarios point here by default."
echo "To use a build in another location instead, export before running:"
echo "    export DWARF_AFL_HARNESS=/path/to/dwarf-decode-any"
echo "    export DWARF_AFL_FUZZ=/path/to/afl-fuzz   # must be 4.40c"
