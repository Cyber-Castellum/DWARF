#!/usr/bin/env python3
"""Generate a client-facing HTML report for a DWARF fuzzing campaign.

Single source of truth is a results JSON (see tools/campaign_results.json). The
local soak log (tools/sp3a_blockfetch_campaign.sh output) can be folded in with
--soak-log to auto-populate the soak timeline + verdict.

Usage:
  python3 tools/generate_campaign_report.py campaign_results.json out.html \
      [--soak-log /path/to/sp3a_campaign.log]

The output is a self-contained HTML file (no external assets, print-to-PDF
friendly) suitable for presenting to a client.
"""
import json
import sys
import re
import datetime
from html import escape


def parse_soak_log(path):
    """Parse the campaign soak log into samples + verdict."""
    samples, verdict, seed, start = [], None, None, None
    try:
        lines = open(path).read().splitlines()
    except OSError:
        return None
    for ln in lines:
        m = re.search(r"(\S+Z) CAMPAIGN START: (.+)", ln)
        if m:
            start = m.group(1)
        m = re.search(r"reproduce with --seed (0x[0-9a-f]+)", ln)
        if m:
            seed = m.group(1)
        m = re.search(r"(\S+Z) served=(\d+) VRF=(\d+) advRestart=(\d+) relay2Restart=(\d+) relay2=(\w+)", ln)
        if m:
            samples.append({
                "t": m.group(1), "served": int(m.group(2)), "vrf": int(m.group(3)),
                "advRestart": int(m.group(4)), "relay2Restart": int(m.group(5)),
                "relay2": m.group(6),
            })
        m = re.search(r"CAMPAIGN DONE: .*-> (.+)", ln)
        if m:
            verdict = m.group(1).strip()
    if not samples and start is None:
        return None
    return {"start": start, "seed": seed, "samples": samples, "verdict": verdict}


def chip(status):
    s = status.lower()
    cls = {"passed": "ok", "pass": "ok", "completed": "ok",
           "failed": "bad", "fail": "bad",
           "unfound": "muted", "in progress": "warn", "pending": "warn"}.get(s, "muted")
    return f'<span class="chip {cls}">{escape(status)}</span>'


def sparkline(samples, w=560, h=90):
    """Dependency-free inline SVG of served-over-time."""
    pts = [s["served"] for s in samples]
    if len(pts) < 2:
        return '<p class="muted">Not enough samples yet for a chart.</p>'
    mx = max(pts) or 1
    n = len(pts)
    coords = []
    for i, v in enumerate(pts):
        x = 40 + (w - 60) * i / (n - 1)
        y = h - 20 - (h - 35) * v / mx
        coords.append(f"{x:.1f},{y:.1f}")
    poly = " ".join(coords)
    last = pts[-1]
    return f'''<svg viewBox="0 0 {w} {h}" width="100%" style="max-width:{w}px">
      <polyline fill="none" stroke="var(--accent)" stroke-width="2" points="{poly}"/>
      <text x="40" y="14" fill="var(--muted)" font-size="11">mutated blocks served (cumulative) — peak {mx}</text>
      <text x="{w-12}" y="{h-20-(h-35)*last/mx:.0f}" fill="var(--accent)" font-size="11" text-anchor="end">{last}</text>
    </svg>'''


