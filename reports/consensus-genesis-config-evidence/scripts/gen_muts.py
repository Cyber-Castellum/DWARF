import json, copy, os
base=json.load(open("/tmp/cfgdiff/configs/shelley-genesis.base.json"))
def m(name, fn):
    g=copy.deepcopy(base); fn(g); 
    json.dump(g, open(f"/tmp/cfgdiff/muts/{name}.json","w"), indent=2)
    return name
muts=[]
muts.append(("baseline", lambda g: None))
muts.append(("asc_2.0", lambda g: g.update(activeSlotsCoeff=2.0)))
muts.append(("asc_0", lambda g: g.update(activeSlotsCoeff=0)))
muts.append(("asc_1.0", lambda g: g.update(activeSlotsCoeff=1.0)))
muts.append(("asc_neg", lambda g: g.update(activeSlotsCoeff=-0.1)))
muts.append(("asc_precise", lambda g: g.update(activeSlotsCoeff=0.123456789012345)))
muts.append(("asc_string", lambda g: g.update(activeSlotsCoeff="0.2")))
muts.append(("k_0", lambda g: g.update(securityParam=0)))
muts.append(("k_neg", lambda g: g.update(securityParam=-5)))
muts.append(("k_float", lambda g: g.update(securityParam=2.5)))
muts.append(("epoch_0", lambda g: g.update(epochLength=0)))
muts.append(("epoch_1", lambda g: g.update(epochLength=1)))
muts.append(("slotlen_0", lambda g: g.update(slotLength=0)))
muts.append(("asc_missing", lambda g: g.pop("activeSlotsCoeff",None)))
muts.append(("extra_field", lambda g: g.update(dwarfUnknownField="x")))
for n,fn in muts: m(n,fn)
json.dump([n for n,_ in muts], open("/tmp/cfgdiff/muts/_order.json","w"))
print("generated", len(muts), "mutations")
