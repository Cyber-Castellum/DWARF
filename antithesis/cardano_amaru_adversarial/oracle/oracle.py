"""DWARF adversarial differential oracle for the mixed Amaru net.

Two Amaru relays bootstrap from the same store, then:
  - amaru-relay-2 peers the HONEST cardano relay  -> control (adopts the real chain)
  - amaru-relay-1 peers the DWARF ADVERSARY       -> fed mutated / forged block CBOR

This oracle tails both relays' logs (shared amaru-logs volume) and emits Antithesis
SDK properties that formalize the differential:

  always     amaru-relay-1 never adopts a tip that is NOT on the honest chain
             (no forged-fork adoption) — the core safety property.
  always     amaru-relay-1 never gets AHEAD of the honest tip height (can only
             advance via forged blocks, since its sole peer is the adversary).
  always     neither Amaru relay panics / logs a fatal error under adversarial input.
  sometimes  amaru-relay-1 rejected a forged block at decode (the adversary is
             actually exercising Amaru's decoder — the test is really adversarial).
  sometimes  amaru-relay-2 advanced the honest chain (liveness works alongside the attack).

A failing `always` = Amaru accepted something forged that the honest node did not
= a real finding. The SDK is optional so the container is runnable locally.
"""
import os
import re
import time

try:
    from antithesis.assertions import always, sometimes, reachable
except Exception:  # local dry-run without the SDK
    def always(cond, message, details):
        if not cond:
            print(f"[ALWAYS-FAIL] {message} {details}", flush=True)
    def sometimes(cond, message, details):
        if cond:
            print(f"[sometimes-ok] {message} {details}", flush=True)
    def reachable(message, details):
        print(f"[reachable] {message} {details}", flush=True)

LOG_DIR = os.environ.get("AMARU_LOG_DIR", "/opt/amaru-logs")
ADV_LOG = os.path.join(LOG_DIR, os.environ.get("ADV_RELAY_LOG", "amaru-relay-1.log"))
HON_LOG = os.path.join(LOG_DIR, os.environ.get("HONEST_RELAY_LOG", "amaru-relay-2.log"))
POLL = float(os.environ.get("ORACLE_POLL_SECS", "5"))
# margin (in blocks) tolerated before flagging relay-1 as "ahead" of honest — absorbs
# ordinary bootstrap/propagation skew; a real forged advance grows without bound.
AHEAD_MARGIN = int(os.environ.get("ORACLE_AHEAD_MARGIN", "3"))

ADOPT_RE = re.compile(r"adopted tip tip\.slot=(\d+) tip\.hash=([0-9a-f]+).*?block_height=(\d+)")
DECODE_ERR_RE = re.compile(r"failed to decode message from network|Invalid CBOR|decode error")
FATAL_RE = re.compile(r"\bpanic(ked)?\b|\bFATAL\b|amaru::fatal")


def follow(path, state):
    """Return new text appended to path since last call (by byte offset)."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return ""
    off = state.get(path, 0)
    if size < off:          # rotated/truncated
        off = 0
    if size == off:
        return ""
    with open(path, "r", errors="replace") as f:
        f.seek(off)
        data = f.read()
        state[path] = f.tell()
    return data


def main():
    state = {}
    honest_hashes = set()      # every tip hash the honest relay has adopted
    honest_by_height = {}      # block_height -> honest hash
    honest_max_bh = 0
    adv_rejected = False
    honest_advanced_baseline = None

    while True:
        hon_new = follow(HON_LOG, state)
        adv_new = follow(ADV_LOG, state)

        for m in ADOPT_RE.finditer(hon_new):
            h, bh = m.group(2), int(m.group(3))
            honest_hashes.add(h)
            honest_by_height[bh] = h
            honest_max_bh = max(honest_max_bh, bh)
        if honest_advanced_baseline is None and honest_max_bh:
            honest_advanced_baseline = honest_max_bh

        # honest liveness under the concurrent attack
        if honest_advanced_baseline is not None and honest_max_bh > honest_advanced_baseline:
            sometimes(True, "honest amaru relay advances the real chain during the attack",
                      {"block_height": honest_max_bh})
            reachable("honest amaru relay reached a new tip", {"block_height": honest_max_bh})

        # the adversary is actually exercising Amaru's decoder
        if DECODE_ERR_RE.search(adv_new):
            adv_rejected = True
        if adv_rejected:
            sometimes(True, "adversarial amaru relay rejected a forged block at decode", {})

        for m in ADOPT_RE.finditer(adv_new):
            slot, h, bh = int(m.group(1)), m.group(2), int(m.group(3))
            # SAFETY 1: any tip relay-1 adopts at a height the honest relay has also
            # reached must be the SAME hash (i.e. on the honest chain). A mismatch
            # means relay-1 adopted a forged fork the honest node rejected.
            if bh in honest_by_height:
                always(honest_by_height[bh] == h,
                       "adversarial amaru relay never adopts a forged fork (its tip matches the honest chain at equal height)",
                       {"block_height": bh, "adv_hash": h, "honest_hash": honest_by_height[bh]})
            # SAFETY 2: relay-1's only peer is the adversary, so it must never climb
            # meaningfully ABOVE the honest tip — doing so requires adopting forged blocks.
            always(bh <= honest_max_bh + AHEAD_MARGIN,
                   "adversarial amaru relay never advances ahead of the honest chain (no forged advance)",
                   {"adv_block_height": bh, "honest_block_height": honest_max_bh})

        # robustness: forged input must not crash Amaru
        if FATAL_RE.search(adv_new):
            always(False, "adversarial amaru relay does not panic on forged input", {"log": ADV_LOG})
        if FATAL_RE.search(hon_new):
            always(False, "honest amaru relay does not panic", {"log": HON_LOG})

        time.sleep(POLL)


if __name__ == "__main__":
    main()
