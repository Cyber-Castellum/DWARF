#!/usr/bin/env python3
# Wrap a cardano-cli signed tx (TextEnvelope JSON with a "cborHex" field, OR raw
# CBOR-hex on argv) into the node-to-node wire GenTx envelope: [6, tag24(txbytes)]
# where 6 = the Conway era tag and tag 24 = CBOR-in-CBOR byte string. Emits raw
# bytes to the output file. No third-party deps (manual CBOR head construction).
#
# Usage: wrap_gentx.py <signed.tx | cborhex> <out.cbor>
import json, sys

def read_txbytes(src):
    # try TextEnvelope JSON first, else treat as hex string / file of hex
    try:
        with open(src) as f:
            j = json.load(f)
        return bytes.fromhex(j["cborHex"])
    except (json.JSONDecodeError, FileNotFoundError, KeyError):
        s = src
        try:
            with open(src) as f: s = f.read().strip()
        except FileNotFoundError:
            pass
        return bytes.fromhex(s)

def bstr_head(n):
    # CBOR byte-string major type 2 length header
    if n < 24:      return bytes([0x40 + n])
    if n < 0x100:   return bytes([0x58, n])
    if n < 0x10000: return bytes([0x59, n >> 8, n & 0xFF])
    return bytes([0x5A]) + n.to_bytes(4, "big")

def wrap(txbytes):
    # array(2) = 0x82 ; unsigned 6 = 0x06 ; tag 24 = 0xD8 0x18 ; byte-string(tx)
    return bytes([0x82, 0x06, 0xD8, 0x18]) + bstr_head(len(txbytes)) + txbytes

if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: wrap_gentx.py <signed.tx|cborhex> <out.cbor>")
    tx = read_txbytes(sys.argv[1])
    open(sys.argv[2], "wb").write(wrap(tx))
    print(f"wrote {sys.argv[2]}: wire GenTx = {4 + len(bstr_head(len(tx)))} envelope bytes + {len(tx)} tx bytes")
