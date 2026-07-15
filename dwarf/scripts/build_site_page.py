#!/usr/bin/env python3
"""
Build a self-contained DWARF site page that = the existing docs/index.html
+ two new Reference sections (Scenario DSL + Primitive catalogue) rendered in
the site's own design language, with inline-SVG/CSS visualizations.

Content is reused verbatim from gen_reference.py (build_spec / build_primitives),
so the site page and the standalone docs never diverge.

Usage: python3 build_site_page.py <dwarf_dir> <base_index.html> <out.html>
"""
import importlib.util, json, os, sys, html, re, glob
from collections import defaultdict

DWARF   = sys.argv[1]
BASE    = sys.argv[2]
OUTFILE = sys.argv[3]
HERE    = os.path.dirname(os.path.abspath(__file__))

# import the generator as a library
spec = importlib.util.spec_from_file_location("genref", os.path.join(HERE, "gen_reference.py"))
G = importlib.util.module_from_spec(spec); spec.loader.exec_module(G)
G.DWARF = DWARF  # point content builders at the real dwarf dir
mdi = G.md_inline_to_html

reg = json.load(open(os.path.join(DWARF, "primitives/registry.json")))["primitives"]

# ---------------------------------------------------------------- block helpers
def drop_h1(blocks):
    return [b for b in blocks if not (b[0]=="h" and b[1]==1)]

def drop_section(blocks, h2title):
    """Remove an H2 heading with the given title and every block until the next H2."""
    out=[]; skipping=False
    for b in blocks:
        if b[0]=="h" and b[1]==2:
            skipping = (b[2].strip().lower()==h2title.strip().lower())
        if not skipping: out.append(b)
    return out

def drop_notes(blocks):
    return [b for b in blocks if b[0]!="note" or "Coverage:" not in b[1] and "generated from" not in b[1].lower()]

# ---------------------------------------------------------------- DWARF-skinned renderers
def cells_html(cells):
    return "".join(f"<td>{mdi(str(c))}</td>" for c in cells)

def render_dwarf(blocks):
    out=[]
    for b in blocks:
        if b[0]=="h":
            lvl=b[1]
            cls={2:"ref-h2",3:"ref-h3",4:"ref-h4"}.get(lvl,"ref-h3")
            out.append(f'<h{min(lvl+1,4)} id="{G.slug(b[2])}" class="{cls}">{mdi(b[2])}</h{min(lvl+1,4)}>')
        elif b[0]=="p":
            out.append(f'<p class="ref-p">{mdi(b[1])}</p>')
        elif b[0]=="note":
            out.append(f'<div class="ref-note">{mdi(b[1])}</div>')
        elif b[0]=="code":
            out.append(f'<pre class="ref-code"><code>{html.escape(b[1])}</code></pre>')
        elif b[0]=="ul":
            out.append('<ul class="ref-ul">'+"".join(f"<li>{mdi(i)}</li>" for i in b[1])+"</ul>")
        elif b[0]=="table":
            th="".join(f"<th>{mdi(h)}</th>" for h in b[1])
            tr="".join(f"<tr>{cells_html(r)}</tr>" for r in b[2])
            out.append(f'<div class="ref-tablewrap"><table class="ref-table"><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table></div>')
    return "".join(out)

def render_catalog(blocks):
    """Like render_dwarf, but wrap each H3 + its table in a collapsible <details>."""
    out=[]; open_det=False
    def close():
        nonlocal open_det
        if open_det: out.append("</div></details>"); open_det=False
    for b in blocks:
        if b[0]=="h" and b[1]==2:
            close(); out.append(f'<h3 id="{G.slug(b[2])}" class="ref-h2">{mdi(b[2])}</h3>')
        elif b[0]=="h" and b[1]==3:
            close()
            out.append(f'<details class="ref-details" open><summary>{mdi(b[2])}</summary><div class="ref-detbody">')
            open_det=True
        elif b[0]=="p":
            out.append(f'<p class="ref-p">{mdi(b[1])}</p>')
        elif b[0]=="note":
            out.append(f'<div class="ref-note">{mdi(b[1])}</div>')
        elif b[0]=="table":
            th="".join(f"<th>{mdi(h)}</th>" for h in b[1])
            tr="".join(f"<tr>{cells_html(r)}</tr>" for r in b[2])
            tbl=f'<div class="ref-tablewrap"><table class="ref-table"><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table></div>'
            # bump the summary with a count
            if open_det and out and out[-1].endswith('<div class="ref-detbody">'):
                out[-1]=out[-1].replace("</summary>", f' <span class="ref-count">{len(b[2])}</span></summary>')
            out.append(tbl)
        elif b[0]=="ul":
            out.append('<ul class="ref-ul">'+"".join(f"<li>{mdi(i)}</li>" for i in b[1])+"</ul>")
    close()
    return "".join(out)

# ---------------------------------------------------------------- visualizations
def svg_bars(data, bar_w=420, label_w=250, row_h=34, hue="var(--green)"):
    maxv=max(v for _,v in data) or 1
    pad=10; h=pad*2+row_h*len(data); w=label_w+bar_w+56
    rows=[f'<svg class="ref-svg" viewBox="0 0 {w} {h}" role="img" preserveAspectRatio="xMinYMin meet">']
    y=pad
    for name,v in data:
        bw=max(3, bar_w*v/maxv); cy=y+row_h/2
        rows.append(f'<text x="{label_w-10}" y="{cy}" text-anchor="end" dominant-baseline="central" class="bl">{html.escape(name)}</text>')
        rows.append(f'<rect x="{label_w}" y="{cy-9:.0f}" width="{bw:.1f}" height="18" rx="4" fill="{hue}"><title>{html.escape(name)}: {v}</title></rect>')
        rows.append(f'<text x="{label_w+bw+9:.1f}" y="{cy}" dominant-baseline="central" class="bv">{v}</text>')
        y+=row_h
    rows.append("</svg>")
    return "".join(rows)

# family counts
from collections import Counter
fam_c=Counter(reg[n]["family"] for n in reg)
FAM_ORDER=["load","assertion","fault","setup","probe","teardown"]
FAM_LABEL={"load":"load — strategies","assertion":"assertion — oracles","fault":"fault","setup":"setup","probe":"probe","teardown":"teardown"}
fam_data=[(FAM_LABEL[f], fam_c[f]) for f in FAM_ORDER if fam_c.get(f)]

# load subfamily counts (shorten the fully-spelled-out AFL++ label for the chart
# only — the full expansion still appears in the catalogue heading + text)
load_names=[n for n in reg if reg[n]["family"]=="load"]
sub_c=Counter(G.load_subfamily(n) for n in load_names)
def _short_theme(t): return t.replace(" (American Fuzzy Lop plus-plus, AFL++)", " (AFL++)")
sub_data=[(_short_theme(k), v) for k, v in sorted(sub_c.items(), key=lambda kv:-kv[1])]

LIFECYCLE = """
<div class="ref-flow" aria-label="scenario lifecycle">
  <div class="flow-serial">
    <div class="stage s-setup"><b>setup</b><span>prepare the world</span></div>
    <div class="flow-arrow">&rarr;</div>
    <div class="stage s-load">
      <b>load</b><span>the workload / strategy</span>
      <div class="concurrent">
        <span class="chip">faults <em>&#8635; concurrent</em></span>
        <span class="chip">probes <em>&#8635; concurrent</em></span>
      </div>
    </div>
    <div class="flow-arrow">&rarr;</div>
    <div class="stage s-assert"><b>assertions</b><span>the oracle &mdash; pass / fail</span></div>
    <div class="flow-arrow">&rarr;</div>
    <div class="stage s-teardown"><b>teardown</b><span>cleanup &mdash; always runs</span></div>
  </div>
  <p class="flow-caption">The serial spine runs left&#8594;right. <b>faults</b> and <b>probes</b> run <em>concurrently with load</em>, not as separate steps. A <code>seed</code> makes the whole run replay deterministically.</p>
</div>
"""

