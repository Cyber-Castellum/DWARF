#!/usr/bin/env python3
"""cov_campaign_harvest.py — snapshot the running native-SanCov AFL++ coverage
campaigns (one AFL output dir per surface under a root) into:

  * a SARIF 2.1.0 evidence bundle (rules: cov-crash = uncaught exception / RTS
    abort -> SIGABRT; cov-hang = non-termination/timeout; results = any saved
    crashes/hangs, 0 expected) with per-surface coverage metrics in properties.
  * a markdown report (per-surface edges/execs/stability/cycles/crashes).

Designed to run periodically while the campaigns are live, so the report + SARIF
grow "as it goes". Reads root-owned AFL files; run under sudo.

Usage: cov_campaign_harvest.py <root> <surfaces-csv> <out-sarif> <out-md> [schema.json]
"""
import json
import os
import sys


def _find(root, surface):
    """Locate the fuzzer_stats + crashes/hangs dirs for one surface."""
    base = os.path.join(root, surface)
    stats = None
    for dirpath, _dirs, files in os.walk(base):
        if "fuzzer_stats" in files:
            stats = os.path.join(dirpath, "fuzzer_stats")
            break
    crashes, hangs = [], []
    for dirpath, _dirs, files in os.walk(base):
        leaf = os.path.basename(dirpath)
        for f in files:
            if f == "README.txt":
                continue
            if leaf == "crashes":
                crashes.append(os.path.join(dirpath, f))
            elif leaf == "hangs":
                hangs.append(os.path.join(dirpath, f))
    return stats, crashes, hangs


def _parse_stats(path):
    out = {}
    if not path or not os.path.exists(path):
        return out
    with open(path) as fh:
        for line in fh:
            if ":" in line:
                k, _, v = line.partition(":")
                out[k.strip()] = v.strip()
    return out


SURFACE_ENTRY = {
    "tx": "wire GenTx -> Conway tx decode",
    "block": "full block decode (widest)",
    "header": "Praos header decode",
    "ledger": "Conway TxBody + getMinFeeTxUtxo",
    "applytx": "applyTx (mempool LEDGER STS)",
    "applyblock": "applyBlock (BBODY->LEDGERS->per-tx rules) over genesis NewEpochState",
    "handshake": "N2N handshake codec decode",
    "txsub": "tx-submission2 codec decode",
    "keepalive": "keep-alive codec decode",
}

RULES = [
    {
        "id": "cov-crash",
        "name": "UncaughtExceptionOrAbort",
        "shortDescription": {"text": "A non-DeserialiseFailure exception or RTS abort reached the harness (SIGABRT)."},
        "defaultConfiguration": {"level": "error"},
    },
    {
        "id": "cov-hang",
        "name": "NonTermination",
        "shortDescription": {"text": "The harness exceeded the per-input time budget (hang / DoS candidate)."},
        "defaultConfiguration": {"level": "warning"},
    },
]


def main():
    root, surfaces_csv, out_sarif, out_md = sys.argv[1:5]
    schema = sys.argv[5] if len(sys.argv) > 5 else None
    surfaces = [s for s in surfaces_csv.split(",") if s]

    runs, md = [], []
    md.append("# DWARF native-SanCov exhaustive fuzz campaign — live report\n")
    md.append("Coverage-guided AFL++ over the cardano-node (Haskell) decode + ledger surfaces "
              "(GHC SanitizerCoverage). Oracle: clean DeserialiseFailure/validation reject -> exit 0; "
              "any uncaught exception / RTS abort -> SIGABRT (crash); hang -> timeout.\n")
    md.append("| surface | entrypoint | run_s | execs | edges | corpus | cycles | stability | crashes | hangs |")
    md.append("|---|---|--:|--:|--:|--:|--:|--:|--:|--:|")

    total_crashes = total_hangs = 0
    for s in surfaces:
        stats_path, crashes, hangs = _find(root, s)
        st = _parse_stats(stats_path)
        total_crashes += len(crashes)
        total_hangs += len(hangs)
        results = []
        for c in crashes:
            results.append({
                "ruleId": "cov-crash", "level": "error",
                "message": {"text": f"{s}: saved crash input {os.path.basename(c)}"},
                "locations": [{"physicalLocation": {"artifactLocation": {"uri": c}}}],
            })
        for h in hangs:
            results.append({
                "ruleId": "cov-hang", "level": "warning",
                "message": {"text": f"{s}: saved hang input {os.path.basename(h)}"},
                "locations": [{"physicalLocation": {"artifactLocation": {"uri": h}}}],
            })
        runs.append({
            "tool": {"driver": {
                "name": "dwarf-cov-fuzz",
                "informationUri": "https://github.com/Cyber-Castellum/DWARF",
                "version": "0.2",
                "rules": RULES,
            }},
            "invocations": [{
                "executionSuccessful": True,
                "commandLine": f"dwarf-cov-run {s}",
                "properties": {"surface": s, "entrypoint": SURFACE_ENTRY.get(s, s)},
            }],
            "results": results,
            "properties": {
                "surface": s,
                "edges_found": st.get("edges_found"),
                "execs_done": st.get("execs_done"),
                "execs_per_sec": st.get("execs_per_sec"),
                "corpus_count": st.get("corpus_count"),
                "cycles_done": st.get("cycles_done"),
                "stability": st.get("stability"),
                "bitmap_cvg": st.get("bitmap_cvg"),
                "run_time": st.get("run_time"),
                "saved_crashes": st.get("saved_crashes"),
                "saved_hangs": st.get("saved_hangs"),
            },
        })
        md.append("| {s} | {e} | {rt} | {ex} | {ed} | {cc} | {cy} | {stab} | {cr} | {hg} |".format(
            s=s, e=SURFACE_ENTRY.get(s, s),
            rt=st.get("run_time", "-"), ex=st.get("execs_done", "-"),
            ed=st.get("edges_found", "-"), cc=st.get("corpus_count", "-"),
            cy=st.get("cycles_done", "-"), stab=st.get("stability", "-"),
            cr=len(crashes), hg=len(hangs)))

    sarif = {
        "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json",
        "version": "2.1.0",
        "runs": runs,
    }
    with open(out_sarif, "w") as fh:
        json.dump(sarif, fh, indent=2)

    md.append(f"\n**Totals:** crashes={total_crashes}, hangs={total_hangs} across {len(surfaces)} surfaces.")
    md.append(f"\nSARIF: `{out_sarif}` ({len(runs)} runs).")
    with open(out_md, "w") as fh:
        fh.write("\n".join(md) + "\n")

    valid = "unchecked"
    if schema and os.path.exists(schema):
        try:
            import jsonschema  # type: ignore
            jsonschema.validate(sarif, json.load(open(schema)))
            valid = "valid"
        except ImportError:
            valid = "no-jsonschema"
        except Exception as e:  # noqa: BLE001
            valid = f"INVALID: {e}"
    print(f"harvest: surfaces={len(surfaces)} crashes={total_crashes} hangs={total_hangs} sarif_schema={valid}")


if __name__ == "__main__":
    main()
