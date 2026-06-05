# cardano-node shims (Haskell)

M2 shim binaries that call cardano-node/cardano-ledger decode surfaces and emit the Dwarf shim outcome contract (`OK` / `ERR <reason>` / crash).

## Targets

| Shim binary | Decodes (Conway era) |
|---|---|
| `cardano-node-cbor-decode-tx-body` | `Cardano.Ledger.Api.Tx.Body.TxBody Conway` |
| `cardano-node-cbor-decode-block-header` | `Cardano.Protocol.TPraos.BHeader.BHeader StandardCrypto` |
| `cardano-node-cbor-decode-certificate` | `Cardano.Ledger.Api.Tx.Cert.TxCert Conway` |
| `cardano-node-cbor-decode-auxiliary-data` | `Cardano.Ledger.Core.TxAuxData Conway` |
| `cardano-node-cbor-decode-block` | `Cardano.Ledger.Block.Block (BHeader StandardCrypto) Conway` |
| `cardano-node-mini-protocol-decode-*` | Retained mini-protocol message decoders |

## Build

```bash
make -C dwarf/targets cardano-node           # cabal build all
make -C dwarf/targets cardano-node-install   # stage into dwarf/targets/cardano-node/bin/
make -C dwarf/targets update                 # refresh manifests with current git sha + binary path
```

Dependencies resolve via `cabal.project` → CHaP (Cardano's Haskell package repo). The index-state pins in `cabal.project` are copied from `codebases/cardano-node/cabal.project` and should be refreshed together when bumping either.

### First build is slow

GHC + cabal + CHaP rebuild from scratch takes 30-60 minutes and ~10 GB of disk. The second build uses the cabal store and is fast.

## Shim shape

Each `src/*.hs` is ~20 lines with this structure:

1. Read CBOR bytes from stdin.
2. Call `decodeFullAnnotator` (for annotator-wrapped types like `TxBody`, `BHeader`, `Block`, `TxAuxData`) or `decodeFull` (for plain types like `TxCert`).
3. On `Right _` print `OK` and exit 0; on `Left err` print `ERR <show err>` and exit 1. Any panic, signal, non-0/1 exit is classified `crash` by Dwarf.

## API variance

The exact decoder signatures vary slightly between cardano-ledger minor versions (e.g. `decodeFull` vs `decodeFull'`, `decCBOR` import path). If `cabal build all` fails at the first attempt after a CHaP bump:

- inspect `Cardano.Ledger.Binary.Decoding` in the resolved ledger-binary package for the current signatures
- adjust the relevant `src/*.hs` files
- re-run `make cardano-node-install && make update`

The shims are intentionally small so this is usually a narrow change per file.