RUNTIME_CARDS = """
<div class="ref-cards">
  <div class="ref-card"><div class="rt-tag">runtime: library</div><h4>No node</h4><p>Drives a library / binary harness (shim) directly &mdash; fast, deterministic parser &amp; decoder fuzzing. No Docker, no network.</p></div>
  <div class="ref-card"><div class="rt-tag">runtime: single-node</div><h4>One process</h4><p>Spins up a single node process for behaviour that needs a live node but not a whole network.</p></div>
  <div class="ref-card"><div class="rt-tag">runtime: devnet</div><h4>Full topology</h4><p>Deploys a multi-node devnet via a <code>profile</code> (docker / host / multi-host) &mdash; consensus, mini-protocol, epoch and fault scenarios across a real network.</p></div>
</div>
"""

def _prof_runtime(d):
    nt=d.get("node_type","")
    if nt: return nt
    lbl=(d.get("label") or "").lower()
    if "amaru" in lbl and "haskell" in lbl: return "mixed"
    return "amaru" if "amaru" in lbl else "cardano-node"
def _prof_nodes(d):
    parts=[]
    hs=d.get("node_count"); am=d.get("amaru_node_count",0)
    if isinstance(hs,int) and hs: parts.append(f"{hs} haskell")
    if isinstance(am,int) and am: parts.append(f"{am} amaru")
    return " + ".join(parts) or "&mdash;"
def _profiles_catalog():
    pdir=os.path.join(DWARF,"profiles"); rows=[]
    if os.path.isdir(pdir):
        for name in sorted(os.listdir(pdir)):
            f=os.path.join(pdir,name,"profile.yaml")
            if not os.path.isfile(f): continue
            try: rows.append(json.load(open(f)))
            except Exception: pass
    return rows
_PROFILES=_profiles_catalog()
def _profile_usage():
    use=defaultdict(lambda:{"full":0,"smoke":0})
    for f in glob.glob(os.path.join(DWARF,"scenarios","*.yaml")):
        try: d=json.load(open(f))
        except Exception: continue
        pr=d.get("profile")
        if pr: use[pr]["smoke" if "smoke" in os.path.basename(f) else "full"]+=1
    return use
_PROFILE_USE=_profile_usage()
def _scn_totals():
    full=smoke=am=0
    for f in glob.glob(os.path.join(DWARF,"scenarios","*.yaml")):
        try: d=json.load(open(f))
        except Exception: continue
        if "smoke" in os.path.basename(f): smoke+=1
        else: full+=1
        if d.get("target",{}).get("implementation")=="amaru": am+=1
    return full,smoke,am
_SCN_FULL,_SCN_SMOKE,_SCN_AMARU=_scn_totals()
def _prof_verified(d):
    u=_PROFILE_USE.get(d.get("id"))
    if not u: return '<span class="sb local">defined · not yet exercised</span>'
    n=u["full"]+u["smoke"]; depth="full" if u["full"] else "smoke"
    return f'<span class="sb both">{depth} · {n} scenario{"s" if n!=1 else ""}</span>'
def profiles_table():
    if not _PROFILES: return ""
    tr="".join(f"<tr><td><code>{html.escape(d.get('id',''))}</code></td><td>{_prof_runtime(d)}</td><td>{_prof_nodes(d)}</td><td>{_prof_verified(d)}</td><td>{html.escape(d.get('label',''))}</td></tr>" for d in _PROFILES)
    return f'<div class="ref-tablewrap"><table class="ref-table"><thead><tr><th>profile</th><th>runtime</th><th>nodes</th><th>verified</th><th>purpose</th></tr></thead><tbody>{tr}</tbody></table></div>'

PROFILES = f"""
<div class="prof-triad">
  <span class="chip2"><b>profile</b> &mdash; where / how it runs</span>
  <span class="chip2"><b>scenario</b> &mdash; what to run</span>
  <span class="chip2"><b>substrate</b> &mdash; the running network</span>
</div>
<p class="ref-p">A <b>profile</b> is a named deployment configuration &mdash; the box, credentials, paths, and launch mode a devnet runs on. A devnet scenario names one through its <code>profile</code> field (required when <code>runtime: devnet</code>); <code>cardano-profile deploy &lt;profile-id&gt;</code> provisions the substrate, and profiles are edited with <code>cardano-profile config set</code> and listed at <code>/operate/profiles</code>. Put simply: the scenario is the <em>test plan</em>; the profile is <em>where it runs</em>.</p>
<h4 class="ref-h4">Compose modes &mdash; how the nodes launch</h4>
<div class="ref-cards">
  <div class="ref-card"><div class="rt-tag">compose_mode: host</div><h4>Native processes</h4><p>Each node runs as a native process via <code>tmux</code> on one machine. Fastest; least isolation.</p></div>
  <div class="ref-card"><div class="rt-tag">compose_mode: docker</div><h4>Containers</h4><p>Nodes run as <code>docker-compose</code> containers &mdash; the same isolation the Antithesis substrate uses.</p></div>
  <div class="ref-card"><div class="rt-tag">compose_mode: multi-host</div><h4>SSH fan-out</h4><p>Each node is provisioned on a different remote box over SSH; per-host telemetry folds into one bundle.</p></div>
</div>
<p class="ref-p">The substrate is <em>mode-agnostic</em> &mdash; the observation tiles look identical across modes; only performance, isolation, and reachability differ.</p>
<h4 class="ref-h4">What a profile holds</h4>
<div class="ref-tablewrap"><table class="ref-table"><thead><tr><th>field</th><th>meaning</th></tr></thead><tbody>
<tr><td><code>deployment_name</code></td><td>Human label for the deployment (e.g. <code>cardano-box</code>).</td></tr>
<tr><td><code>host</code> · <code>ssh_user</code> · <code>ssh_key_path</code></td><td>How DWARF reaches the deployment box over SSH.</td></tr>
<tr><td><code>remote_base_path</code></td><td>Remote directory for deployment artifacts.</td></tr>
<tr><td><code>docker_registry</code></td><td>Default registry prefix for framework images.</td></tr>
<tr><td><code>allow_prereq_install</code> · <code>allow_sudo</code></td><td>Guardrails for remote setup / privileged commands (off by default).</td></tr>
<tr><td><code>moog</code></td><td>Moog / GitHub / Antithesis requester settings (see the Antithesis section).</td></tr>
</tbody></table></div>
<h4 class="ref-h4">Included profiles</h4>
<p class="ref-p">DWARF ships {len(_PROFILES)} ready deployment profiles (the lettered <code>a</code>&ndash;<code>l</code> series) &mdash; Haskell, Amaru, and mixed topologies across a local devnet plus preview, preview2, and preprod. Deploy one with <code>cardano-profile deploy &lt;id&gt;</code>; <code>cardano-profile list-profiles</code> lists them all.</p>
{profiles_table()}
<p class="ref-p">The <b>verified</b> column shows how far each has been exercised by a checked-in scenario. Several &mdash; notably the Amaru and preprod profiles &mdash; are defined and deployable but not yet wired to a scenario. These are <em>local-devnet</em> deployment targets; the Antithesis run uses its own pinned compose bundle, not one of these profiles.</p>
"""

SHAPE_TREE = """
<div class="ref-shape">
  <div class="shape-tree">
    <div class="tn">array <span class="tc">// CBOR array of 2</span>
      <div class="tchild">
        <div class="tn leaf">uint <span class="tc">max 18 &mdash; certificate discriminator</span></div>
        <div class="tn">array
          <div class="tchild">
            <div class="tn leaf">uint <span class="tc">max 1 &mdash; credential kind</span></div>
            <div class="tn leaf">bytes <span class="tc">length 28 &mdash; blake2b-224 hash</span></div>
          </div>
        </div>
      </div>
    </div>
  </div>
  <div class="shape-out"><span>generates &rarr;</span><code>82 12 82 01 58 1c &lt;28 random bytes&gt;</code></div>
</div>
"""

