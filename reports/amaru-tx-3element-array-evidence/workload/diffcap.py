import os, sys
os.environ["WORKLOAD_TARGETS"] = f"amaru={os.environ['AIP']}:3011,cardano={os.environ['CIP']}:8090"
os.environ["WORKLOAD_CORPUS"] = "/wl/corpus"
sys.path.insert(0, "/wl")
import workload

print("DWARF submit-api differential — same mutated tx to Amaru + cardano-node")
print("decode_agree=False  => the implementations disagree on whether the bytes decode as a tx (the finding)")
print("-" * 100)
splits = 0
for s in range(60):
    r = workload.drive_submit(seed=s)
    det = " | ".join(
        f"{l}: decoded={d['decoded']} status={d['status']} reason={(d['reason'] or '')[:46]!r}"
        for l, d in r["detail"].items())
    flag = "  <<< DECODE DIVERGENCE" if not r["decode_agree"] else ""
    print(f"seed={s:3d} accept_agree={r['agree']} decode_agree={r['decode_agree']} :: {det}{flag}")
    if not r["decode_agree"]:
        splits += 1
print("-" * 100)
print(f"=== decode-divergences: {splits}/60 ===")