def render(data, soak):
    gen = data.get("generated") or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%MZ")
    runs = data.get("antithesis_runs", [])

    run_blocks = []
    for r in runs:
        rows = "".join(
            f'<tr><td><code>{escape(a["name"])}</code></td><td>{chip(a["status"])}</td>'
            f'<td class="muted">{escape(a.get("note",""))}</td></tr>'
            for a in r.get("assertions", [])
        )
        c = r.get("counts", {})
        counts = ""
        if c:
            counts = (f'<p class="muted" style="margin:.3rem 0 0">Properties: '
                      f'<b class="ok">{c.get("passed",0)} passed</b> · '
                      f'{c.get("failed",0)} failed · {c.get("unfound",0)} unfound '
                      f'<span class="muted">(failures/unfound are expected harness artifacts — see notes)</span></p>')
        run_blocks.append(f'''
        <div class="card">
          <div class="runhead">
            <b>{escape(r.get("label",""))}</b> {chip(r.get("status","")) }
            <span class="muted">· {escape(r.get("duration","")) } · image {escape(r.get("image",""))} · commit <code>{escape(r.get("commit",""))}</code> · faults: {escape(r.get("faults","none"))}</span>
          </div>
          <p class="muted" style="margin:.2rem 0">testRunId <code>{escape(r.get("testRunId",""))}</code></p>
          <table><tr><th>Assertion</th><th>Result</th><th>Meaning</th></tr>{rows}</table>
          {counts}
        </div>''')

    soak_html = ""
    if soak:
        samples = soak.get("samples", [])
        last = samples[-1] if samples else None
        crashes = max((s["relay2Restart"] for s in samples), default=0)
        verdict = soak.get("verdict") or ("running" if samples else "starting")
        vcls = "bad" if crashes > 0 else "ok"
        rows = "".join(
            f'<tr><td class="muted">{escape(s["t"])}</td><td>{s["served"]}</td>'
            f'<td>{s["vrf"]}</td><td>{s["relay2Restart"]}</td><td>{escape(s["relay2"])}</td></tr>'
            for s in samples[-12:]
        )
        soak_html = f'''
        <h2>Local soak campaign (8h, build-host)</h2>
        <div class="card">
          <p>A sustained block-fetch soak: the adversary served a continuous stream of
          structurally-mutated blocks to an eclipsed cardano-node against the evolving
          real chain. Seed <code>{escape(soak.get("seed") or "n/a")}</code>.</p>
          {sparkline(samples)}
          <p style="margin:.6rem 0 0">Mutated blocks served: <b class="ok">{last["served"] if last else 0}</b>
             &nbsp;·&nbsp; Node restarts (crash oracle): <b class="{vcls}">{crashes}</b>
             &nbsp;·&nbsp; Verdict: <b class="{vcls}">{escape(verdict)}</b></p>
          <details style="margin-top:.6rem"><summary class="muted">recent samples</summary>
          <table style="margin-top:.4rem"><tr><th>time (UTC)</th><th>served</th><th>VRF errs</th><th>node restarts</th><th>node</th></tr>{rows}</table>
          </details>
        </div>'''

    repro = "".join(f"<li>{escape(x)}</li>" for x in data.get("reproducibility", []))
    scope = "".join(f"<li>{escape(x)}</li>" for x in data.get("scope_notes", []))

    return f'''<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(data.get("title","DWARF Campaign Report"))}</title>
<style>
  :root{{--bg:#ffffff;--ink:#1a2230;--muted:#5b6573;--accent:#2563eb;--ok:#15803d;--bad:#b91c1c;--warn:#b45309;--rule:#e2e8f0;--card:#f8fafc;--mono:"SFMono-Regular",Menlo,Consolas,monospace}}
  *{{box-sizing:border-box}} html,body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}}
  .wrap{{max-width:880px;margin:0 auto;padding:40px 28px 80px}}
  h1{{font-size:26px;margin:0 0 4px}} h2{{font-size:18px;margin:30px 0 10px;border-bottom:2px solid var(--rule);padding-bottom:6px}}
  .sub{{color:var(--muted);font-size:13px;margin-bottom:22px}}
  code{{font-family:var(--mono);font-size:.86em;background:#eef2f7;padding:1px 5px;border-radius:4px}}
  .card{{background:var(--card);border:1px solid var(--rule);border-radius:10px;padding:14px 18px;margin:12px 0}}
  .hero{{background:linear-gradient(135deg,#eff6ff,#f8fafc);border:1px solid #dbeafe;border-radius:12px;padding:18px 22px;margin:8px 0 8px}}
  .hero b{{color:var(--accent)}}
  table{{border-collapse:collapse;width:100%;font-size:13.5px;margin:6px 0}}
  th,td{{border:1px solid var(--rule);padding:7px 10px;text-align:left;vertical-align:top}} th{{background:#f1f5f9;color:var(--muted);font-weight:600}}
  .chip{{display:inline-block;font:600 11.5px/1.4 var(--mono);padding:1px 8px;border-radius:20px;border:1px solid}}
  .chip.ok{{color:var(--ok);border-color:#bbf7d0;background:#f0fdf4}}
  .chip.bad{{color:var(--bad);border-color:#fecaca;background:#fef2f2}}
  .chip.warn{{color:var(--warn);border-color:#fde68a;background:#fffbeb}}
  .chip.muted{{color:var(--muted);border-color:var(--rule);background:#fff}}
  .ok{{color:var(--ok)}} .bad{{color:var(--bad)}} .muted{{color:var(--muted)}}
  .runhead{{display:flex;flex-wrap:wrap;gap:8px;align-items:baseline}}
  ul{{margin:6px 0;padding-left:20px}} li{{margin:3px 0}}
  details summary{{cursor:pointer}}
  @media print{{.wrap{{max-width:none;padding:0}} .card,.hero{{break-inside:avoid}} a{{color:inherit;text-decoration:none}}}}
</style></head><body><div class="wrap">
  <h1>{escape(data.get("title","DWARF — Cardano Node CBOR Fuzzing Campaign"))}</h1>
  <div class="sub">{escape(data.get("client_line",""))} · generated {escape(gen)}</div>

  <div class="hero">{data.get("summary","")}</div>

  <h2>What was tested</h2>
  <div class="card">{data.get("objective","")}</div>
  {('<h2>Coverage matrix</h2><div class="card">' + data["coverage"] + '</div>') if data.get("coverage") else ''}

  <h2>Method</h2>
  <div class="card">{data.get("method","")}</div>

  <h2>Live results — Antithesis</h2>
  {''.join(run_blocks) if run_blocks else '<div class="card muted">No Antithesis runs recorded yet.</div>'}
  {soak_html}

  <h2>Findings</h2>
  <div class="card">{data.get("findings","")}</div>

  <h2>Reproducibility</h2>
  <div class="card"><ul>{repro}</ul></div>

  <h2>Scope &amp; honest limitations</h2>
  <div class="card"><ul>{scope}</ul></div>

  <p class="sub" style="margin-top:30px">{escape(data.get("footer",""))}</p>
</div></body></html>'''


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    data = json.load(open(sys.argv[1]))
    out = sys.argv[2]
    soak = None
    if "--soak-log" in sys.argv:
        soak = parse_soak_log(sys.argv[sys.argv.index("--soak-log") + 1])
    open(out, "w").write(render(data, soak))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