ANTI_PIPELINE = """
<div class="pipe">
  <div class="pstep"><span class="pn">1</span><div><b>Author</b><p>A scenario with one <code>cbor_fuzz_*</code> load on a <code>cardano-node</code> target &mdash; one CBOR decode surface (tx-body, block, header, certificate, aux-data).</p></div></div>
  <div class="pdash"></div>
  <div class="pstep"><span class="pn">2</span><div><b>Generate</b><p><code>scenario run --backend antithesis</code> runs the generator: it checks the four rules (cardano-node · one cbor_fuzz/coverage load · surface&rarr;a <em>built</em> adversary mode · assertions&rarr;native SDK) and writes a self-contained bundle.</p></div></div>
  <div class="pdash"></div>
  <div class="pstep"><span class="pn">3</span><div><b>Verify (Stage-2)</b><p>A static gate rejects any &ldquo;looks-green-but-doesn't-fuzz&rdquo; bundle: no build contexts, adversary image + <code>exclude_from_faults</code> present, &ge;1 SDK assertion, drivers present.</p></div></div>
  <div class="pdash"></div>
  <div class="pstep"><span class="pn">4</span><div><b>Commit</b><p>The bundle is committed into the target GitHub repo at a directory. Moog references it by <em>repo + commit + directory</em> &mdash; nothing is uploaded to an API.</p></div></div>
  <div class="pdash"></div>
  <div class="pstep"><span class="pn">5</span><div><b>Launch via Moog</b><p><code>moog create-test --repo … --commit &lt;sha&gt; --approve</code>: the requester wallet signs an <b>on-chain</b> create-test (Moog MPFS / token on Cardano). Secrets are read from on-host files at runtime, never logged.</p></div></div>
  <div class="pdash"></div>
  <div class="pstep"><span class="pn">6</span><div><b>Run</b><p>Moog's agent launches it on Antithesis. The <code>dwarf-adversary</code> serves <em>Term-mutated</em> CBOR over node-to-node to the node under test; the SDK <em>reachable/sometimes</em> assertions record decoder-reached &amp; clean-rejection.</p></div></div>
  <div class="pdash"></div>
  <div class="pstep"><span class="pn">7</span><div><b>Results</b><p><code>moog test-status &lt;id&gt;</code> reads the run phase from <code>moog facts</code>; triage checks the node stayed error/critical-free with no rare events.</p></div></div>
</div>
"""

ANTI_ENGINES = """
<div class="engines">
  <div class="engine"><div class="etag">backend: local</div><h4>AFL++ on the instrumented binary</h4><p>Byte-level <code>mutate_cbor</code> steered by real edge coverage over the SanitizerCoverage-instrumented <code>cardano-node</code>.</p></div>
  <div class="ejoin"><span>same target decoder<br>same asserted property<br>same seed source</span></div>
  <div class="engine"><div class="etag">backend: antithesis</div><h4>dwarf-adversary over the wire</h4><p>Term-level structural <code>mutateTerm</code> applied to live node-to-node messages inside a self-contained Antithesis testnet.</p></div>
</div>
<p class="ref-p"><b>One definition, two engines.</b> A single scenario drives both backends via <code>scenario run --backend local-devnet | antithesis</code>. The mutation engines differ <em>by design</em>; the target, property, and seed are shared.</p>
"""

ANTI_MAP_ROWS = [
    ("header", "chainsync", "block-header"),
    ("block", "blockfetch", "block"),
    ("tx / applytx / applyblock / ledger", "txsubmission", "tx-body"),
    ("certificate", "txsubmission", "certificate"),
    ("auxiliary-data", "txsubmission", "auxiliary-data"),
]
def anti_map_table():
    tr="".join(f"<tr><td><code>{html.escape(s)}</code></td><td>{p}</td><td><code>{sh}</code></td></tr>" for s,p,sh in ANTI_MAP_ROWS)
    return f'<div class="ref-tablewrap"><table class="ref-table"><thead><tr><th>Decode surface</th><th>N2N adversary protocol</th><th>CBOR shape served</th></tr></thead><tbody>{tr}</tbody></table></div>'

ANTI_IMAGES = """
<div class="ref-cards">
  <div class="ref-card imgcard">
    <div class="rt-tag">backends: antithesis + on-wire</div>
    <code class="imgname">ghcr.io/j-gainsec/dwarf-adversary</code>
    <h4>The adversary</h4>
    <p>The Haskell <code>dwarf-adversary</code> &mdash; joins the testnet as a node-to-node peer and serves <b>Term-level structurally-mutated CBOR</b> to the node under test. Also ships the in-process <code>dwarf-decoder-fuzz</code>. The generated Antithesis bundle pins <code>:0.9.0</code> (current release line <code>:0.19.0</code>).</p>
  </div>
  <div class="ref-card imgcard">
    <div class="rt-tag">backend: local coverage</div>
    <code class="imgname">ghcr.io/j-gainsec/dwarf-haskell-cov</code>
    <h4>The coverage harness</h4>
    <p>A <b>SanitizerCoverage-instrumented</b> <code>cardano-node</code> (GHC <code>-fllvm</code> + an LLVM SanCov pass). AFL++ drives it with real native edge coverage across the decode and Conway ledger surfaces. Cross-platform image.</p>
  </div>
</div>
<div class="ref-note">The two <code>ghcr.io/j-gainsec/*</code> images above are DWARF's own. The Antithesis <em>testnet substrate</em> itself uses pinned upstream images (<code>ghcr.io/cardano-foundation/*</code>, <code>ghcr.io/pragma-org/amaru</code>) &mdash; registry-hermetic, no build contexts (the Stage-2 gate rejects any <code>build:</code>).</div>
"""

def coverage_stat(total, covered, load_n, assn_n, fam_n):
    return f"""
<div class="ref-stats">
  <div class="ref-stat big"><b>{covered}<span class="slash">/</span>{total}</b><span>primitives documented (100%)</span></div>
  <div class="ref-stat"><b>{load_n}</b><span>load strategies</span></div>
  <div class="ref-stat"><b>{assn_n}</b><span>assertion oracles</span></div>
  <div class="ref-stat"><b>{fam_n}</b><span>primitive families</span></div>
</div>"""

# ---------------------------------------------------------------- assemble sections
spec_blocks = drop_notes(drop_section(drop_h1(G.build_spec()), "Runtime tiers"))
prim_blocks = drop_notes(drop_h1(G.build_primitives()))

total=len(reg); covered=sum(1 for n in reg if n in G.DESC)

