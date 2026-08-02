import glob, os, urllib.request, urllib.error

AMARU="http://127.0.0.1:3011/api/submit/tx"
CARDANO="http://127.0.0.1:8090/api/submit/tx"

CARDANO_DECODE_FAIL=["DecoderErrorDeserialiseFailure","DeserialiseFailure","Deserialisation",
    "TxCmdTxReadError","expected list len","Size mismatch","end of input","unknown tag"]
CARDANO_VALIDATE=["ValidationError","ConwayMempoolFailure","ShelleyTxValidation","UtxoFailure",
    "BadInputsUTxO","ValueNotConserved","failed to","OutsideValidityInterval","FeeTooSmall","MempoolFailure"]
AMARU_DECODE_FAIL=["Invalid CBOR","decode error","array length mismatch","missing value at index",
    "unexpected","expected","insufficient bytes","invalid"]
AMARU_VALIDATE=["failed to prepare transaction","validation"]

def post(url, data):
    req=urllib.request.Request(url, data=data, headers={"Content-Type":"application/cbor"}, method="POST")
    try:
        r=urllib.request.urlopen(req, timeout=10); return r.getcode(), r.read().decode("utf8","replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf8","replace")
    except Exception as e:
        return -1, f"CONNERR {e}"

def decoded(node, code, body):
    # returns True if the bytes DECODED as a tx (i.e. failure is at validation, not decode); None if accepted(2xx)
    if code//100==2: return True
    b=body
    if node=="amaru":
        if any(m.lower() in b.lower() for m in AMARU_VALIDATE): return True
        if any(m.lower() in b.lower() for m in AMARU_DECODE_FAIL): return False
    else:
        if any(m in b for m in CARDANO_DECODE_FAIL): return False
        if any(m in b for m in CARDANO_VALIDATE): return True
    return None  # unknown

def both(data):
    ca=post(CARDANO,data); am=post(AMARU,data)
    if ca[0]==-1 or am[0]==-1: return ("CONN", ca, am, None)
    dc=decoded("cardano",*ca); da=decoded("amaru",*am)
    return (dc, da, ca, am)

seeds=sorted(glob.glob("/seeds/*.cbor"))
print(f"=== BASELINE (clean seeds) — {len(seeds)} ===")
base={}
for s in seeds:
    raw=open(s,"rb").read(); n=os.path.basename(s)
    dc,da,ca,am=both(raw)
    base[n]=raw
    flag="  <<< DECODE DIVERGENCE" if (dc is not None and da is not None and dc!=da) else ""
    print(f"{n:28} cardano(decoded={dc}, {ca[0]}) amaru(decoded={da}, {am[0]}){flag}")

print("\n=== MUTATION SWEEP (array/map length headers +/-1, + top-level arity) ===")
divs=0; tested=0
for n,raw in base.items():
    positions=set([0])  # always try top-level arity (found #1)
    for i,b in enumerate(raw):
        # CBOR major type 4 (array) 0x80-0x9b, major type 5 (map) 0xa0-0xbb : immediate-count headers
        if 0x80<=b<=0x9b or 0xa0<=b<=0xbb:
            positions.add(i)
    for i in sorted(positions):
        for delta in (-1,+1):
            nb=raw[i]+delta
            if not (0<=nb<=0xff): continue
            # keep same major type nibble region (don't cross into a different type)
            if (raw[i]&0xe0)!=(nb&0xe0): continue
            mut=bytearray(raw); mut[i]=nb; mut=bytes(mut)
            dc,da,ca,am=both(mut); tested+=1
            if dc is not None and da is not None and dc!=da:
                divs+=1
                print(f"DIVERGENCE {n} @byte{i} 0x{raw[i]:02x}->0x{nb:02x}: "
                      f"cardano(decoded={dc},{ca[0]}: {ca[1][:70]!r}) | amaru(decoded={da},{am[0]}: {am[1][:70]!r})")
print(f"\n=== done: {tested} mutations tested, {divs} decode-divergences ===")
