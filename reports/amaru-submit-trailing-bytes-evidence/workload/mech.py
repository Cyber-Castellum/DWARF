import urllib.request, urllib.error, re
A="http://127.0.0.1:3011/api/submit/tx"; C="http://127.0.0.1:8090/api/submit/tx"
def post(u,d):
    r=urllib.request.Request(u,data=d,headers={"Content-Type":"application/cbor"},method="POST")
    try:
        x=urllib.request.urlopen(r,timeout=10); return x.getcode(),x.read().decode("utf8","replace")
    except urllib.error.HTTPError as e: return e.code,e.read().decode("utf8","replace")
def amid(body):
    m=re.search(r"prepare transaction ([0-9a-f]+)",body); return m.group(1)[:16] if m else None

raw=open("/seeds/c01-plain.cbor","rb").read()
ac,ab=post(A,raw); cc,cb=post(C,raw)
clean_id=amid(ab)
print(f"CLEAN len={len(raw)}   amaru={ac} id={clean_id} | cardano={cc} {cb[:45]!r}")

print("\n--- TRAILING-BYTE test (append junk to a valid tx) ---")
for name,extra in [("+1x 0xff", b"\xff"), ("+3 bytes", b"\x00\x01\x02"), ("+map a26161", bytes.fromhex("a26161"))]:
    d=raw+extra
    ac,ab=post(A,d); cc,cb=post(C,d)
    mid=amid(ab)
    tag=" SAME-ID(trailing-ignored)" if mid==clean_id else ""
    print(f"{name:14} amaru={ac} id={mid}{tag} | cardano={cc} {cb[:55]!r}")

print("\n--- ARITY-MUTATION id test (does amaru id change vs clean?) ---")
# shrink the first inner array header we find after byte 5
for i,b in enumerate(raw):
    if i>5 and 0x81<=b<=0x9b:
        mut=bytearray(raw); mut[i]=b-1; d=bytes(mut)
        ac,ab=post(A,d); cc,cb=post(C,d)
        print(f"@byte{i} 0x{b:02x}->0x{b-1:02x}: amaru={ac} id={amid(ab)} (clean={clean_id}) | cardano={cc} {cb[:45]!r}")
        break
