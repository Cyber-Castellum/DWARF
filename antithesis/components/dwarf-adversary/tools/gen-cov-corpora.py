#!/usr/bin/env python3
"""Generate the dwarf-decode-any coverage-harness seed corpora.

Produces three corpora under corpora/ (relative to the dwarf-adversary package):
  tx/     wire-GenTx seeds (copied from dist-fuzz/corpus)
  block/  200 individual block-fetch wire blocks, split out of the CBOR
          array in ../../cardano_node_dwarf_baked/baked-blocks.cbor
  txbody/ raw Conway TxBody bytes, extracted (element 0 of the inner Conway tx
          array) from each wire-GenTx seed

Run from the dwarf-adversary package dir:  python3 tools/gen-cov-corpora.py
"""
import glob
import os
import shutil
import struct
import sys

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BAKED = os.path.join(PKG, "..", "..", "cardano_node_dwarf_baked", "baked-blocks.cbor")
TXSRC = os.path.join(PKG, "dist-fuzz", "corpus")
OUT = os.path.join(PKG, "corpora")


def item_end(b, i):
    """Return the index one past the CBOR item starting at b[i]."""
    a = b[i]; mt = a >> 5; ai = a & 0x1F; i += 1
    if ai < 24: val = ai
    elif ai == 24: val = b[i]; i += 1
    elif ai == 25: val = struct.unpack(">H", b[i:i + 2])[0]; i += 2
    elif ai == 26: val = struct.unpack(">I", b[i:i + 4])[0]; i += 4
    elif ai == 27: val = struct.unpack(">Q", b[i:i + 8])[0]; i += 8
    else: raise ValueError("indefinite/reserved length")
    if mt in (0, 1, 7): return i
    if mt in (2, 3): return i + val
    if mt == 4:
        for _ in range(val): i = item_end(b, i)
        return i
    if mt == 5:
        for _ in range(val): i = item_end(b, i); i = item_end(b, i)
        return i
    if mt == 6: return item_end(b, i)
    raise ValueError("bad major type %d" % mt)


def nth(b, i, n):
    """(start,end) of element n of the CBOR array whose header is at b[i]."""
    ai = b[i] & 0x1F; i += 1
    if ai == 24: i += 1
    elif ai == 25: i += 2
    elif ai == 26: i += 4
    for _ in range(n): i = item_end(b, i)
    return i, item_end(b, i)


def bstr_payload(b, j):
    """(start,end) of the byte-string payload whose header is at b[j]."""
    ai = b[j] & 0x1F; j += 1
    if ai == 24: ln = b[j]; j += 1
    elif ai == 25: ln = struct.unpack(">H", b[j:j + 2])[0]; j += 2
    elif ai == 26: ln = struct.unpack(">I", b[j:j + 4])[0]; j += 4
    else: ln = ai
    return j, j + ln


def gen_blocks():
    d = os.path.join(OUT, "block"); os.makedirs(d, exist_ok=True)
    b = open(BAKED, "rb").read()
    assert b[0] == 0x98, "expected array(<=255) header"
    n = b[1]; i = 2; cnt = 0
    for k in range(n):
        start = i
        assert b[i] == 0xD8 and b[i + 1] == 0x18, "expected tag 24"
        i += 2
        _, e = bstr_payload(b, i); i = e
        open(os.path.join(d, "blk-%03d.cbor" % k), "wb").write(b[start:i]); cnt += 1
    print("block: wrote %d seeds" % cnt)


def gen_txbody():
    """Emit corpora/txbody (raw Conway TxBody = element 0) and corpora/conwaytx
    (the full inner Conway tx = the tag24 payload), both from the wire-GenTx
    corpus."""
    dtb = os.path.join(OUT, "txbody"); os.makedirs(dtb, exist_ok=True)
    dctx = os.path.join(OUT, "conwaytx"); os.makedirs(dctx, exist_ok=True)
    cnt = 0
    for f in sorted(glob.glob(os.path.join(TXSRC, "*"))):
        b = open(f, "rb").read()
        try:
            s1, _ = nth(b, 0, 1)            # element 1 of outer [6, tag24(tx)]
            j = s1
            assert b[j] == 0xD8 and b[j + 1] == 0x18; j += 2
            ps, pe = bstr_payload(b, j)
            conway = b[ps:pe]               # full Conway tx [body, wits, isvalid, aux]
            open(os.path.join(dctx, "ctx-%02d.cbor" % cnt), "wb").write(conway)
            s0, e0 = nth(conway, 0, 0)      # element 0 = tx_body
            open(os.path.join(dtb, "tb-%02d.cbor" % cnt), "wb").write(conway[s0:e0])
            cnt += 1
        except Exception as ex:
            print("  skip %s: %s" % (os.path.basename(f), ex), file=sys.stderr)
    print("txbody + conwaytx: wrote %d seeds each" % cnt)


def gen_tx():
    d = os.path.join(OUT, "tx"); os.makedirs(d, exist_ok=True)
    cnt = 0
    for f in sorted(glob.glob(os.path.join(TXSRC, "*"))):
        shutil.copy(f, os.path.join(d, os.path.basename(f))); cnt += 1
    print("tx: copied %d seeds" % cnt)


def gen_miniprotocol():
    """Hand-crafted minimal N2N mini-protocol wire messages (AFL evolves from
    these). keepalive: StServer decodes MsgKeepAliveResponse=[1,word16];
    txsub: StIdle decodes MsgRequestTxIds=[0,bool,word16,word16] / RequestTxs."""
    ka = {
        "ka-resp-0": bytes([0x82, 0x01, 0x19, 0x00, 0x00]),
        "ka-resp-42": bytes([0x82, 0x01, 0x19, 0x00, 0x2A]),
        "ka-req": bytes([0x82, 0x00, 0x19, 0xFF, 0xFF]),
        "ka-done": bytes([0x81, 0x02]),
    }
    tx = {
        "req-ids-nb": bytes([0x84, 0x00, 0xF4, 0x00, 0x00, 0x00, 0x0A]),
        "req-ids-bl": bytes([0x84, 0x00, 0xF5, 0x00, 0x00, 0x00, 0x05]),
        "req-txs": bytes([0x82, 0x01, 0x80]),
        "msg-init": bytes([0x81, 0x06]),
    }
    hs = {  # StConfirm: MsgReplyVersions / MsgAcceptVersion / MsgRefuse
        "reply-empty": bytes([0x82, 0x01, 0xA0]),
        "reply-v": bytes([0x82, 0x01, 0xA1, 0x07, 0xF6]),
        "accept": bytes([0x83, 0x02, 0x07, 0xF6]),
        "refuse": bytes([0x82, 0x03, 0x83, 0x00, 0x81, 0x07, 0x60]),
    }
    for name, seeds in (("keepalive", ka), ("txsub", tx), ("handshake", hs)):
        d = os.path.join(OUT, name); os.makedirs(d, exist_ok=True)
        for n, b in seeds.items():
            open(os.path.join(d, n + ".cbor"), "wb").write(b)
        print("%s: wrote %d seeds" % (name, len(seeds)))


if __name__ == "__main__":
    gen_tx(); gen_blocks(); gen_txbody(); gen_miniprotocol()
    print("done ->", OUT)
