#!/usr/bin/env python3
"""Loop a DWARF scenario for a wall-clock duration, applying verify_scenario each
iteration. Emits a JSONL heartbeat + final summary. Bounded disk: passing bundles
are pruned (keep last N), failing/divergent bundles are kept in full as evidence.

Each scenario gets its own workdir so the cwd-relative `outputs/` of parallel runs
never collide."""
import argparse
import json
import os
import shutil
import sys
import time


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True, help="absolute path to scenario yaml/json")
    ap.add_argument("--dwarf-root", default="/home/dwarf/dwarf-v4")
    ap.add_argument("--workdir", required=True, help="per-scenario working dir (cwd for outputs/)")
    ap.add_argument("--duration", type=int, default=14400, help="wall-clock seconds (default 4h)")
    ap.add_argument("--max-iters", type=int, default=0, help="cap iterations (0 = unlimited; for smoke)")
    ap.add_argument("--registry", default=None)
    ap.add_argument("--keep-passing", type=int, default=3, help="retain this many most-recent passing bundles")
    args = ap.parse_args()

    sys.path.insert(0, os.path.join(args.dwarf_root, "dwarf"))
    from profile_manager import scenario as S

    workdir = os.path.abspath(args.workdir)
    runs_dir = os.path.join(workdir, "runs")
    state_dir = os.path.join(workdir, "state")
    for d in (workdir, runs_dir, state_dir):
        os.makedirs(d, exist_ok=True)
    registry = args.registry or os.path.join(args.dwarf_root, "dwarf/primitives/registry.json")
    hb = open(os.path.join(workdir, "heartbeat.jsonl"), "a", buffering=1)
    os.chdir(workdir)

    sid = os.path.basename(args.scenario)
    started = time.time()
    deadline = started + args.duration
    n = passed = failed = errored = 0
    passing_runs = []

    def emit(rec):
        hb.write(json.dumps(rec) + "\n")

    emit({"event": "start", "scenario": sid, "ts": int(started), "duration_s": args.duration,
          "max_iters": args.max_iters})

    while time.time() < deadline and (args.max_iters == 0 or n < args.max_iters):
        n += 1
        t0 = time.time()
        try:
            res = S.verify_scenario(args.scenario, runs_dir=runs_dir, state_dir=state_dir,
                                    registry_path=registry)
            state, reason, run_id = res["state"], res["reason"], res["run_id"]
            asserts = res.get("assertions") or {}
        except Exception as e:  # infra hiccup: log, pause, keep going
            errored += 1
            emit({"iter": n, "ts": int(time.time()), "state": "error", "err": str(e)[:400],
                  "elapsed": round(time.time() - t0, 1), "pass": passed, "fail": failed, "err_n": errored})
            time.sleep(5)
            continue

        dt = round(time.time() - t0, 1)
        rd = os.path.join(runs_dir, str(run_id))
        if state == "pass":
            passed += 1
            passing_runs.append(rd)
            while len(passing_runs) > args.keep_passing:
                shutil.rmtree(passing_runs.pop(0), ignore_errors=True)
        else:  # DIVERGENCE or assertion fail -- keep the bundle
            failed += 1

        emit({"iter": n, "ts": int(time.time()), "state": state, "reason": reason,
              "run_id": str(run_id), "assertions": asserts, "elapsed": dt,
              "pass": passed, "fail": failed, "err_n": errored,
              "remaining_s": max(0, int(deadline - time.time()))})

    summary = {"scenario": sid, "iterations": n, "passed": passed, "failed": failed,
               "errored": errored, "duration_s": round(time.time() - started, 1),
               "finished_ts": int(time.time())}
    json.dump(summary, open(os.path.join(workdir, "summary.json"), "w"), indent=2)
    emit({"event": "summary", **summary})
    hb.close()


if __name__ == "__main__":
    main()