SEC_TICKER = """
<section id="attackcost" class="shell ref-section">
  <div class="section-head"><div>
    <p class="eyebrow">Live &middot; Cardano</p>
    <h2>Cost to attack Cardano, right now.</h2>
  </div></div>
  <p class="ref-lede">The textbook figure is "51% of the stake." The <em>effective</em> figure is lower &mdash; because only part of all ADA is actively staked, an attacker needs a fraction of <b>active</b> stake, not of total supply. This is computed live from on-chain data + the ADA price.</p>
  <div class="tick-grid">
    <div class="tick-hero">
      <div class="tick-label">Est. cost to reach the ~40%-of-active-stake band</div>
      <div class="tick-big" id="tk-cost">$&mdash;</div>
      <div class="tick-sub"><span id="tk-ada">&mdash;</span> ADA &middot; <span id="tk-pct">&mdash;</span> of circulating supply</div>
    </div>
    <div class="tick-cards">
      <div class="tick-card"><b id="tk-active">&mdash;</b><span>active stake (ADA)</span></div>
      <div class="tick-card"><b id="tk-ratio">&mdash;</b><span>staking ratio (of circulating)</span></div>
      <div class="tick-card"><b id="tk-price">$&mdash;</b><span>ADA price</span></div>
      <div class="tick-card"><b id="tk-epoch">&mdash;</b><span>epoch</span></div>
    </div>
  </div>
  <div class="tick-rows">
    <div class="tick-row"><span>~40% of active stake (effective / harm-onset band)</span><b id="tk-r40">&mdash;</b></div>
    <div class="tick-row"><span>50% of active stake (majority)</span><b id="tk-r50">&mdash;</b></div>
  </div>
  <p class="ref-p" style="font-size:.85rem"><span id="tk-status">snapshot</span> &middot; source: Koios (on-chain) + CoinGecko (price). The theoretical Praos threshold (~48% of active stake at mainnet's block rate) barely moves; the <em>economic</em> figures above tick with staking ratio and price. Note: acquiring this much ADA would itself crash the price &mdash; the cost is a deterrent, not just a number.</p>
</section>
<script>
(function(){
  // baked snapshot (fallback if live fetch is blocked by CSP/CORS) — epoch 642
  var SNAP={epoch:642,circ:36474473805.5,total:38743637897.0,active:21392411062.6,price:0.157322};
  function fmtUSD(n){ if(n>=1e9)return '$'+(n/1e9).toFixed(2)+'B'; if(n>=1e6)return '$'+(n/1e6).toFixed(1)+'M'; return '$'+Math.round(n).toLocaleString(); }
  function fmtADA(n){ if(n>=1e9)return (n/1e9).toFixed(2)+'B'; if(n>=1e6)return (n/1e6).toFixed(1)+'M'; return Math.round(n).toLocaleString(); }
  function set(id,v){ var e=document.getElementById(id); if(e)e.textContent=v; }
  function render(d,live){
    var ada40=d.active*0.40, ada50=d.active*0.50, cost=ada40*d.price;
    set('tk-cost',fmtUSD(cost));
    set('tk-ada',fmtADA(ada40));
    set('tk-pct',(100*ada40/d.circ).toFixed(1)+'%');
    set('tk-active',fmtADA(d.active));
    set('tk-ratio',(100*d.active/d.circ).toFixed(1)+'%');
    set('tk-price','$'+d.price.toFixed(4));
    set('tk-epoch',d.epoch);
    set('tk-r40',fmtADA(ada40)+' ADA  ·  '+fmtUSD(ada40*d.price)+'  ·  '+(100*ada40/d.circ).toFixed(1)+'% supply');
    set('tk-r50',fmtADA(ada50)+' ADA  ·  '+fmtUSD(ada50*d.price)+'  ·  '+(100*ada50/d.circ).toFixed(1)+'% supply');
    set('tk-status', live ? 'live · updates every 60s' : 'snapshot (live feed blocked here — live on the hosted site)');
  }
  async function live(){
    try{
      var totals=await fetch('https://api.koios.rest/api/v1/totals').then(function(r){return r.json();});
      var t=totals[0], epoch=+t.epoch;
      var ei=await fetch('https://api.koios.rest/api/v1/epoch_info?_epoch_no='+epoch).then(function(r){return r.json();});
      var pr=await fetch('https://api.coingecko.com/api/v3/simple/price?ids=cardano&vs_currencies=usd').then(function(r){return r.json();});
      render({epoch:epoch,circ:+t.circulation_lovelace/1e6,total:+t.total_supply_lovelace/1e6,active:+ei[0].active_stake/1e6,price:+pr.cardano.usd}, true);
    }catch(e){ render(SNAP,false); }
  }
  render(SNAP,false);   // show snapshot immediately
  live();               // then try to go live
  setInterval(live, 60000);
})();
</script>
"""

SEC_DSL = f"""
<section id="dsl" class="shell ref-section">
  <div class="section-head"><div>
    <p class="eyebrow">Reference &middot; how it works</p>
    <h2>The scenario DSL, end to end.</h2>
  </div></div>
  <p class="ref-lede">A <b>scenario</b> is a single YAML file &mdash; DWARF's domain-specific language (DSL) for tests. It says <em>what to run against</em>, <em>what to do</em>, and <em>what must be true afterwards</em>. Everything below is generated from the framework's own schema, so it always matches the code.</p>
  <h3 class="ref-h2">At a glance &mdash; the lifecycle</h3>
  {LIFECYCLE}
  <h3 class="ref-h2">The three runtime tiers</h3>
  {RUNTIME_CARDS}
  <h3 class="ref-h2">Profiles &mdash; where a devnet runs</h3>
  {PROFILES}
  <h3 class="ref-h2">The CBOR shape grammar, by example</h3>
  <p class="ref-p">Structured fuzzing describes a well-formed value by its <em>shape</em>, leaving leaves random; the mutation pass then corrupts inner fields to reach deep decoder paths.</p>
  {SHAPE_TREE}
  <h3 class="ref-h2">Full field &amp; grammar reference</h3>
  <div class="ref-body">{render_dwarf(spec_blocks)}</div>
</section>
"""

SEC_ANTI = f"""
<section id="antithesis" class="shell ref-section">
  <div class="section-head"><div>
    <p class="eyebrow">Reference &middot; two backends</p>
    <h2>How DWARF generates Antithesis tests.</h2>
  </div></div>
  <p class="ref-lede">The same scenario that fuzzes a decoder locally can be turned into a self-contained <b>Antithesis</b> test and launched through <b>Moog</b>. The generator targets CBOR decode fuzzing: it takes a single <code>cbor_fuzz_*</code> (or coverage) load on a <code>cardano-node</code> target and emits a verified, self-contained test bundle.</p>
  <h3 class="ref-h2">The pipeline &mdash; scenario &rarr; generator &rarr; bundle &rarr; Moog &rarr; Antithesis</h3>
  {ANTI_PIPELINE}
  <h3 class="ref-h2">One definition, two engines</h3>
  {ANTI_ENGINES}
  <h3 class="ref-h2">Which decode surface maps to which on-wire adversary</h3>
  <p class="ref-p">On the Antithesis backend the <code>dwarf-adversary</code> serves structurally-mutated CBOR over a node-to-node mini-protocol. Each supported decode surface maps to a <em>built</em> adversary mode:</p>
  {anti_map_table()}
  <h3 class="ref-h2">The container images (<code>ghcr.io/j-gainsec</code>)</h3>
  <p class="ref-p">DWARF's fuzzing ships as two public images &mdash; one per backend &mdash; while the testnet substrate uses pinned upstream images.</p>
  {ANTI_IMAGES}
  <div class="ref-note"><b>Honest scope.</b> Generatable today = <code>cardano-node</code> only (Amaru / differential generation is follow-on work); decode surfaces map to the five built adversary modes above (on-wire modes for handshake / keepalive / txsub framing are still to come). Moog does <b>not</b> call an Antithesis API &mdash; it signs an on-chain create-test and CF's Moog agent launches the run.</div>
</section>
"""

