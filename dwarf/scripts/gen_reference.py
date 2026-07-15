#!/usr/bin/env python3
"""
Generate the DWARF DSL + primitives reference (markdown + HTML) from the
authoritative sources:
  - spec/v1/schema.json      -> the scenario DSL field reference / lifecycle
  - primitives/registry.json -> the canonical primitive list (completeness)
  - curated DESC below       -> plain-English purpose / oracle per primitive

Re-run after adding a primitive: any registry primitive missing a DESC entry
is still listed (with a humanised fallback) AND reported, so the doc can never
silently fall out of sync with the registry again.

Usage:
  python3 gen_reference.py <dwarf_dir> <out_dir>
Emits: spec-reference.md, primitives-reference.md,
       spec-reference.html, primitives-reference.html  (in <out_dir>)
"""
import json, os, sys, html, re, glob
from collections import defaultdict

DWARF = sys.argv[1] if len(sys.argv) > 1 else "."
OUT   = sys.argv[2] if len(sys.argv) > 2 else "."

# ---------------------------------------------------------------- doc model
def H(level, text): return ("h", level, text)
def P(text):        return ("p", text)
def NOTE(text):     return ("note", text)
def CODE(text, lang="jsonc"): return ("code", text, lang)
def TABLE(headers, rows):     return ("table", headers, rows)
def UL(items):      return ("ul", items)

def render_md(blocks):
    out = []
    for b in blocks:
        if b[0] == "h":
            out.append(f"\n{'#'*b[1]} {b[2]}\n")
        elif b[0] == "p":
            out.append(b[1] + "\n")
        elif b[0] == "note":
            out.append(f"> {b[1]}\n")
        elif b[0] == "code":
            out.append(f"```{b[2]}\n{b[1]}\n```\n")
        elif b[0] == "ul":
            out.append("\n".join(f"- {i}" for i in b[1]) + "\n")
        elif b[0] == "table":
            headers, rows = b[1], b[2]
            out.append("| " + " | ".join(headers) + " |")
            out.append("|" + "|".join(["---"]*len(headers)) + "|")
            for r in rows:
                cells = [str(c).replace("|", "\\|").replace("\n", " ") for c in r]
                out.append("| " + " | ".join(cells) + " |")
            out.append("")
    return "\n".join(out).strip() + "\n"

CSS = """
:root{--bg:var(--wb-bg,#0e1116);--surface:var(--wb-surface,#161b22);--ink:var(--wb-ink,#e6edf3);
--muted:var(--wb-muted,#8b949e);--rule:var(--wb-rule,#30363d);--accent:var(--wb-accent,#58a6ff);
--accent2:var(--wb-accent-2,#3fb950);--radius:var(--wb-card-radius,10px);--pad:var(--wb-layout-padding,18px)}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;-webkit-text-size-adjust:100%}
.wrap{max-width:960px;margin:0 auto;padding:var(--pad)}
h1{font-size:25px;line-height:1.2;margin:.2em 0 .1em;border-bottom:1px solid var(--rule);padding-bottom:.35em}
h2{font-size:19px;margin:32px 0 6px;padding-bottom:5px;border-bottom:1px solid var(--rule)}
h3{font-size:15px;margin:22px 0 4px;color:var(--accent)}
h4{font-size:13.5px;margin:16px 0 2px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
p{margin:8px 0}
code{background:rgba(127,127,127,.16);padding:1px 5px;border-radius:5px;font-size:.86em;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
pre{background:var(--surface);border:1px solid var(--rule);border-radius:var(--radius);padding:12px;overflow-x:auto;font-size:12.5px;line-height:1.45}
pre code{background:none;padding:0}
blockquote{margin:10px 0;padding:8px 12px;border-left:3px solid var(--accent);background:var(--surface);border-radius:6px;color:var(--muted)}
ul{padding-left:20px}li{margin:3px 0}
.tablewrap{overflow-x:auto;margin:10px 0}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{border:1px solid var(--rule);padding:6px 9px;text-align:left;vertical-align:top}
th{color:var(--muted);font-weight:600;background:var(--surface)}
td code{white-space:nowrap}
.toc{background:var(--surface);border:1px solid var(--rule);border-radius:var(--radius);padding:12px 16px;margin:16px 0;font-size:13.5px}
.toc a{color:var(--accent);text-decoration:none}.toc a:hover{text-decoration:underline}
.kicker{color:var(--accent);font-weight:700;letter-spacing:.08em;text-transform:uppercase;font-size:12px}
.lead{color:var(--muted);font-size:15px}
"""

def slug(t): return re.sub(r'[^a-z0-9]+','-', t.lower()).strip('-')

def md_inline_to_html(s):
    s = html.escape(s)
    # stash code spans so emphasis/link rules never touch their contents
    codes = []
    def _stash(m):
        codes.append(m.group(1)); return f"\x00{len(codes)-1}\x00"
    s = re.sub(r'`([^`]+)`', _stash, s)
    s = re.sub(r'\*\*([^*]+?)\*\*', r'<strong>\1</strong>', s)          # **bold**
    s = re.sub(r'(?<!\*)\*(?!\*)([^*\n]+?)\*(?!\*)', r'<em>\1</em>', s)  # *italic*
    s = re.sub(r'\[([^\]]+)\]\((https?://[^)]+)\)', r'<a href="\2">\1</a>', s)
    s = re.sub(r'\x00(\d+)\x00', lambda m: f"<code>{codes[int(m.group(1))]}</code>", s)
    return s

def render_html(title, subtitle, favicon_kicker, blocks):
    # TOC from h2s
    h2s = [b[2] for b in blocks if b[0]=="h" and b[1]==2]
    body = []
    for b in blocks:
        if b[0]=="h":
            lvl=b[1]; body.append(f'<h{lvl} id="{slug(b[2])}">{md_inline_to_html(b[2])}</h{lvl}>')
        elif b[0]=="p":
            body.append(f"<p>{md_inline_to_html(b[1])}</p>")
        elif b[0]=="note":
            body.append(f"<blockquote>{md_inline_to_html(b[1])}</blockquote>")
        elif b[0]=="code":
            body.append(f"<pre><code>{html.escape(b[1])}</code></pre>")
        elif b[0]=="ul":
            body.append("<ul>"+"".join(f"<li>{md_inline_to_html(i)}</li>" for i in b[1])+"</ul>")
        elif b[0]=="table":
            headers,rows=b[1],b[2]
            th="".join(f"<th>{md_inline_to_html(h)}</th>" for h in headers)
            trs=""
            for r in rows:
                trs+="<tr>"+"".join(f"<td>{md_inline_to_html(str(c))}</td>" for c in r)+"</tr>"
            body.append(f'<div class="tablewrap"><table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table></div>')
    toc = '<div class="toc"><strong>Contents</strong><br>' + " · ".join(
        f'<a href="#{slug(h)}">{html.escape(h)}</a>' for h in h2s) + "</div>"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{html.escape(title)}</title>
