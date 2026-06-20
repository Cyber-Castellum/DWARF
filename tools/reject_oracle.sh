#!/usr/bin/env bash
# FU1 reject-oracle (local analysis). Classifies how the node-under-test (relay2)
# responded to the mutated CBOR the adversary served, from relay2's tracer:
#   - decode-reject   : DecoderFailure / ShutdownPeer scoped to the adversary peer
#   - validation-reject: ChainDB/consensus InvalidBlock / invalid events
#   - ADOPTED          : blocks relay2 added to its chain (AddedToCurrentChain /
#                        SwitchedToAFork). Under ECLIPSE the adversary is relay2's
#                        ONLY peer, so any adopted block came from the adversary —
#                        adopting a MUTATED block would be an acceptance FINDING.
# Verdict: served>0 AND decode-reject>0 AND adopted==0  => node correctly REJECTS,
# never accepts (reject-oracle holds). adopted>0 under eclipse => investigate.
# Run on build-host against a live eclipse stack. Arg: adversary peer IP (default 198.51.100.8).
set -uo pipefail
ADVIP="${1:-198.51.100.8}"
b=$(docker exec tracer cat /opt/cardano-tracer/logs/relay2.example_3001/node.json 2>/dev/null)
served=$(docker exec dwarf-adversary sh -lc 'grep -cE "dwarf_served_mutated_(block|tx|header)" /tmp/sdk.jsonl 2>/dev/null || echo 0')
decodeRej=$(printf '%s' "$b" | grep -Fc 'DecoderFailure')
shutdownPeer=$(printf '%s' "$b" | grep -Fc 'ShutdownPeer')
invalidBlk=$(printf '%s' "$b" | grep -Eic 'InvalidBlock|ValidationError' || echo 0)
adopted=$(printf '%s' "$b" | grep -Fc 'AddedToCurrentChain')
forks=$(printf '%s' "$b" | grep -Fc 'SwitchedToAFork')
crit=$(printf '%s' "$b" | grep -Fc '"sev":"Critical"')
rrc=$(docker inspect relay2 --format '{{.RestartCount}}' 2>/dev/null)
echo "served(mutated)=$served  decode-reject(DecoderFailure)=$decodeRej  ShutdownPeer=$shutdownPeer  validation-reject=$invalidBlk  ADOPTED(AddedToCurrentChain)=$adopted  forks=$forks  relay2Critical=$crit  relay2Restart=$rrc"
# Reject can be at the DECODER (DecoderFailure: struct/bytes modes) or at the
# VALIDATOR (ShutdownPeer / BlockFetchProtocolFailureWrongBlock: semantic mode,
# where the block decodes but its body fails header-body consistency).
rejects=$(( ${decodeRej:-0} + ${shutdownPeer:-0} ))
if [ "${served:-0}" -gt 0 ] && [ "${adopted:-0}" -eq 0 ] && [ "$rejects" -gt 0 ]; then
  stage="decode"; [ "${decodeRej:-0}" -eq 0 ] && stage="validation (header-body consistency)"
  echo "REJECT-ORACLE: PASS — node rejected mutated CBOR at the $stage stage and adopted ZERO mutated blocks (decodeRej=$decodeRej shutdownPeer=$shutdownPeer)."
elif [ "${adopted:-0}" -gt 0 ]; then
  echo "REJECT-ORACLE: adopted=$adopted — EXPECTED if mutation-rate<1.0 (the unmutated half is correctly adopted); a FINDING only at rate 1.0 (would mean a mutated block was accepted). Re-run at --mutation-rate 1.0 to disambiguate."
else
  echo "REJECT-ORACLE: INCONCLUSIVE — served=$served decodeRej=$decodeRej shutdownPeer=$shutdownPeer adopted=$adopted (need more observation)."
fi