SEC_PRIM = f"""
<section id="primitives" class="shell ref-section">
  <div class="section-head"><div>
    <p class="eyebrow">Reference &middot; the catalogue</p>
    <h2>Every strategy, every oracle.</h2>
  </div></div>
  <p class="ref-lede">The complete set of primitives a scenario can reference &mdash; each <em>strategy</em> (what a scenario does) and each <em>oracle</em> (the pass/fail condition). A scenario can only name primitives that exist here; pasted YAML can never add behaviour. All {total} are fully implemented; the <b>verified</b> column shows how far each has actually been exercised. Across the {_SCN_FULL + _SCN_SMOKE} shipped scenarios &mdash; {_SCN_FULL} full + {_SCN_SMOKE} smoke, {_SCN_AMARU} targeting Amaru &mdash; the deep, evidenced surfaces are the CBOR-decode / coverage / on-wire ones.</p>
  {coverage_stat(total, covered, fam_c['load'], fam_c['assertion'], len(FAM_ORDER))}
  <div class="ref-charts">
    <figure class="ref-fig"><figcaption>Primitives by family</figcaption>{svg_bars(fam_data)}</figure>
    <figure class="ref-fig"><figcaption>Load strategies by theme</figcaption>{svg_bars(sub_data, bar_w=250, label_w=320)}</figure>
  </div>
  <p class="ref-hint">Every group below is complete and collapsible &mdash; open one to see its primitives, runtimes, and pass-conditions.</p>
  <div class="ref-body">{render_catalog(prim_blocks)}</div>
</section>
"""

EVID_BUNDLE_FILES = """
<div class="ref-card filetree">
  <div class="ft-head"><code>dwarf/runs/&lt;run-id&gt;/</code> <span>&mdash; one directory per run, exportable as <code>dwarf/bundles/&lt;run-id&gt;.tar.gz</code></span></div>
  <ul>
    <li><code>manifest.json</code> <em>&mdash; run identity, target, seed, exit-status, assertion summary (the hashed root of trust)</em></li>
    <li><code>scenario.yaml</code> · <code>resolved-profile.json</code> · <code>env.json</code> <em>&mdash; exactly what ran, where, and in what environment</em></li>
    <li><code>assertions.json</code> <em>&mdash; every assertion's evaluated value + pass/fail</em></li>
    <li><code>inputs/</code> · <code>outputs/</code> <em>&mdash; what went into and out of the system under test</em></li>
    <li><code>log.ndjson</code> · <code>events/</code> · <code>metrics/</code> · <code>probes/</code> <em>&mdash; the append-only event log and normalized telemetry</em></li>
    <li><code>chain.json</code> <em>&mdash; this run's hash-chain link (tamper-evidence)</em></li>
  </ul>
</div>
"""

EVID_PIPELINE = """
<div class="pipe">
  <div class="pstep"><span class="pn">1</span><div><b>Run</b><p>A deterministic run id <code>YYYYMMDDTHHMMSSZ-&lt;hash&gt;</code> is derived from <code>sha256(scenario · profile · env · seed)</code> &mdash; identical inputs reproduce it.</p></div></div>
  <div class="pdash"></div>
  <div class="pstep"><span class="pn">2</span><div><b>Bundle</b><p>The run writes its self-contained directory &mdash; the <b>unit of evidence</b> everything downstream operates on.</p></div></div>
  <div class="pdash"></div>
  <div class="pstep"><span class="pn">3</span><div><b>Chain</b><p><code>chain.json</code> records <code>manifest_hash</code> + <code>prev_hash</code> into an append-only chain (first link is <code>genesis</code>). Verification walks back to genesis; any edit breaks the recomputed hash.</p></div></div>
  <div class="pdash"></div>
  <div class="pstep"><span class="pn">4</span><div><b>Sign &amp; attest</b><p>Ed25519: <b>sign</b> a per-file hash manifest (verdict verified / tampered / unsigned), and <b>attest</b> the run's provenance (scenario, profile, and tooling versions).</p></div></div>
  <div class="pdash"></div>
  <div class="pstep"><span class="pn">5</span><div><b>Export SARIF</b><p><code>runtime_bundle_export_sarif</code> emits standards-compliant <b>SARIF v2.1.0</b> (validated against the bundled schema) so any SARIF-consuming audit tool can read the findings.</p></div></div>
  <div class="pdash"></div>
  <div class="pstep"><span class="pn">6</span><div><b>Promote</b><p>An explicit, recorded operator action writes a <code>promotion.json</code> (reason, actor) with a novelty check against prior bundles. Gated by the scenario's <code>evidence_intent</code> + <code>promotion_blockers</code> &mdash; metadata never auto-promotes.</p></div></div>
</div>
"""

EVID_OPS = [
  ("sign","Ed25519-sign a per-file hash manifest of the bundle."),
  ("attestation","Signed provenance statement (scenario / profile / tooling versions)."),
  ("chain","Composite: sign + promote + dedupe in one step."),
  ("chain_verify","Walk the hash chain back to genesis; report a chain verdict."),
  ("export_sarif","Emit SARIF v2.1.0 from bundle-diff / replay divergences."),
  ("export","Package / export a captured bundle (optionally signed)."),
  ("promote","Write a structured promotion record (reason, actor)."),
  ("triage","Composite: promote + dedupe (no signature)."),
  ("dedupe","Novelty check vs previously-promoted bundles (match / novel)."),
  ("diff","Per-file sha256 comparison of any two bundles."),
  ("timeline","Chronological evidence timeline across many bundles."),
  ("summary_compose","Executive roll-up (coverage, chain, attestation) across bundles."),
]
def evid_ops_table():
    tr="".join(f"<tr><td><code>runtime_bundle_{n}</code></td><td>{d}</td></tr>" for n,d in EVID_OPS)
    return f'<div class="ref-tablewrap"><table class="ref-table"><thead><tr><th>operation</th><th>what it does</th></tr></thead><tbody>{tr}</tbody></table></div>'

SEC_EVIDENCE = f"""
<section id="evidence" class="shell ref-section">
  <div class="section-head"><div>
    <p class="eyebrow">Reference &middot; the output</p>
    <h2>Evidence &mdash; every run is an auditable bundle.</h2>
  </div></div>
  <p class="ref-lede">DWARF's product is <b>evidence</b>. Every run &mdash; local or Antithesis, any runtime &mdash; produces one self-contained, tamper-evident <b>forensic bundle</b>: the same format everywhere, signed, hash-chained, and exportable for audit.</p>
  <h3 class="ref-h2">What's in a bundle</h3>
  {EVID_BUNDLE_FILES}
  <h3 class="ref-h2">From run to promoted finding</h3>
  {EVID_PIPELINE}
  <h3 class="ref-h2">Integrity &mdash; three independent guarantees</h3>
  <div class="ref-cards">
    <div class="ref-card"><div class="rt-tag">append-only</div><h4>Manifest hash chain</h4><p>Each bundle links to the previous by hash. Walking back to <code>genesis</code> reconstructs every run; any tampered manifest breaks the chain. (A manifest chain, not a Merkle tree over files.)</p></div>
    <div class="ref-card"><div class="rt-tag">Ed25519</div><h4>Per-file signature</h4><p><code>runtime_bundle_sign</code> signs a hash manifest of every file &mdash; the real per-file tamper check, verdict verified / tampered / unsigned.</p></div>
    <div class="ref-card"><div class="rt-tag">Ed25519</div><h4>Provenance attestation</h4><p><code>runtime_bundle_attestation</code> signs <em>what produced</em> the run &mdash; scenario, profile, and cardano-node / amaru / DWARF tooling versions.</p></div>
  </div>
  <h3 class="ref-h2">Bundle operations</h3>
  {evid_ops_table()}
  <div class="ref-note"><b>Evidence lifecycle.</b> The scenario fields <code>evidence_intent</code> (<code>candidate</code> / <code>regression</code> / <code>finding-validation</code> / <code>risk-support</code>), <code>promotion_blockers</code>, and <code>testcase_candidate</code> label a run's evidence &mdash; but they are <b>metadata only</b>. Promotion to a finding is an explicit, recorded operator action, not an automatic effect.</div>
</section>
"""

