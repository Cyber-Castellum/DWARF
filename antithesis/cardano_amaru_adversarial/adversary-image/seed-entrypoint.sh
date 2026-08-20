#!/bin/sh
# Derive the adversary mutation seed from ANTITHESIS randomness when running on the
# Antithesis platform. This is what makes the run explore MUTATIONS x FAULTS rather than
# one fixed attack under many fault schedules -- and because the entropy is Antithesis
# own, any failure it finds still replays deterministically.
#
# Off-platform (local validation) the SDK is absent or no-ops, so we fall back to a fixed
# seed and local runs stay byte-for-byte reproducible.
set -eu
SEED="${DWARF_ADV_SEED:-}"
if [ -z "$SEED" ]; then
  SEED=$(python3 -c "
try:
    from antithesis.random import get_random
    v = get_random()
    print(abs(int(v)) % (2**63 - 1))
except Exception:
    print(20260820)
" 2>/dev/null || echo 20260820)
fi
[ -n "$SEED" ] || SEED=20260820
echo "dwarf-adversary: mutation seed = $SEED (antithesis-derived if on-platform)" >&2
exec /usr/local/bin/dwarf-adversary --seed "$SEED" "$@"
