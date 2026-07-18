import json, copy, os
base=json.load(open("/tmp/cfgsweep/base.json"))
# numeric/edge value palette
EDGE = {
  "zero":0, "one":1, "neg1":-1, "negf":-0.5, "half":0.5, "float":2.5,
  "big63":2**63, "big64":2**64, "huge":10**30, "tiny":1e-300,
  "str":"5", "null":None, "bool":True, "emptystr":"", "sci":1e18,
}
# top-level shelley-genesis numeric fields to sweep
FIELDS = ["activeSlotsCoeff","securityParam","epochLength","slotLength","maxKESEvolutions",
          "slotsPerKESPeriod","updateQuorum","maxLovelaceSupply","networkMagic","systemStart",
          "protocolParams","genDelegs","initialFunds"]
# nested protocolParams numeric fields
PP_FIELDS = ["a0","rho","tau","decentralisationParam","minFeeA","minFeeB","maxBlockBodySize",
             "maxTxSize","keyDeposit","poolDeposit","eMax","nOpt","protocolVersion"]
n=0
def dump(name, g):
    global n; json.dump(g, open(f"/tmp/cfgsweep/muts/{name}.json","w")); n+=1
dump("_baseline", copy.deepcopy(base))
for f in FIELDS:
    for en,ev in EDGE.items():
        g=copy.deepcopy(base); g[f]=ev; dump(f"top.{f}.{en}", g)
    g=copy.deepcopy(base); g.pop(f,None); dump(f"top.{f}.missing", g)
pp=base.get("protocolParams",{})
for f in PP_FIELDS:
    if f not in pp: continue
    for en,ev in EDGE.items():
        g=copy.deepcopy(base); g["protocolParams"][f]=ev; dump(f"pp.{f}.{en}", g)
    g=copy.deepcopy(base); g["protocolParams"].pop(f,None); dump(f"pp.{f}.missing", g)
print("generated", n, "mutations")