COV_GROUPS = [
  ("Decode", [("header","Praos / VRF / KES header"),("tx","Conway tx body + witnesses"),("block","full block &mdash; widest decode")]),
  ("Ledger rules (state-transition system)", [("ledger","TxBody + min-fee"),("applytx","UTXOW &rarr; UTXO"),("applyblock","BBODY &rarr; LEDGERS &rarr; LEDGER &middot; deepest")]),
  ("Mini-protocol (on-wire framing)", [("handshake","version negotiation"),("txsub","TxSubmission2"),("keepalive","liveness")]),
]
BOTH_BACKENDS={"header","tx","block","ledger","applytx","applyblock"}
def cov_surfaces():
    cols=""
    for title,items in COV_GROUPS:
        chips=""
        for dec,desc in items:
            badge = '<span class="sb both">both backends</span>' if dec in BOTH_BACKENDS else '<span class="sb local">local only</span>'
            deep = ' surf-deep' if dec=="applyblock" else ''
            chips+=f'<div class="surf{deep}"><code>DWARF_DECODER={dec}</code><span class="sd">{desc}</span>{badge}</div>'
        cols+=f'<div class="surf-col"><h4 class="ref-h4">{title}</h4>{chips}</div>'
    return f'<div class="surf-grid">{cols}</div>'

SEC_COVERAGE = f"""
<section id="coverage" class="shell ref-section">
  <div class="section-head"><div>
    <p class="eyebrow">Reference &middot; the local engine</p>
    <h2>Native coverage-guided fuzzing.</h2>
  </div></div>
  <p class="ref-lede">The local backend drives a <b>SanitizerCoverage-instrumented</b> <code>cardano-node</code> with <b>AFL++</b>, steering mutation on <b>real native edge coverage</b> &mdash; not black-box. One multi-surface harness (<code>dwarf-haskell-cov</code>) picks its target from <code>DWARF_DECODER</code>.</p>
  <div class="prof-triad">
    <span class="chip2">image: <b>dwarf-haskell-cov</b></span>
    <span class="chip2">engine: <b>AFL++</b> (afl.rs 4.40c)</span>
    <span class="chip2">GHC <b>-fllvm</b> + LLVM <b>SanCov</b> edges</span>
  </div>
  <h3 class="ref-h2">The nine surfaces</h3>
  {cov_surfaces()}
  <p class="ref-p">The six decode / ledger surfaces run on <b>both backends</b> from one definition (local AFL++ and the Antithesis adversary, same target &amp; seed); the three mini-protocol framing surfaces are local-AFL only today (the on-wire adversary mode is follow-on work).</p>
  <div class="ref-card"><span class="tag-deep">deepest surface</span>
    <p class="ref-p" style="margin-top:6px"><code>applyblock</code> decodes a Conway tx, wraps it as a single-tx block body, and runs <code>applyBlockEither ValidateAll</code> &mdash; the full <b>BBODY &rarr; LEDGERS &rarr; per-tx LEDGER</b> pipeline (ConwayUtxow / Utxo / Certs) over a genesis-initialised Conway <code>NewEpochState</code>. It reaches the <em>real ledger rules</em>, not just the decoder &mdash; and runs both under AFL++ locally and in-process under Antithesis.</p>
  </div>
  <div class="ref-note"><b>The gate.</b> A coverage run passes <code>aflpp_smoke_exit_clean</code> only on a clean exit that also clears its floors: executions, corpus (queue) growth, AFL cycles, and edge-bitmap coverage %.</div>
</section>
"""

PLUGIN_BASES = [
  ("LoadPrimitive","run(handle, rng)","the workload / strategy"),
  ("ProbePrimitive","sample(...) / sample_for_input(...)","time-series or per-input sampling"),
  ("AssertionPrimitive","evaluate(...) / evaluate_outcomes(...)","the pass/fail oracle"),
  ("FaultPrimitive","apply(handle) / remove(handle)","degrade then restore (nested)"),
]
def plugin_bases_table():
    tr="".join(f"<tr><td><code>{b}</code></td><td><code>{m}</code></td><td>{d}</td></tr>" for b,m,d in PLUGIN_BASES)
    return f'<div class="ref-tablewrap"><table class="ref-table"><thead><tr><th>base class</th><th>hook</th><th>role</th></tr></thead><tbody>{tr}</tbody></table></div>'

CLI_GROUPS = [
  ("Deploy / lifecycle","deploy &lt;profile&gt;","remove · status · inspect · doctor · logs · snapshot · prereq-check"),
  ("Run","scenario run &lt;path&gt;","scenario validate/new/verify · compare · fuzz run/campaign · smoke"),
  ("Evidence","bundle list/inspect/promote","bundle sign/verify/audit-trail/export/replay-and-diff · coverage aggregate · testcase …"),
  ("Moog / Antithesis","moog create-test","moog healthcheck/readiness/bootstrap/registration · moog test-status · antithesis build"),
  ("Config / wallet","config get/set","wallet add/list/healthcheck · intake · list-profiles"),
  ("Dashboard","dashboard serve","dashboard generate · dashboard status"),
]
def cli_table():
    tr="".join(f"<tr><td>{g}</td><td><code>cardano-profile {c}</code></td><td>{more}</td></tr>" for g,c,more in CLI_GROUPS)
    return f'<div class="ref-tablewrap"><table class="ref-table"><thead><tr><th>group</th><th>key command</th><th>also</th></tr></thead><tbody>{tr}</tbody></table></div>'

SEC_EXTEND = f"""
<section id="extend" class="shell ref-section">
  <div class="section-head"><div>
    <p class="eyebrow">Reference &middot; operate &amp; extend</p>
    <h2>Extending &amp; driving DWARF.</h2>
  </div></div>

  <div class="ref-note"><b>Install &amp; operate.</b> Deploy the framework + dashboard on any Docker host with Compose v2: <code>delivery/scripts/install.sh</code> &rarr; <code>build-image.sh</code> &rarr; <code>deploy.sh</code> &rarr; <code>status.sh</code>, then open <code>/operate</code> and <code>/learn</code>. Full procedures &mdash; requirements, runtime layout, config, Moog / GitHub / Antithesis setup, and troubleshooting &mdash; live in <code>INSTALL.md</code> and <code>OPERATIONS.md</code>.</div>

  <h3 class="ref-h2">The registry boundary</h3>
  <div class="ref-note"><b>A scenario is data; primitives are code.</b> A scenario can only <em>reference</em> primitives already registered in <code>primitives/registry.json</code> &mdash; pasted YAML can never introduce new behaviour. Adding a capability means adding code + a registry entry (or an installed, version-gated plugin), never scenario data. That boundary is the framework's safety guarantee.</div>
  <p class="ref-p">Two ways to add behaviour: an <b>in-tree primitive</b> (<code>cardano-profile primitive new --family … --name …</code> scaffolds the stub + registry entry), or an <b>out-of-tree plugin</b> &mdash; a directory on <code>DWARF_PLUGINS_DIR</code> (or <code>~/.dwarf/plugins</code>) with a <code>plugin.json</code> (pinned to <code>dwarf_api_version: v1</code>, a <code>register(registry)</code> entrypoint, and its own schemas). Plugins can't shadow built-in names.</p>
  <h4 class="ref-h4">Primitive base classes</h4>
  {plugin_bases_table()}

  <h3 class="ref-h2">The <code>cardano-profile</code> CLI</h3>
  <p class="ref-p">One operator entrypoint spans deploy, run, evidence, Moog, config, and the dashboard.</p>
  {cli_table()}

  <h3 class="ref-h2">Notifications</h3>
  <p class="ref-p">A <code>notifications:</code> block in <code>dwarf/state/config.yaml</code> maps events to handlers. Dispatch is best-effort (5s timeout, logged to <code>state/notifications.log</code>) &mdash; a downstream outage never breaks a run.</p>
  <div class="ref-cards">
    <div class="ref-card"><div class="rt-tag">events</div><p><code>on_scenario_fail</code> · <code>on_coverage_regression</code> · <code>on_assertion_population_shift</code></p></div>
    <div class="ref-card"><div class="rt-tag">handlers</div><p><code>webhook</code> (POST JSON) · <code>slack</code> (incoming webhook) · <code>email</code> (SMTP)</p></div>
  </div>
</section>
"""

