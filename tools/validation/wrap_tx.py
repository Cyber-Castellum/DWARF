#!/usr/bin/env python3
# Wrap a cardano-cli signed-tx file (TextEnvelope JSON w/ cborHex = raw Conway tx)
# into the HardFork N2N GenTx wire envelope: CBOR [6, tag24(rawtx)], so it can be
# served by the dwarf-adversary via --seed-tx (decoded by decTx).
import json, sys
def bstr_header(n):
    if n <= 23: return bytes([0x40|n])
    if n <= 255: return bytes([0x58, n])
    if n <= 65535: return bytes([0x59, n>>8, n&0xff])
    return bytes([0x5a, (n>>24)&0xff,(n>>16)&0xff,(n>>8)&0xff,n&0xff])
infile, outfile = sys.argv[1], sys.argv[2]
d = json.load(open(infile))
raw = bytes.fromhex(d["cborHex"])
wire = bytes([0x82, 0x06, 0xd8, 0x18]) + bstr_header(len(raw)) + raw
open(outfile,"wb").write(wire)
print(f"wrapped {infile} ({len(raw)}B raw) -> {outfile} ({len(wire)}B wire)")