<style>{CSS}</style></head><body><div class="wrap">
<div class="kicker">{html.escape(favicon_kicker)}</div>
<h1>{html.escape(title)}</h1>
<p class="lead">{md_inline_to_html(subtitle)}</p>
{toc}
{''.join(body)}
</div></body></html>"""

# ---------------------------------------------------------------- curated descriptions
# assertions: the pass condition (the "expected outcome"). load/etc: the purpose.
DESC = {
# ---- assertions (oracle / pass condition) ----
"parse_succeeds_or_clean_error":"PASS iff no parse outcome is a crash (every input is `ok` or `clean_error`) over >= min_outcomes_count inputs.",
"roundtrip_equals_original":"PASS iff every `ok` parse re-encodes to bytes identical to the input, over >= min_inputs_parsed inputs.",
"parser_bounds_enforced":"PASS iff parser/deserialization bounds are enforced before any unbounded work.",
"malformed_input_parity_preserved":"PASS iff malformed-input handling matches the reference implementation (no divergence).",
"validation_path_parity_preserved":"PASS iff the validation path preserves reference parity with zero mismatched steps.",
"blockfetch_invalid_block_rejected":"PASS iff an invalid BlockFetch block payload is rejected.",
"blockfetch_invalid_range_rejected":"PASS iff an invalid BlockFetch range is rejected without serving any blocks.",
"blockfetch_response_range_strict":"PASS iff a mismatched BlockFetch response range is strictly rejected.",
"blockfetch_continuity_failure_rejected":"PASS iff a BlockFetch continuity failure is rejected before downstream state advances.",
"blockfetch_range_pressure_bounded":"PASS iff BlockFetch range pressure stays within resource bounds and >= min blocks/range-requests are observed.",
"chainsync_height_monotonicity_enforced":"PASS iff a non-incrementing ChainSync height is rejected cleanly.",
"chainsync_slot_monotonicity_enforced":"PASS iff a non-monotonic ChainSync slot is rejected cleanly.",
"chainsync_parent_discontinuity_rejected":"PASS iff a parent-hash discontinuity is rejected before the candidate chain advances.",
"chainsync_responder_rollback_then_forward_clean":"PASS iff a producer fork-switch yields clean rollback-then-forward follower behaviour, meeting minimum observed counts.",
"txsubmission_window_enforced":"PASS iff the negotiated txid window rejects overflow cleanly after enough txids/messages are observed.",
"txsubmission_batch_enforced":"PASS iff the negotiated tx-body batch limit rejects an oversize batch cleanly.",
"txsubmission_unexpected_body_rejected":"PASS iff an unexpected TxSubmission body is rejected with a stated reason.",
"keepalive_failure_budget_bounded":"PASS iff keepalive failures stay within the configured retry budget.",
"mux_ingress_overrun_scoped":"PASS iff a mux ingress overrun is scoped to the offending bearer only.",
"duplex_promotion_slot_limit_enforced":"PASS iff duplex promotion pressure respects the configured hard slot limit.",
"local_query_amplification_bounded":"PASS iff local-query amplification stays below the configured CPU ceiling.",
"local_submit_availability_preserved":"PASS iff local-submit stress preserves node availability and queue limits.",
"mempool_relay_pressure_bounded":"PASS iff mempool-relay pressure stays inside the configured budget and peak memory <= ceiling.",
"mempool_failure_contained":"PASS iff a mempool failure stays contained without loss of any node.",
"reconnection_clean":"PASS iff the reconnected node rejoins and matches the honest-quorum tip with real reconnect telemetry.",
"peer_connectivity_observed":"PASS iff all expected honest-honest peer edges are observed within the timeout.",
"peer_eviction_within_seconds":"PASS iff honest nodes evict byzantine peers within the allowed time window.",
"k_bound_rollback_recovered":"PASS iff a within-k rollback recovers consistently, with observed slot transitions.",
"chain_select_consistent":"PASS iff every observed node chain-selects the same tip.",
"chain_switch_consistent":"PASS iff all observed honest nodes reach the injected chain-switch tip.",
"tip_convergence_clean":"PASS iff all observed nodes converge to one tip group within tolerance before the deadline.",
"epoch_boundary_timing_within_bounds":"PASS iff the epoch boundary occurs within the configured slot window.",
"reward_calculation_boundary_invariant":"PASS iff reward-calculation invariants hold across the epoch boundary.",
"stake_snapshot_freeze_consistent":"PASS iff the stake snapshot remains byte-stable across the freeze window.",
"leadership_schedule_recomputes_clean":"PASS iff leadership-schedule recomputation matches deterministically.",
"hf_boundary_rule_consistent":"PASS iff all observed nodes apply one protocol version at the hard-fork boundary.",
"transition_window_validated":"PASS iff pre/post-hard-fork (HF) submissions validate under the matching rule windows.",
"mode_switch_genesis_observed":"PASS iff the genesis-mode transition reaches caught-up mode without peer-set capture.",
"praos_header_assertion_rejected":"PASS iff an invalid Praos header is rejected at the assertion boundary.",
"overlay_slot_forging_rejected":"PASS iff an overlay-slot forging attempt is rejected and the forged block is not adopted.",
"ledger_peer_stake_weight_preserved":"PASS iff observed ledger-peer stake weighting stays within the allowed delta.",
"big_ledger_peer_quorum_intact":"PASS iff the observed big-ledger-peer subset still contains >= the required expected top peers.",
"bootstrap_assumptions_safe":"PASS iff bootstrap assumptions remain explicit and downgrade-free.",
"topology_bootstrap_diversity_preserved":"PASS iff bootstrap topology preserves the minimum honest-peer diversity floor.",
"all_nodes_responsive":"PASS iff every observed node is responsive.",
"all_nodes_started_clean":"PASS iff the compose report has >= min_node_count nodes and >= min_completed clean start events.",
"load_events_are_ok":"PASS iff >= min_event_count outcome-bearing load events are present and none are non-ok.",
"substrate_quorum_observed":"PASS iff a quorum (>= minimum_fraction) of nodes agrees on one real, non-zero tip group.",
"honest_quorum_preserved":"PASS iff the honest-node quorum fraction >= minimum_fraction on an ok run.",
"honest_peer_set_uncompromised":"PASS iff each honest node retains >= minimum_honest_peers honest peers without capture.",
"hot_warm_churn_within_bounds":"PASS iff observed hot/warm peer churn <= maximum_events_per_hour.",
"quorum_holds_despite_byzantine":"PASS iff a real byzantine event occurs and honest quorum tip-convergence holds throughout observation.",
"byzantine_isolation_observed":"PASS iff complete isolation is observed between honest and byzantine nodes.",
"byzantine_cardano_node_recorded_clean":"PASS iff the byzantine cardano-node fault run records >= min_completed clean events.",
"panic_path_contained":"PASS iff a crash-triggering input stays on a contained non-panic path and the node stays up.",
"runtime_starvation_bounded":"PASS iff blocking work preserves runtime liveness without starvation.",
"container_runtime_hardening_observed":"PASS iff captured `docker inspect` artifacts prove hardening on every required container.",
"plutus_phase2_isvalid_mismatch_rejected":"PASS iff a phase-2 IsValid mismatch is rejected with ValidationTagMismatch.",
"plutus_phase2_exunits_overrun_rejected":"PASS iff a phase-2 ExUnits overrun is rejected on mempool admission.",
"plutus_phase2_donotintervene_retry_clean":"PASS iff DoNotIntervene retry behaviour stays within the configured budget.",
"plutus_phase2_differential_equivalent":"PASS iff Amaru and cardano-node agree on phase-2 Plutus admission behaviour.",
"snapshot_captured_clean":"PASS iff snapshot capture emits a non-empty snapshot and the node restarts cleanly.",
"snapshot_corruption_detected":"PASS iff snapshot corruption yields a changed snapshot digest (corruption is detected).",
"snapshot_restore_succeeded":"PASS iff snapshot restore restarts the target node and recovers health.",
"substrate_checkpoint_recorded_clean":"PASS iff the substrate checkpoint is non-empty and the substrate restarts cleanly.",
"substrate_resume_succeeded":"PASS iff resume restores the checkpoint and recovers substrate health.",
"forensic_snapshot_emitted_clean":"PASS iff the forensic snapshot bundle is non-empty (>= min_completed).",
"credential_ceremony_recorded_clean":"PASS iff the credential ceremony records >= min_completed clean events and >= min_keys_generated keys.",
"bundle_attestation_signature_valid":"PASS iff a verified bundle attestation (valid signature) is present.",
"bundle_chain_verify_clean":"PASS iff the bundle hash-chain verifies as all-linked.",
"bundle_diff_completed_clean":"PASS iff the bundle diff emits a non-empty result on an ok run.",
"bundle_sarif_export_valid":"PASS iff the SARIF export is schema-valid.",
"bundle_summary_compose_completed_clean":"PASS iff the composed summary bundle is non-empty (>= min_completed).",
"bundle_timeline_emitted_clean":"PASS iff the bundle timeline emits >= 1 signature record.",
"aflpp_smoke_exit_clean":"PASS iff the AFL++ smoke run exits clean, meeting completed/queue/execs/cycles/bitmap-coverage floors.",
# ---- load / setup / probe / fault / teardown (purpose) ----
"cbor_edge_cases":"Feed a curated list of hand-crafted CBOR edge-case byte strings at a decode target and classify each.",
"cbor_fuzz_structured":"Shape-aware CBOR fuzzing: generate a structurally-valid CBOR value from a shape tree, mutate inner bytes, feed to a decode target.",
"cbor_fuzz_target":"Blind random-bytes CBOR fuzzing of a decode target within a byte-length range.",
"runtime_aflpp_campaign":"Run a coverage-guided AFL++ campaign against a built binary, then replay findings against decode targets.",
"load_shell_command":"Run an arbitrary host shell command as a load/setup step and assert its exit code.",
"runtime_blockfetch_delay_success":"Delay BlockFetch responses but still eventually succeed (tolerance test).",
"runtime_blockfetch_delay_timeout":"Delay BlockFetch past the timeout threshold to force a timeout.",
"runtime_blockfetch_drop_timeout":"Drop BlockFetch responses entirely to trigger a timeout.",
"runtime_blockfetch_drop_isolated_peer":"Drop BlockFetch traffic from an isolated peer (peer-isolation handling).",
"runtime_blockfetch_continuity_failure":"Serve a BlockFetch stream with a continuity gap to test rejection.",
"runtime_blockfetch_invalid_block_cbor":"Serve a block with invalid CBOR over BlockFetch to test rejection.",
"runtime_blockfetch_invalid_range":"Request an invalid BlockFetch range to test rejection.",
"runtime_blockfetch_range_mismatch":"Serve a BlockFetch response whose range mismatches the request.",
"runtime_blockfetch_range_pressure":"Apply sustained BlockFetch range-request pressure to probe resource bounds.",
"runtime_chainsync_nonincrementing_height":"Serve a ChainSync header with non-incrementing height to test rejection.",
"runtime_chainsync_nonmonotonic_slot":"Serve a ChainSync header with a non-monotonic slot to test rejection.",
"runtime_chainsync_parent_discontinuity":"Serve a ChainSync header with a parent-hash discontinuity to test rejection.",
"runtime_chainsync_responder_fork_switch":"Drive a ChainSync responder fork-switch (rollback then forward) and observe follower behaviour.",
"runtime_client_blockfetch_burst":"Drive a burst of BlockFetch requests from a client to stress the responder.",
"runtime_client_blockfetch_multi_peer":"Drive BlockFetch from multiple simultaneous client peers.",
"runtime_client_chainsync_burst":"Drive a burst of ChainSync requests from a client.",
"runtime_client_chainsync_multi_peer":"Drive ChainSync from multiple simultaneous client peers.",
"runtime_txsubmission_window_pressure":"Push the TxSubmission txid inflight window past its negotiated bound.",
"runtime_txsubmission_batch_pressure":"Push an oversize TxSubmission body batch past the negotiated limit.",
"runtime_txsubmission_unexpected_body":"Send an unrequested TxSubmission body to test rejection.",
"shim_peer_invalid_cbor":"Adversarial peer that sends invalid CBOR frames to a target node.",
"shim_peer_malformed_blockfetch":"Adversarial peer that sends malformed BlockFetch messages.",
"shim_peer_malformed_handshake":"Adversarial peer that sends a malformed protocol handshake.",
"shim_peer_malformed_txsubmission":"Adversarial peer that sends malformed TxSubmission messages.",
"shim_peer_raw_bytes":"Adversarial peer that sends an arbitrary raw hex payload to a target node.",
"shim_responder_stale_blockfetch":"Hostile responder that serves stale BlockFetch data to a syncing node.",
"runtime_network_impairment":"Impair the link between two nodes (latency/jitter/loss/partition).",
"runtime_time_skew":"Skew a node's clock (libfaketime) for a duration to test time sensitivity.",
"runtime_partition_rejoin":"Partition then rejoin nodes to test convergence/recovery.",
"runtime_bandwidth_throttle":"Throttle a node's bandwidth to probe sync behaviour under a slow link.",
"runtime_perturb_ledger_peer_weights":"Perturb ledger-peer stake weights to probe peer-selection stability.",
"runtime_substitute_big_ledger_peers":"Substitute the big-ledger-peer set to probe Sybil/quorum resistance.",
"runtime_peersharing_fault":"Inject a PeerSharing fault (adversarial address exchange).",
"runtime_localtxmonitor_fault":"Inject a LocalTxMonitor fault against the mempool-inspection protocol.",
"runtime_keepalive_failure_cascade":"Force a cascade of KeepAlive failures to probe the disconnect/recovery budget.",
"runtime_mux_ingress_overrun":"Overrun a mux bearer's ingress to test per-bearer scoping.",
"runtime_duplex_promotion_pressure":"Apply duplex-promotion pressure to test the hard slot limit.",
"runtime_slow_loris_chainsync":"Slow-loris (byte-drip) a ChainSync connection to hold resources.",
"runtime_local_query_stress":"Stress LocalStateQuery to probe query amplification / central-processing-unit (CPU) ceiling.",
"runtime_local_submit_stress":"Stress LocalTxSubmission to probe availability and queue limits.",
"runtime_mempool_relay_pressure":"Apply mempool-relay pressure to probe budget and memory ceiling.",
"runtime_blocking_work_starvation":"Inject blocking work to probe runtime-liveness starvation bounds.",
"runtime_inject_hot_warm_churn":"Inject hot/warm peer churn to probe governor churn bounds.",
"runtime_handshake_version_negotiation_pressure":"Pressure handshake version negotiation to probe downgrade handling.",
"runtime_overlay_slot_forging":"Attempt overlay-slot forging to test rejection of the forged block.",
"runtime_malformed_input_differential":"Feed malformed input to Amaru and cardano-node and compare handling (differential).",
"runtime_validation_path_differential":"Compare validation-path behaviour across implementations (differential).",
"runtime_disk_full_probe":"Fill disk toward a limit to probe disk-full handling.",
"runtime_force_epoch_boundary":"Force the chain to an epoch-boundary slot to exercise the transition.",
"runtime_force_hf_boundary":"Force the chain to a hard-fork boundary slot to exercise the transition.",
"runtime_force_rollback":"Force a rollback of a chosen depth to test recovery.",
"runtime_simulate_era_transition":"Simulate an era transition over a slot window.",
"runtime_genesis_mode_simulate":"Force a node into Genesis-mode sync and watch for peer-set capture.",
"runtime_recompute_leadership_schedule":"Recompute the leadership schedule and check determinism.",
"runtime_simulate_stake_snapshot_update":"Simulate a stake-snapshot rollover at an epoch boundary.",
"runtime_trigger_rupd_pulse":"Trigger a reward-update (RUPD) pulse to exercise reward accounting.",
"runtime_simulate_peer_set_capture":"Simulate a peer-set capture / eclipse attempt.",
"runtime_bootstrap_assumption_probe":"Probe that bootstrap assumptions stay explicit and downgrade-free.",
"runtime_bootstrap_topology_concentration":"Probe bootstrap-topology concentration vs the honest-diversity floor.",
"runtime_panic_path_probe":"Feed a crash-candidate input and check the node stays on a contained non-panic path.",
"runtime_parser_bounds_probe":"Probe that parser bounds are enforced before unbounded work.",
"runtime_mempool_failure_probe":"Probe that a mempool failure stays contained.",
"runtime_praos_header_assertion_probe":"Serve an invalid Praos header and check it is rejected.",
"runtime_plutus_phase2_submit_probe":"Submit a Plutus phase-2 tx with is-valid / ex-units overrides to probe validation.",
"runtime_plutus_phase2_differential_observation":"Observe phase-2 Plutus admission on both implementations for equivalence.",
"runtime_runtime_starvation_probe":"Probe runtime-liveness starvation under blocking work.",
"runtime_kill_node":"Kill a node process (ungraceful) to test peer/recovery behaviour.",
"runtime_restart_node":"Restart a node and verify it recovers to a healthy synced state.",
"runtime_generated_node_freeze_check":"Verify a blocked node freezes while healthy peers keep progressing.",
"runtime_generated_node_port_drop_check":"Verify the effect of dropping a node's port while healthy peers continue.",
"runtime_generated_node_recovery_check":"Verify a recovered node reaches a required phase alongside healthy peers.",
"runtime_snapshot_capture":"Capture a node's on-disk state snapshot.",
"runtime_snapshot_corrupt":"Corrupt a captured snapshot (zero/truncate/flip) to test detection.",
"runtime_snapshot_restore":"Restore a snapshot and recover node health.",
"runtime_substrate_checkpoint":"Take a consistent checkpoint of the whole substrate.",
"runtime_substrate_resume":"Resume the substrate from a checkpoint.",
"runtime_profile_copied_state_divergence":"Start a node from copied state and observe general chain divergence.",
"runtime_profile_copied_state_chainsync_divergence":"Start from copied state and observe ChainSync-specific divergence.",
"runtime_profile_copied_state_recovery":"Verify recovery of a node started from copied state.",
"runtime_profile_copied_state_postremediation_blockfetch":"Check BlockFetch behaviour after remediating a copied-state node.",
"runtime_profile_restart_recovery":"Restart a node and verify recovery (profile flow).",
"runtime_profile_restart_postrecovery_blockfetch":"Check BlockFetch behaviour after a restart-recovery.",
"runtime_preview_parity_baseline":"Baseline parity between Amaru and cardano-node roots on the preview network.",
"runtime_preview_upstream_delay":"Inject upstream delay/jitter on preview, then recover, checking parity.",
"runtime_preview_upstream_drop":"Drop upstream preview traffic for a window, then recover.",
"runtime_preview_upstream_loss":"Apply packet loss to upstream preview traffic, then recover.",
"runtime_preview_upstream_reset":"Reset the upstream preview connection, then recover.",
"runtime_observability_log_baseline":"Capture a baseline of node logging output.",
"runtime_observability_trace_settings_baseline":"Capture a baseline of node tracing/trace-settings config.",
"runtime_live_implementation_baseline":"Capture a live-implementation baseline for a scenario across the runtime.",
"runtime_multi_node_observation":"Observe multiple nodes over a window, running a set of observation primitives.",
"runtime_resource_profile":"Sample a node's resource usage over time.",
"runtime_connection_state":"Snapshot a node's network connection state.",
"runtime_container_runtime_inspect":"Inspect the container runtime/state of the substrate (hardening evidence).",
"runtime_haskell_gc_capture":"Capture Haskell garbage-collection / run-time-system (GC/RTS) stats from a cardano-node target.",
"runtime_syscall_trace":"Trace syscalls of a target node during a workload.",
"runtime_pcap_capture":"Capture network packets — packet capture (pcap) — during a workload.",
"runtime_credential_ceremony":"Run a credential/key ceremony to generate pool credentials for a testnet.",
"runtime_cardano_lsq_extract":"Extract ledger state via a Local State Query over a node socket.",
"runtime_chain_switch_inject":"Inject a chain switch and observe honest-node convergence to the new tip.",
"runtime_forensic_snapshot":"Package selected run artifacts into a forensic snapshot archive.",
"runtime_bundle_attestation":"Produce a signed attestation for a result bundle.",
"runtime_bundle_chain":"Build/append a chained (hash-linked) evidence bundle with a stated reason.",
"runtime_bundle_chain_verify":"Verify the integrity of a bundle chain for a target run.",
"runtime_bundle_dedupe":"Deduplicate bundle entries for a target run.",
"runtime_bundle_diff":"Diff two runs' bundles over specified relative paths.",
"runtime_bundle_export":"Export a run's bundle (optionally signed).",
"runtime_bundle_export_sarif":"Export a run's findings as a Static Analysis Results Interchange Format (SARIF) v2.1.0 report.",
"runtime_bundle_promote":"Promote a run's bundle to a higher tier with a stated reason.",
"runtime_bundle_sign":"Cryptographically sign a run's bundle.",
"runtime_bundle_summary_compose":"Compose a combined summary across multiple bundles.",
"runtime_bundle_timeline":"Build a chronological timeline across bundles, with optional filters.",
"runtime_bundle_triage":"Triage a run's bundle, recording a reason and actor.",
# setup
"runtime_compose_substrate":"Bring up the docker-compose substrate and wait for health.",
"runtime_install_version":"Install/pin a specific node/implementation version into the runtime.",
"runtime_substrate_tip_warmup":"Warm a freshly-composed substrate until nodes reach a minimum tip/slot.",
# probe
"parser_exit_status":"Per-input probe: record each iteration's outcome to probes/parser_exit_status.ndjson (newline-delimited JavaScript Object Notation).",
# fault
"fault_local_port_delay":"Inject bounded loopback latency scoped to one local listener port (host tc netem).",
"fault_local_port_drop":"Drop loopback traffic to one target port for the load duration (host iptables).",
"fault_node_freeze":"Freeze (SIGSTOP) a target node for a window, then resume.",
"runtime_byzantine_cardano_node":"Run a byzantine cardano-node proxy that mutates/downgrades protocol traffic.",
# teardown
"runtime_teardown_substrate":"Tear down the substrate and record the outcome (runs regardless of pass/fail).",
}

# ---------------------------------------------------------------- Antithesis generator coverage
# Primitives the DWARF->Antithesis generator (profile_manager/antithesis_generator.py)
# can carry onto the native Antithesis backend. Ground truth: the generator is
# CBOR-only -- it requires exactly one `cbor_fuzz_*` load primitive on a
# `cardano-node` target, optionally + `runtime_aflpp_campaign` (coverage surface).
# The assertions below are the ones mapped to native SDK catalog entries
# (_ASSERTION_MAP). Everything else is local-backend only.
ANTITHESIS = {
    "cbor_fuzz_structured", "cbor_fuzz_target", "runtime_aflpp_campaign",
    "parse_succeeds_or_clean_error", "roundtrip_equals_original",
    "aflpp_smoke_exit_clean", "parser_exit_status",
}
def anti_mark(n):
    return "✓" if n in ANTITHESIS else "—"

# ---------------------------------------------------------------- verification evidence
# Computed from scenarios/ at build time: how far each primitive has actually been
# EXERCISED (evidence), NOT an implementation claim. Every primitive is fully
# implemented; this only records the depth/targets it has been run against.
def _walk_key(o, key):
    r=set()
    if isinstance(o, dict):
        v=o.get(key)
        if isinstance(v, str): r.add(v)
        for vv in o.values(): r|=_walk_key(vv, key)
    elif isinstance(o, list):
        for vv in o: r|=_walk_key(vv, key)
    return r

_SCEN_EVID=None
def scenario_evidence():
    global _SCEN_EVID
    if _SCEN_EVID is not None: return _SCEN_EVID
    ev=defaultdict(lambda: {"full":False,"smoke":False,"targets":set()})
    for f in glob.glob(os.path.join(DWARF, "scenarios", "*.yaml")):
        try: d=json.load(open(f))
        except Exception: continue
        smoke="smoke" in os.path.basename(f)
        tgt=d.get("target",{}).get("implementation","")
        impls=_walk_key(d,"impl") | _walk_key(d,"implementation")
        for p in _walk_key(d,"primitive"):
            e=ev[p]
            if smoke: e["smoke"]=True
            else: e["full"]=True
            if tgt: e["targets"].add(tgt)
            if "amaru" in impls: e["targets"].add("amaru")
    _SCEN_EVID=ev
    return ev

def verified_cell(n):
    e=scenario_evidence().get(n)
    if not e or (not e["full"] and not e["smoke"]): return "not yet exercised"
    depth="full" if e["full"] else "smoke"
    t=e["targets"]
    has_am="amaru" in t
    has_cn=("cardano-node" in t) or (bool(t) and not (has_am and len(t)==1))
    tg="cn+amaru" if (has_cn and has_am) else ("amaru" if has_am else "cn")
    return f"{depth} · {tg}"

# ---------------------------------------------------------------- load subfamily buckets
def load_subfamily(n):
    if n.startswith("cbor_"): return "CBOR fuzzing"
    if n=="runtime_aflpp_campaign": return "Coverage-guided fuzzing (American Fuzzy Lop plus-plus, AFL++)"
    if n=="load_shell_command": return "Generic execution"
    if n.startswith("runtime_blockfetch_"): return "BlockFetch pressure & faults"
    if n.startswith("runtime_chainsync_"): return "ChainSync faults"
    if n.startswith("runtime_client_"): return "Client-driven load (multi-peer / burst)"
    if n.startswith("runtime_txsubmission_"): return "TxSubmission pressure"
    if n.startswith("shim_"): return "Adversarial peer / responder (shim)"
    if n.startswith("runtime_preview_"): return "Preview-network upstream fault & parity"
    if n.startswith("runtime_profile_"): return "Copied-state / restart recovery profiles"
    if n.startswith("runtime_generated_node_"): return "Generated-node fault checks"
    if n.startswith("runtime_bundle_") or n=="runtime_forensic_snapshot": return "Forensics / evidence bundle"
    if n.startswith("runtime_snapshot_") or n.startswith("runtime_substrate_"): return "State snapshot / checkpoint"
    if any(k in n for k in ("epoch_boundary","hf_boundary","era_transition","force_rollback","genesis_mode","leadership_schedule","stake_snapshot","rupd","peer_set_capture")): return "Epoch / era / hard-fork control"
    if any(k in n for k in ("observability","baseline","multi_node_observation","resource_profile","connection_state","container_runtime_inspect","haskell_gc","syscall_trace","pcap")): return "Observability / capture / baselines"
    if n.endswith("_probe") or n.startswith("runtime_plutus_") or n.startswith("runtime_praos_"): return "Targeted probes"
    if n in ("runtime_kill_node","runtime_restart_node"): return "Node lifecycle"
    if n in ("runtime_credential_ceremony","runtime_cardano_lsq_extract"): return "Setup / extraction utilities"
    return "Network / resource / protocol faults"

# ---------------------------------------------------------------- build spec doc
def build_spec():
    b=[]
    b+=[H(1,"DWARF Scenario DSL Reference (spec v1)")]
    b+=[P("A **scenario** is a single file written in YAML (YAML Ain’t Markup Language, a human-readable data format) — an instance of DWARF's **domain-specific language (DSL)** for describing tests. It describes one repeatable experiment against a Cardano node: it declares *what to run against* (target + runtime), *what to do* (an ordered list of primitives), and *what must be true afterwards* (assertions). The golden rule: **a scenario is data; primitives are code.** A scenario can only *reference* primitives that are already registered in `primitives/registry.json` — pasted YAML can never introduce new behaviour. That registry boundary is the framework's safety guarantee. (Scenarios may equivalently be written in JSON (JavaScript Object Notation), since JSON is a subset of YAML.)")]
    b+=[NOTE("This file is generated from `spec/v1/schema.json` and `primitives/registry.json`. Do not hand-edit; run `scripts/gen_reference.py` after schema/registry changes.")]

    b+=[H(2,"The scenario lifecycle")]
    b+=[P("When you run a scenario, the runner executes six kinds of primitive in a fixed order. Two of them (**faults** and **probes**) run *concurrently with* the load phase rather than after it:")]
    b+=[UL([
      "**setup** — ordered. Prepare the world (install a version, compose a devnet, warm it to tip). Any failure aborts the run **before** load.",
      "**load** — ordered. The actual workload/strategy under test (fuzz a decoder, pressure a mini-protocol, force an epoch boundary…). The first load primitive's duration (or the scenario `duration`) bounds the run.",
      "**faults** — run *concurrently with load*. Degrade the environment while load runs (delay/drop a port, freeze a node, byzantine proxy).",
      "**probes** — sampled *concurrently with load*. Each streams a time series to `probes/<primitive>.ndjson` (newline-delimited JSON) in the run bundle.",
      "**assertions** — evaluated *after* load completes. Each records its value, the data points used, and pass/fail. These decide the scenario's overall verdict.",
      "**teardown** — ordered. Cleanup; runs **regardless of pass/fail**. Failures are logged but never change the verdict.",
    ])]
    b+=[P("So the serial spine is **setup → load → assertions → teardown**, with **faults** and **probes** layered over the load phase. `seed` seeds every primitive's random-number generator (RNG) so a run replays deterministically.")]

    # top-level fields
    schema=json.load(open(os.path.join(DWARF,"spec/v1/schema.json")))
    props=schema.get("properties",{}); req=set(schema.get("required",[]))
    rows=[]
    order=["spec_version","id","title","authors","tags","related_milestones","m1_trace","evidence_intent","promotion_blockers","testcase_candidate","target","runtime","profile","substrate","duration","seed","setup","load","faults","probes","assertions","teardown","phases","invariants","iterations","shrink"]
    meaning={
      "spec_version":"Spec major version. `v1` validates against this schema.",
      "id":"Globally-unique scenario id. Lowercase kebab-case; stable across renames (primary key).",
      "title":"Human-readable one-line title shown in dashboards/reports.",
      "authors":"Optional author handles.",
      "tags":"Free-form tags for indexing/filtering (e.g. `ser-deser`, `networking`, `abuse`).",
      "related_milestones":"Optional milestone labels this scenario supports (e.g. `M2`).",
      "m1_trace":"Traceability back to Milestone-1 artifacts (threat_ids / gap_ids / architecture_ids / risk_candidate_ids).",
      "evidence_intent":"How the run's evidence is intended to be used. Does not promote a finding by itself.",
      "promotion_blockers":"Gates that must close before this scenario can support a promoted finding.",
      "testcase_candidate":"Optional: auto-ingest the completed run as a testcase lifecycle record.",
      "target":"Which implementation is under test. Runner refuses if a referenced primitive doesn't support it.",
      "runtime":"Execution tier (see below). Runner refuses a primitive whose runtimes exclude this value.",
      "profile":"Devnet profile id. **Required when `runtime: devnet`, forbidden otherwise.**",
      "duration":"Wall-clock cap on the load phase. Units `s`/`m`/`h` (e.g. `30s`, `10m`, `2h`).",
      "seed":"Integer or `0x…` hex seeding every primitive's RNG. Omitted → runner generates and records one. Required for deterministic replay.",
      "setup":"Ordered setup primitive refs. Failure aborts before load.",
      "load":"Ordered load primitive refs (the workload).",
      "faults":"Fault primitives run concurrently with load.",
      "probes":"Probe primitives sampled concurrently with load.",
      "assertions":"Assertion primitives evaluated after load; decide pass/fail.",
      "teardown":"Ordered teardown refs; always run; never change the verdict.",
      "iterations":"Trial count when the scenario contains FUZZ slots (default 100). Each iteration becomes a child bundle.",
      "shrink":"When true (default), a failing FUZZ iteration is minimised toward smaller inputs.",
      "substrate":"Inline devnet composition (nodes + topology + launch mode). Devnet scenarios use this instead of / alongside a `profile` id. See the substrate section below.",
      "phases":"Optional phased execution — an ordered list of phases, each carrying its own setup/load/faults/probes/assertions/teardown, instead of declaring them once at top level.",
      "invariants":"Optional declarative invariants attached to the scenario (runner-interpreted).",
    }
    for k in order:
        v=props.get(k,{})
        t=v.get("type","—")
        if isinstance(t,list): t="/".join(t)
        enum=v.get("enum")
        typ=f"`{t}`"+ (f" enum: {', '.join('`%s`'%e for e in enum)}" if enum else "")
        rows.append([f"`{k}`", typ, "**yes**" if k in req else "no", meaning.get(k,(v.get('description') or '').split('.')[0])])
    b+=[H(2,"Top-level fields")]
    b+=[P(f"`target` requires `implementation` (`cardano-node` | `amaru`) and `version` (`any`, or a semver/branch/commit). Unknown top-level keys are rejected (`additionalProperties: false`). Required fields: {', '.join('`%s`'%r for r in sorted(req))}.")]
    b+=[TABLE(["field","type","required","meaning"], rows)]

    b+=[H(2,"Runtime tiers")]
    b+=[TABLE(["runtime","what it spins up","use for"],[
      ["`library`","nothing — drives a library/binary harness (shim) directly","fast, deterministic parser/decoder fuzzing (no node, no docker)"],
      ["`single-node`","one node process","behaviour that needs a live node but not a network"],
      ["`devnet`","a multi-node devnet via a `profile` (docker / host / multi-host)","consensus, mini-protocol, epoch and fault scenarios across a real topology"],
    ])]

    b+=[H(2,"The FUZZ mechanism")]
    b+=[P("Any primitive parameter can be replaced by the bare string `\"FUZZ\"` (the runner infers a generator from the primitive's declared parameter type) or the long-form `{\"fuzz\": {…}}` object. When any FUZZ slot is present the whole scenario is repeated `iterations` times (default 100), each iteration a child bundle; `shrink` (default true) minimises any failing input. Supported generator `type`s: `int`, `bytes`, `string`, `bool`, `choice`, `weighted_choice` (with `min`/`max`/`values`/`weights`).")]
    b+=[NOTE("The FUZZ slot mechanism (parameter-level) is distinct from the Concise Binary Object Representation (CBOR) **shape grammar** below (which is the parameter *of* the `cbor_fuzz_structured` primitive).")]

    b+=[H(2,"The CBOR shape grammar")]
    b+=[P("`cbor_fuzz_structured` takes a `shape` — a recursive tree that names a well-formed CBOR value by its structure while leaving leaf contents random. The generator walks the tree and emits real CBOR bytes; the `mutation_rate` then corrupts inner fields. This reaches deep decoder paths a blind random-bytes fuzzer rarely hits. Node types:")]
    b+=[TABLE(["type","fields","emits"],[
      ["`uint`","`max` (default 0xffffffff)","random unsigned int in [0,max] (major type 0)"],
      ["`bytes`","`length` (int, or `{min,max}`)","random byte string (major type 2)"],
      ["`text`","`length` (int, or `{min,max}`)","random ASCII (American Standard Code for Information Interchange) text string (major type 3)"],
      ["`bool`","—","random true/false (0xf5/0xf4)"],
      ["`null`","—","CBOR null (0xf6)"],
      ["`array`","`elements` (list of shapes)","definite array; header len = len(elements), then each element"],
      ["`map`","`entries` (list of `[key, shape]`; key = int or string)","definite map; each entry emits key then value shape"],
      ["`tag`","`tag` (int), `inner` (shape)","semantic tag wrapping the encoding of `inner` (major type 6)"],
      ["`any`","—","terminal wildcard: a short random uint in [0,100]"],
    ])]
    b+=[P("Annotated example — a Conway-style certificate skeleton (array of a discriminator + a nested [kind, 28-byte hash]):")]
    b+=[CODE('{\n  "type": "array",                       // CBOR array of 2 (major type 4)\n  "elements": [\n    {"type": "uint", "max": 18},         // cert discriminator 0..18\n    {"type": "array", "elements": [\n      {"type": "uint", "max": 1},        // credential kind 0..1\n      {"type": "bytes", "length": 28}    // blake2b-224 key/script hash\n    ]}\n  ]\n}')]
    b+=[P("This generates bytes like `82 12 82 01 58 1c <28 random bytes>` — a structurally-valid certificate with randomised leaves that the mutation pass then perturbs.")]

    b+=[H(2,"Two worked examples")]
    b+=[H(3,"Library-tier CBOR fuzz (no node)")]
    b+=[CODE('{\n  "spec_version": "v1",\n  "id": "cardano-node-cbor-tx-body-fuzz",\n  "title": "Library-tier CBOR fuzz of cardano-node tx-body parser",\n  "target": {"implementation": "cardano-node", "version": "any"},\n  "runtime": "library",\n  "seed": "0xCAFE0001",\n  "load": [\n    {"primitive": "cbor_fuzz_target", "target_id": "cardano-node-cbor-decode-tx-body",\n     "manifests_dir": "dwarf/targets/manifests", "iterations": 10000,\n     "min_bytes": 1, "max_bytes": 4096, "per_input_timeout_seconds": 2}\n  ],\n  "probes":     [{"primitive": "parser_exit_status"}],\n  "assertions": [{"primitive": "parse_succeeds_or_clean_error"}],\n  "teardown":   []\n}')]
    b+=[P("Reads as: feed 10,000 random 1–4096-byte inputs to the tx-body decoder shim; record each outcome; **pass iff none crashed** (every input parsed cleanly or was cleanly rejected).")]
    b+=[H(3,"Devnet-tier compound scenario (multi-node)")]
    b+=[P("A `devnet` scenario adds a `substrate` block (nodes + topology) and a `profile`, then sequences runtime primitives — e.g. skew a node's clock, force an epoch boundary, roll the stake snapshot, recompute the leadership schedule — and asserts the epoch-boundary invariants held. Setup composes the substrate; teardown tears it down; assertions like `epoch_boundary_timing_within_bounds` and `reward_calculation_boundary_invariant` decide the verdict.")]

    b+=[H(2,"The substrate block — inline devnet composition")]
    b+=[P("A `devnet` scenario declares the network under test inline via a `substrate` block (an alternative to naming a `profile`). It lists the nodes, their roles, and the peer topology.")]
    b+=[TABLE(["substrate field","meaning"],[
      ["`network`","Base network/config the devnet derives from (e.g. `preview`)."],
      ["`nodes[]`","The nodes — each with `id`, `impl` (`cardano-node`/`amaru`), `version`, `role`, optional `host`."],
      ["`topology.edges[]`","Directed peer edges `{from, to}` between node ids."],
      ["`compose_mode`","How nodes launch: `host` (native tmux) · `docker` (compose) · `multi-host` (SSH fan-out)."],
      ["`host_strategy` · `hosts`","Multi-host placement (which box each node runs on)."],
    ])]
    b+=[P("Node **roles** the runner recognises:")]
    b+=[TABLE(["role","behaviour"],[
      ["`honest`","Plays by the rules — the baseline."],
      ["`adversary`","A node the scenario drives adversarially (e.g. serves a private fork)."],
      ["`byzantine`","A node whose traffic is mutated/corrupted via a byzantine proxy."],
      ["`sybil`","A cheap identity used in eclipse / Sybil topologies."],
      ["`submitter`","Drives local-tx-submission load."],
      ["`observer`","Watches only — used to read tips/telemetry without participating."],
    ])]
    b+=[CODE('"substrate": {\n  "network": "preview",\n  "nodes": [\n    {"id": "node1", "impl": "cardano-node", "version": "10.7.1", "role": "honest"},\n    {"id": "adv1",  "impl": "cardano-node", "version": "10.7.1", "role": "adversary"}\n  ],\n  "topology": {"edges": [\n    {"from": "adv1", "to": "node1"}, {"from": "node1", "to": "adv1"}\n  ]}\n}')]
    b+=[H(3,"Phased scenarios")]
    b+=[P("Instead of one flat `setup`/`load`/`assertions` list, a scenario may use a `phases` array — an ordered list of phases, each carrying its own primitive lists. Useful when a run has distinct stages (baseline → fault → remediation → recheck). Primitives inside phases are validated the same way.")]

    b+=[H(2,"How to write & run your own")]
    b+=[UL([
      "Copy the closest example from `dwarf/scenarios/` and change `id`, `title`, and the `load` list.",
      "Pick a `runtime`: `library` for parser/decoder work, `devnet` for anything involving a live network.",
      "Reference only primitives from the **Primitives Reference** (the runner rejects unknown names). Match each primitive's declared `runtimes` and `supports`.",
      "Add `assertions` — these are your oracle; without them a run can't fail. See the assertion catalog for the exact pass condition of each.",
      "Set a `seed` for reproducibility. Add `iterations` if you use FUZZ slots.",
      "Run: `cardano-profile scenario run dwarf/scenarios/<your>.yaml`. Results land in a forensic bundle under `dwarf/runs/<run-id>/`.",
    ])]
    return b

# ---------------------------------------------------------------- build primitives doc
def build_primitives():
    reg=json.load(open(os.path.join(DWARF,"primitives/registry.json")))["primitives"]
    b=[]
    total=len(reg); covered=sum(1 for n in reg if n in DESC)
    b+=[H(1,"DWARF Primitives Reference")]
    b+=[P(f"The complete catalogue of the **{total}** primitives a scenario can reference — every *strategy* (what a scenario can do) and every *oracle* (what it can assert). Generated from `primitives/registry.json`; purposes/pass-conditions are curated. A scenario may only reference names listed here. Many primitives operate on CBOR (Concise Binary Object Representation), Cardano's binary wire and ledger encoding. The **Antithesis** column marks the primitives the DWARF&rarr;Antithesis generator can carry onto the Antithesis backend (CBOR-only by design &mdash; `cbor_fuzz_*` strategies, the `runtime_aflpp_campaign` coverage surface, and the assertions mapped to native SDK checks); everything else runs on the local backend only.")]
    b+=[NOTE(f"Coverage: {covered}/{total} primitives carry a curated description ({round(100*covered/total)}%). Any without one is still listed with its registry metadata. Regenerate with `scripts/gen_reference.py`.")]
    b+=[NOTE("**Every primitive here is fully implemented** (no stubs). The **verified** column is *evidence of exercise*, not an implementation claim: `full` = exercised by a full scenario, `smoke` = exercised by an example / smoke scenario; `cn` = cardano-node, `amaru` = Amaru. So `smoke · cn` means \"implemented, and exercised at smoke depth on cardano-node\" — not \"half-built.\" Note on targets: the registry declares cardano-node + Amaru support broadly, but Amaru is currently *verified* only on the CBOR-decode surfaces and a mixed-substrate baseline (19 primitives); elsewhere it is implemented but not yet exercised against Amaru. Computed from `scenarios/` at build time.")]
    b+=[P("Every primitive lists which **runtimes** it works in (`library` / `single-node` / `devnet`) and its **verified** status &mdash; the depth and target it has actually been exercised against (see the legend below). Common plumbing params (`timeout_seconds`, `output_dir`, `runtime_metadata_path`, `helper_script`) are omitted from the notes below; see each primitive's `params_schema` for the full list.")]

    def fam_table(names):
        rows=[]
        for n in sorted(names):
            p=reg[n]
            purpose=DESC.get(n) or ("*(" + n.replace("_"," ") + ")*")
            rt="·".join(x[:3] for x in p.get("runtimes",[]))
            rows.append([f"`{n}`", purpose, rt, verified_cell(n), anti_mark(n)])
        return TABLE(["primitive","purpose / pass-condition","runtimes","verified","Antithesis"], rows)

    # LOAD grouped by subfamily
    load_names=[n for n,p in reg.items() if p["family"]=="load"]
    buckets={}
    for n in load_names: buckets.setdefault(load_subfamily(n),[]).append(n)
    b+=[H(2,f"Load primitives — strategies ({len(load_names)})")]
    b+=[P("What a scenario *does*. These run in the `load` phase.")]
    for sub in sorted(buckets):
        b+=[H(3,sub)]
        b+=[fam_table(buckets[sub])]

    # ASSERTIONS grouped
    assn=[n for n,p in reg.items() if p["family"]=="assertion"]
    def assn_group(n):
        if n.startswith(("parse_","roundtrip","parser_","malformed_input","validation_path")): return "Parser / CBOR / input correctness"
        if n.startswith(("blockfetch_","chainsync_","txsubmission_","keepalive_","mux_","duplex_","local_query","local_submit","mempool_","reconnection","peer_")): return "Mini-protocol & local inter-process-communication (IPC) behaviour"
        if n.startswith(("k_bound","chain_select","chain_switch","tip_conv","epoch_","reward_","stake_snapshot","leadership_","hf_boundary","transition_window","mode_switch","praos_","overlay_","ledger_peer","big_ledger","bootstrap_","topology_")): return "Consensus / ledger / era"
        if n.startswith(("all_nodes","load_events","substrate_quorum","honest_","hot_warm")): return "Node health / quorum"
        if n.startswith(("quorum_holds","byzantine_","panic_path","runtime_starvation","container_runtime")): return "Byzantine / fault containment"
        if n.startswith("plutus_"): return "Plutus phase-2 validation"
        if n.startswith(("snapshot_","substrate_checkpoint","substrate_resume","forensic_","credential_","bundle_")): return "Forensics / snapshot / bundle"
        if n.startswith("aflpp_"): return "Fuzzing harness"
        return "Other"
    ag={}
    for n in assn: ag.setdefault(assn_group(n),[]).append(n)
    b+=[H(2,f"Assertion primitives — oracles ({len(assn)})")]
    b+=[P("What a scenario *proves*. Each is evaluated after load; the **pass condition** is the expected outcome. Thresholds shown are the tunable params.")]
    for g in sorted(ag):
        b+=[H(3,g)]
        b+=[fam_table(ag[g])]

    # setup / probe / fault / teardown
    for fam,label in [("setup","Setup primitives"),("probe","Probe primitives"),("fault","Fault primitives"),("teardown","Teardown primitives")]:
        names=[n for n,p in reg.items() if p["family"]==fam]
        if not names: continue
        b+=[H(2,f"{label} ({len(names)})")]
        b+=[fam_table(names)]

    # report uncovered
    uncovered=[n for n in reg if n not in DESC]
    if uncovered:
        b+=[H(2,"Primitives awaiting a curated description")]
        b+=[P("These are registered but not yet hand-described (listed above with a fallback). Add them to `DESC` in the generator: " + ", ".join(f"`{n}`" for n in sorted(uncovered)))]
    return b

# ---------------------------------------------------------------- emit
if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    spec=build_spec(); prim=build_primitives()
    open(os.path.join(OUT,"spec-reference.md"),"w").write(render_md(spec))
    open(os.path.join(OUT,"primitives-reference.md"),"w").write(render_md(prim))
    open(os.path.join(OUT,"spec-reference.html"),"w").write(
        render_html("DWARF Scenario DSL Reference (spec v1)","How to read and write a DWARF scenario — fields, lifecycle, FUZZ, and the CBOR shape grammar.","DWARF · DSL Reference",spec))
    open(os.path.join(OUT,"primitives-reference.html"),"w").write(
        render_html("DWARF Primitives Reference","Every strategy and every oracle a scenario can use — the complete 202-primitive catalogue.","DWARF · Primitives Reference",prim))
    print("wrote spec-reference.{md,html} and primitives-reference.{md,html} to", OUT)