SEC_CONSENSUS = """
<section id="consensus" class="shell ref-section">
  <div class="section-head"><div>
    <p class="eyebrow">Reference &middot; consensus</p>
    <h2>Cross-implementation chain-selection differential.</h2>
  </div></div>
  <div class="ref-note"><b>Does the Haskell cardano-node and Amaru ever disagree on which chain is real?</b> DWARF runs a chain-selection differential between the two node implementations on the real mixed <code>cardano_amaru</code> topology (Haskell producers &amp; relays alongside Amaru relays and an Amaru-fed consumer), induces forks, and compares &mdash; block-for-block, time-aligned &mdash; the chain each selects. Across every regime exercised (honest, a &lt;k fork that heals, a &gt;k fork that must not, an epoch boundary), cardano-node and Amaru selected the identical chain.</div>

  <h3 class="ref-h2">How a run works</h3>
  <div class="ref-cards">
    <div class="ref-card"><div class="rt-tag">setup</div><p><code>runtime_attach_topology</code> binds to the running cardano_amaru containers. The <code>ensure_cardano_amaru_converged.py</code> gate first retries bring-up until the producers agree (fixing a startup split-brain).</p></div>
    <div class="ref-card"><div class="rt-tag">fault</div><p><code>runtime_network_partition</code> isolates a producer to build a competing chain, then heals. Fork depth scales with partition time &mdash; crossing the security parameter <code>k</code>.</p></div>
    <div class="ref-card"><div class="rt-tag">observe</div><p><code>runtime_multi_node_observation</code> reads every tip concurrently (time-aligned); <code>runtime_tracer_capture</code> pulls per-node forge / ChainDB forensics into the bundle.</p></div>
    <div class="ref-card"><div class="rt-tag">assert</div><p><code>chain_select_differential</code>: the cardano-node relays and the Amaru path (<code>amaru-consumer</code>) must select the same block and hash.</p></div>
  </div>

  <h3 class="ref-h2">Results by regime</h3>
  <p class="ref-p"><b>chainhold (&gt;k fork):</b> partitioned producer stranded &mdash; deep rollback refused, canonical held identically. <b>&lt;k recovery:</b> partitioned producer recovers, all reconverge. <b>Epoch boundary:</b> hash-identical across the transition. In every case cardano-node &equiv; Amaru.</p>

  <h3 class="ref-h2">Exhaustive sweep</h3>
  <p class="ref-p"><code>consensus_differential_sweep.py</code> walks a grid of fork depths &times; trials into a coverage matrix with a single verdict &mdash; the k-boundary crossover (shallow forks heal, deep forks strand) captured automatically, cardano-node &equiv; Amaru at every depth. A differential (agreement), not a threshold measurement; smoke-level today, with the same harness scaling to long soaks and the Antithesis deterministic scheduler.</p>
</section>
"""

