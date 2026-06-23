# ChainSync codec: `printf` format/argument mismatch crashes the unexpected-message diagnostic (`printf: bad formatting char 'd'`)

**Repo:** `IntersectMBO/ouroboros-network`
**Component:** `ouroboros-network-protocols` — `Ouroboros.Network.Protocol.ChainSync.Codec`
**Affected:** current `main`
(`ouroboros-network/protocols/lib/Ouroboros/Network/Protocol/ChainSync/Codec.hs`, lines 188/191/194)
and release `ouroboros-network-protocols-0.15.2.0`
(`.../src/Ouroboros/Network/Protocol/ChainSync/Codec.hs`, lines 245/248/251). Observed live in a
deployed `cardano-node`.

## Summary

The `decode` fallthrough branches that report an **unexpected message** for the `StNext`
(`SingCanAwait` / `SingMustReply`) and `StIntersect` states pass **four** arguments to a `printf`
format string that has only **three** conversion specifiers. The second argument (`show stok`, a
`String`) is consumed by the `%d` integer conversion, so `Text.Printf` raises
`errorBadFormat 'd'` → **`printf: bad formatting char 'd'`**.

The net effect: when a chain-sync consumer receives a well-formed-but-unexpected message while
awaiting a reply (or intersecting), instead of the intended diagnostic
`codecChainSync (<agency>) unexpected key (k, l)`, the codec throws an opaque
`printf: bad formatting char 'd'`, and the mini-protocol terminates on *that* exception. The peer
is still dropped (correct), but the diagnostic identifying *what* the misbehaving/malicious peer
sent is destroyed.

## Defective code (current `main`)

```haskell
case (key, len, stok) of
  ...
  (_, _, SingIdle) ->                                    -- OK: 2×%s, 4 args
    fail (printf "codecChainSync (%s, %s) unexpected key (%d, %d)"
                 (show (activeAgency :: ActiveAgency st)) (show stok) key len)
  (_, _, SingNext next) ->
    case next of
      SingCanAwait ->                                    -- BUG: 1×%s, 4 args
        fail (printf "codecChainSync (%s) unexpected key (%d, %d)"
                     (show (activeAgency :: ActiveAgency st)) (show stok) key len)
      SingMustReply ->                                   -- BUG: 1×%s, 4 args
        fail (printf "codecChainSync (%s) unexpected key (%d, %d)"
                     (show (activeAgency :: ActiveAgency st)) (show stok) key len)
  (_, _, SingIntersect) ->                               -- BUG: 1×%s, 4 args
    fail (printf "codecChainSync (%s) unexpected key (%d, %d)"
                 (show (activeAgency :: ActiveAgency st)) (show stok) key len)
```

The format string `"codecChainSync (%s) unexpected key (%d, %d)"` has conversions `%s %d %d`, but
the call supplies `(show agency) (show stok) key len`. `Text.Printf` binds left-to-right:

| conversion | argument | type | result |
|---|---|---|---|
| `%s` | `show agency` | `String` | ok |
| `%d` | `show stok`  | **`String`** | `errorBadFormat 'd'` → `printf: bad formatting char 'd'` |

The `SingIdle` branch (line 183) is correct — `(%s, %s)` matches its four arguments — which is why
an unexpected message in `StIdle` produces the proper message, but one in `StNext`/`StIntersect`
crashes the formatter.

This is isolated to the ChainSync codec. Every other protocol codec's fallthrough is internally
consistent: BlockFetch / KeepAlive / LocalTxMonitor / LocalTxSubmission use `(%s, %s)` with 4 args,
and TxSubmission2 / LocalStateQuery use `(%s)` with **3** args (`(show stok) key len`). Only the
three ChainSync `StNext`/`StIntersect` branches mix `(%s)` with 4 args.

## Reproduction

Protocol-level: have a chain-sync *server* send a well-formed message whose key is not legal for the
consumer's current state while the consumer is in `StNext`/`StIntersect` — e.g. send `MsgRequestNext`
(`0x81 0x00`, key 0) or `MsgDone` (`0x81 0x07`, key 7), or a second `MsgAwaitReply` after the first.
The consumer's `decode` is invoked with `stok = SingNext SingCanAwait` (or `SingMustReply` /
`SingIntersect`); no positive case matches, so it hits the buggy branch and throws.

Isolated (no network) — the formatter alone:

```haskell
import Text.Printf
main :: IO ()
main = putStrLn (printf "codecChainSync (%s) unexpected key (%d, %d)"
                        "ClientAgency" "SingCanAwait" (0 :: Word) (1 :: Int))
-- *** Exception: printf: bad formatting char 'd'
```

## Observed vs expected

- **Observed:** mini-protocol terminates with `printf: bad formatting char 'd'`
  (`Net.Mux.Remote.ExceptionExit`, `MiniProtocolNum 2`, `InitiatorDir`).
- **Expected:** a `DeserialiseFailure` carrying
  `codecChainSync (<agency>, <state>) unexpected key (<key>, <len>)`.

## Impact / severity

Low severity, but a real defect in security-relevant code:
- **Not** memory-unsafe and **not** a node crash. The peer is correctly rejected either way (the
  branch was going to `fail`/reject regardless).
- It **destroys the diagnostic** for an adversarial/buggy peer's protocol violation — operators
  triaging a misbehaving or malicious chain-sync peer get an opaque `printf` error instead of the
  offending message key/state. This is precisely the path that fires under a hostile peer.

## Proposed fix

Change the three `StNext`/`StIntersect` format strings to two `%s` conversions to match their
arguments (and the correct `SingIdle` branch):

```diff
-              fail (printf "codecChainSync (%s) unexpected key (%d, %d)"
+              fail (printf "codecChainSync (%s, %s) unexpected key (%d, %d)"
                            (show (activeAgency :: ActiveAgency st)) (show stok) key len)
```

(applied to the `SingCanAwait`, `SingMustReply`, and `SingIntersect` branches —
lines 188, 191, 194 on `main`).

## Discovery

Found by adversarial mini-protocol **state-machine fuzzing** (the DWARF project): a chain-sync
server that emits well-formed messages in illegal protocol state/agency. The condition recurred
deterministically across an 8-hour local soak (7,158 occurrences) and during two live Antithesis
runs (1h clean + 3h with fault injection); the node's safety assertions
(`Never: Cardano Node Errors` / `Critical`) held throughout — this is a diagnostic defect, not a
liveness/safety failure.
