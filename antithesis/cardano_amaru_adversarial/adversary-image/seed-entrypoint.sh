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
  SEED_OUTPUT=$(python3 -c "
try:
    from antithesis.random import get_random
    v = get_random()
    print(abs(int(v)) % (2**63 - 1))
except Exception:
    print(20260820)
" 2>/dev/null || true)
  # The SDK may announce its assertion sink on stdout before printing the
  # random value. Accept only a complete unsigned-decimal line.
  SEED=$(printf '%s\n' "$SEED_OUTPUT" \
    | sed -n 's/^[[:space:]]*\([0-9][0-9]*\)[[:space:]]*$/\1/p' \
    | tail -n 1)
fi
[ -n "$SEED" ] || SEED=20260820
case "$SEED" in
  *[!0-9]*) SEED=20260820 ;;
esac
echo "dwarf-adversary: mutation seed = $SEED (antithesis-derived if on-platform)" >&2
DWARF_ADVERSARY_BIN=${DWARF_ADVERSARY_BIN:-/usr/local/bin/dwarf-adversary}
exec "$DWARF_ADVERSARY_BIN" --seed "$SEED" "$@"