REF_CSS = """
<style id="dwarf-reference-styles">
.ref-section{padding-top:26px}
.ref-lede{color:var(--muted);font-size:1.05rem;max-width:60ch;margin:.2rem 0 1.4rem}
.ref-body{margin-top:8px}
.ref-h2{font-size:1.15rem;margin:30px 0 10px;color:var(--ink);letter-spacing:-.01em}
.ref-h3{font-size:.95rem;margin:18px 0 6px;color:var(--green)}
.ref-h4{font-size:.8rem;margin:14px 0 4px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
.ref-p{color:var(--muted);margin:8px 0;max-width:72ch}
.ref-p code,.ref-note code,.ref-table code,.ref-ul code{background:var(--panel-2);color:var(--green);padding:1px 5px;border-radius:5px;font-size:.85em}
.ref-note{border-left:2px solid var(--green);background:var(--panel);color:var(--muted);padding:10px 14px;border-radius:8px;margin:12px 0;font-size:.92rem}
.ref-ul{color:var(--muted);padding-left:20px;max-width:72ch}.ref-ul li{margin:4px 0}
.ref-code{background:#050b09;border:1px solid var(--line);border-radius:12px;padding:14px 16px;overflow-x:auto;color:var(--ink);font-size:.82rem;line-height:1.5}
.ref-tablewrap{overflow-x:auto;border:1px solid var(--faint);border-radius:12px;margin:12px 0}
.ref-table{border-collapse:collapse;width:100%;font-size:.85rem;min-width:520px}
.ref-table th,.ref-table td{text-align:left;padding:8px 12px;border-bottom:1px solid var(--faint);vertical-align:top}
.ref-table th{color:var(--muted);font-weight:600;background:var(--panel);position:sticky;top:0}
.ref-table tbody tr:hover,.ref-table tr:hover{background:rgba(140,255,203,.04)}
.ref-details{border:1px solid var(--faint);border-radius:12px;margin:10px 0;overflow:hidden;background:var(--panel)}
.ref-details>summary{cursor:pointer;padding:12px 16px;font-weight:600;color:var(--ink);list-style:none;display:flex;align-items:center;gap:10px}
.ref-details>summary::-webkit-details-marker{display:none}
.ref-details>summary::before{content:"▸";color:var(--green);transition:transform .15s}
.ref-details[open]>summary::before{transform:rotate(90deg)}
.ref-count{margin-left:auto;background:var(--panel-2);color:var(--green);border-radius:999px;padding:1px 10px;font-size:.78rem;font-weight:700}
.ref-detbody{padding:0 12px 12px}
.ref-hint{color:var(--muted);font-size:.9rem;margin:14px 0 6px}
/* flow diagram */
.ref-flow{margin:14px 0 6px}
.flow-serial{display:flex;align-items:stretch;gap:10px;flex-wrap:wrap}
.stage{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:12px 16px;min-width:150px;flex:1 1 150px;position:relative}
.stage b{display:block;color:var(--ink);font-size:1rem}
.stage span{color:var(--muted);font-size:.8rem}
.s-load{border-color:var(--green);box-shadow:0 0 0 1px var(--green) inset}
.flow-arrow{align-self:center;color:var(--green);font-size:1.3rem;font-weight:700}
.concurrent{display:flex;gap:6px;margin-top:8px;flex-wrap:wrap}
.chip{background:var(--panel-2);border:1px dashed var(--line);border-radius:999px;padding:2px 10px;font-size:.72rem;color:var(--muted)}
.chip em{color:var(--cyan);font-style:normal}
.flow-caption{color:var(--muted);font-size:.85rem;margin-top:12px}
.prof-triad{display:flex;gap:8px;flex-wrap:wrap;margin:6px 0 12px}
.chip2{background:var(--panel-2);border:1px solid var(--line);border-radius:999px;padding:4px 13px;font-size:.82rem;color:var(--muted)}
.chip2 b{color:var(--green)}
/* cards */
.ref-cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;margin:12px 0}
.ref-card{background:var(--panel);border:1px solid var(--faint);border-radius:14px;padding:16px}
.ref-card h4{color:var(--ink);margin:6px 0 6px;font-size:1rem}
.ref-card p{color:var(--muted);font-size:.86rem;margin:0}
.rt-tag{display:inline-block;font-family:ui-monospace,Menlo,monospace;font-size:.72rem;color:var(--green);background:var(--panel-2);border-radius:6px;padding:2px 8px}
.imgcard .imgname{display:block;font-family:ui-monospace,Menlo,monospace;font-size:.82rem;color:var(--cyan);margin:9px 0 2px;word-break:break-all}
/* shape tree */
.ref-shape{display:flex;flex-wrap:wrap;gap:18px;align-items:center;margin:12px 0}
.shape-tree{font-family:ui-monospace,Menlo,monospace;font-size:.84rem;background:var(--panel);border:1px solid var(--faint);border-radius:12px;padding:14px 16px}
.tn{color:var(--green);padding:2px 0}
.tn.leaf{color:var(--cyan)}
.tc{color:var(--muted)}
.tchild{border-left:1px solid var(--line);margin-left:8px;padding-left:14px}
.shape-out{display:flex;flex-direction:column;gap:6px}
.shape-out span{color:var(--muted);font-size:.8rem}
.shape-out code{background:#050b09;border:1px solid var(--line);border-radius:8px;padding:8px 12px;color:var(--ink);font-size:.82rem}
/* stats */
.ref-stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:8px 0 18px}
.ref-stat{background:var(--panel);border:1px solid var(--faint);border-radius:14px;padding:16px}
.ref-stat b{display:block;font-size:1.6rem;color:var(--ink);letter-spacing:-.02em}
.ref-stat.big b{font-size:2.2rem;color:var(--green)}
.ref-stat .slash{color:var(--muted);font-weight:400;margin:0 2px}
.ref-stat span{color:var(--muted);font-size:.82rem}
/* charts */
.ref-charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px;margin:8px 0 8px}
.ref-fig{margin:0;background:var(--panel);border:1px solid var(--faint);border-radius:14px;padding:14px 16px}
.ref-fig figcaption{color:var(--ink);font-weight:600;font-size:.9rem;margin-bottom:8px}
.ref-svg{width:100%;height:auto;overflow:hidden}
.ref-svg .bl{fill:var(--muted);font-size:11px}
.ref-svg .bv{fill:var(--ink);font-size:12px;font-weight:700}
/* antithesis pipeline */
.pipe{display:flex;flex-direction:column;gap:0;margin:12px 0}
.pstep{display:flex;gap:14px;align-items:flex-start;background:var(--panel);border:1px solid var(--faint);border-radius:12px;padding:12px 16px}
.pstep b{color:var(--ink);font-size:.98rem}
.pstep p{color:var(--muted);font-size:.86rem;margin:2px 0 0;max-width:78ch}
.pn{flex:none;width:26px;height:26px;border-radius:999px;background:var(--green);color:#04120c;font-weight:800;display:flex;align-items:center;justify-content:center;font-size:.85rem}
.pdash{width:2px;height:14px;background:var(--line);margin-left:29px}
.engines{display:grid;grid-template-columns:1fr auto 1fr;gap:14px;align-items:center;margin:12px 0}
.engine{background:var(--panel);border:1px solid var(--faint);border-radius:14px;padding:16px}
.engine h4{color:var(--ink);margin:6px 0 6px;font-size:.98rem}
.engine p{color:var(--muted);font-size:.86rem;margin:0}
.etag{display:inline-block;font-family:ui-monospace,Menlo,monospace;font-size:.72rem;color:var(--green);background:var(--panel-2);border-radius:6px;padding:2px 8px}
.ejoin{text-align:center;color:var(--muted);font-size:.74rem;line-height:1.5;border-left:1px dashed var(--line);border-right:1px dashed var(--line);padding:0 12px}
@media (max-width:640px){.flow-arrow{transform:rotate(90deg)}.flow-serial{flex-direction:column}.engines{grid-template-columns:1fr}.ejoin{border:none;border-top:1px dashed var(--line);border-bottom:1px dashed var(--line);padding:8px 0}}
/* evidence file tree */
.filetree .ft-head{margin-bottom:8px}
.filetree .ft-head span{color:var(--muted);font-size:.84rem}
.filetree ul{list-style:none;padding-left:0;margin:0}
.filetree li{padding:6px 0;border-bottom:1px solid var(--faint);font-size:.88rem;color:var(--ink)}
.filetree li:last-child{border-bottom:none}
.filetree li em{color:var(--muted);font-style:normal}
.tag-deep{display:inline-block;font-size:.7rem;font-weight:700;letter-spacing:.05em;text-transform:uppercase;color:#04120c;background:var(--green);border-radius:6px;padding:2px 8px}
/* coverage surfaces */
.surf-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px;margin:10px 0}
.surf-col h4{margin-top:0}
.surf{background:var(--panel);border:1px solid var(--faint);border-radius:10px;padding:9px 12px;margin-bottom:8px}
.surf.surf-deep{border-color:var(--green);box-shadow:0 0 0 1px var(--green) inset}
.surf>code{display:block;color:var(--green);font-size:.8rem}
.surf .sd{display:block;color:var(--muted);font-size:.78rem;margin:2px 0 5px}
.sb{font-size:.68rem;padding:1px 8px;border-radius:999px;border:1px solid var(--line)}
.sb.both{color:var(--green)}
.sb.local{color:var(--amber);border-color:rgba(255,211,106,.4)}
/* attack-cost live ticker */
.tick-grid{display:grid;grid-template-columns:1.2fr 1fr;gap:16px;margin:10px 0}
.tick-hero{background:var(--panel);border:1px solid var(--green);border-radius:16px;padding:20px 22px;box-shadow:0 0 0 1px var(--green) inset}
.tick-label{color:var(--muted);font-size:.82rem;text-transform:uppercase;letter-spacing:.05em}
.tick-big{font-size:clamp(2.4rem,6vw,3.6rem);font-weight:800;color:var(--green);letter-spacing:-.02em;font-variant-numeric:tabular-nums;line-height:1.05;margin:6px 0}
.tick-sub{color:var(--ink);font-size:.95rem}
.tick-cards{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.tick-card{background:var(--panel);border:1px solid var(--faint);border-radius:12px;padding:12px 14px}
.tick-card b{display:block;font-size:1.25rem;color:var(--ink);font-variant-numeric:tabular-nums}
.tick-card span{color:var(--muted);font-size:.76rem}
.tick-rows{margin:8px 0}
.tick-row{display:flex;justify-content:space-between;gap:12px;align-items:baseline;border-bottom:1px solid var(--faint);padding:8px 2px;font-size:.9rem}
.tick-row span{color:var(--muted)}.tick-row b{color:var(--ink);font-variant-numeric:tabular-nums;text-align:right}
#tk-status{color:var(--green)}
@media (max-width:640px){.tick-grid{grid-template-columns:1fr}}
</style>
"""

# ---------------------------------------------------------------- inject
doc = open(BASE, encoding="utf-8").read()

# 1) nav links
nav_anchor = '<a href="#walkthrough">Interface</a>\n      </div>'
assert nav_anchor in doc, "nav anchor not found"
doc = doc.replace(nav_anchor, '<a href="#walkthrough">Interface</a>\n        <a href="#attackcost">Attack&nbsp;cost</a>\n        <a href="#dsl">DSL</a>\n        <a href="#antithesis">Antithesis</a>\n        <a href="#primitives">Primitives</a>\n        <a href="#coverage">Coverage</a>\n        <a href="#consensus">Consensus</a>\n        <a href="#evidence">Evidence</a>\n        <a href="#extend">Extend</a>\n      </div>', 1)

# 2) styles before </head>
assert "</head>" in doc
doc = doc.replace("</head>", REF_CSS + "\n</head>", 1)

# 3) sections before <footer
m = re.search(r'<footer', doc)
assert m, "footer not found"
doc = doc[:m.start()] + SEC_TICKER + "\n" + SEC_DSL + "\n" + SEC_ANTI + "\n" + SEC_PRIM + "\n" + SEC_COVERAGE + "\n" + SEC_CONSENSUS + "\n" + SEC_EVIDENCE + "\n" + SEC_EXTEND + "\n" + doc[m.start():]

open(OUTFILE, "w", encoding="utf-8").write(doc)
print("wrote", OUTFILE, "bytes:", len(doc))
print("family bars:", fam_data)
print("subfamily bars:", len(sub_data), "themes")
