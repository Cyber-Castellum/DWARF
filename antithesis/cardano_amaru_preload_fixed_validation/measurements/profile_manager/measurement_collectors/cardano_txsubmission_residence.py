"""Strict joins for the UNBUILT inbound V2 request/body handling contract.

The native body-state update is the terminal boundary. It is neither mempool
submission nor acceptance. Clocks bracket calls outside STM; retain bounds only.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any, Mapping

from profile_manager.measurement_report import distribution_summary

MEASUREMENT_ID = 'cardano-patched-txsubmission-residence'
BOUNDARY = 'inbound-v2-request-body-handling'
KIND = 'txsubmission-residence'
BASE = {'schema_version': 'v1', 'target': 'cardano-node::measurement',
        'event': 'txsubmission_residence', 'boundary': BOUNDARY}
PHASE_OUTCOMES = {'request': {'pending'}, 'body': {'body-returned', 'body-omitted'},
                  'terminal': {'body-handled', 'body-omitted'}}


def normalize(record: Mapping[str, Any]) -> dict[str, Any]:
    if any(record.get(key) != value for key, value in BASE.items()):
        raise ValueError('invalid-txsubmission-boundary')
    if record == {**BASE, 'phase': 'unavailable'}:
        return {'kind': KIND, **record}
    expected = set(BASE) | {'phase', 'trace_session', 'peer_id', 'peer_session', 'request_id',
        'tx_id', 'tx_index', 'request_tx_count', 'outcome', 'pending_depth', 'inflight_depth',
        'started_monotonic_ns', 'ended_monotonic_ns'}
    if set(record) != expected:
        raise ValueError('invalid-txsubmission-fields')
    phase, outcome = record['phase'], record['outcome']
    if (not isinstance(phase, str) or phase not in PHASE_OUTCOMES
            or not isinstance(outcome, str) or outcome not in PHASE_OUTCOMES[phase]):
        raise ValueError('invalid-txsubmission-phase-outcome')
    if not isinstance(record['trace_session'], str) or not re.fullmatch(r'[0-9a-f]{32}', record['trace_session']):
        raise ValueError('invalid-txsubmission-session')
    for key in ('peer_session', 'request_id'):
        if type(record[key]) is not int or not 0 < record[key] < 2**64:
            raise ValueError('invalid-txsubmission-identity')
    for key in ('peer_id', 'tx_id'):
        value = record[key]
        if not isinstance(value, str) or not 1 <= len(value) <= 256 or any(ord(c) < 32 or ord(c) > 126 for c in value):
            raise ValueError('invalid-txsubmission-public-identity')
    count, index = record['request_tx_count'], record['tx_index']
    if type(count) is not int or type(index) is not int or not 1 <= index <= count <= 65536:
        raise ValueError('invalid-txsubmission-cardinality')
    for key in ('pending_depth', 'inflight_depth'):
        if type(record[key]) is not int or not 0 <= record[key] <= 65536:
            raise ValueError('invalid-txsubmission-depth')
    start, end = record['started_monotonic_ns'], record['ended_monotonic_ns']
    if type(start) is not int or type(end) is not int or not 0 <= start <= end < 2**64:
        raise ValueError('invalid-txsubmission-clock')
    return {'kind': KIND, **record}


def summarize(events: list[dict[str, Any]], *, incomplete: bool) -> tuple[dict, dict, dict]:
    groups: dict[tuple, list] = defaultdict(list)
    seen: dict[tuple, dict] = {}
    poisoned: set[tuple] = set()
    peer_names: dict[tuple, str] = {}
    bad_peers: set[tuple] = set()
    duplicates = conflicts = unavailable = 0
    for event in events:
        if event.get('kind') != KIND:
            continue
        if event['phase'] == 'unavailable':
            unavailable += 1
            continue
        peer = event['trace_session'], event['peer_session']
        if peer in peer_names and peer_names[peer] != event['peer_id']:
            bad_peers.add(peer)
        peer_names[peer] = event['peer_id']
        batch = (*peer, event['peer_id'], event['request_id'])
        # Index is emitted from the same native ordered Map in all phases.
        key = (*batch, event['tx_index'], event['phase'])
        if key in seen:
            if seen[key] == event:
                duplicates += 1
            else:
                conflicts += 1
                poisoned.add(batch)
            continue
        seen[key] = event
        groups[batch].append(event)
    names = [f'{boundary}_{bound}_bound' for boundary in
             ('request_to_body', 'body_to_terminal', 'request_to_terminal') for bound in ('lower', 'upper')]
    samples = {name: [] for name in [*names, 'pending_body_depth', 'inflight_body_depth']}
    transactions = []
    terminal_outcomes: Counter[str] = Counter()
    invalid = coherent_batches = 0
    for batch, rows in sorted(groups.items()):
        phases = {phase: sorted((e for e in rows if e['phase'] == phase), key=lambda e: e['tx_index'])
                  for phase in PHASE_OUTCOMES}
        count = rows[0]['request_tx_count']
        # Check count against retained rows before allocating any index range.
        valid = (batch not in poisoned and batch[:2] not in bad_peers
                 and all(len(phase_rows) == count for phase_rows in phases.values())
                 and all(e['request_tx_count'] == count for e in rows))
        if valid:
            for phase_rows in phases.values():
                first = phase_rows[0]
                valid &= (len({e['tx_id'] for e in phase_rows}) == count
                    and all(e['tx_index'] == i for i, e in enumerate(phase_rows, 1))
                    and all(all(e[field] == first[field] for field in (
                        'pending_depth', 'inflight_depth', 'started_monotonic_ns', 'ended_monotonic_ns')) for e in phase_rows))
            valid &= (phases['request'][0]['inflight_depth'] >= count
                      and phases['body'][0]['inflight_depth'] >= count
                      and phases['terminal'][0]['inflight_depth'] == phases['body'][0]['inflight_depth'] - count)
            for request, body, terminal in zip(phases['request'], phases['body'], phases['terminal']):
                valid &= (request['tx_id'] == body['tx_id'] == terminal['tx_id']
                    and request['ended_monotonic_ns'] <= body['started_monotonic_ns']
                    and body['ended_monotonic_ns'] <= terminal['started_monotonic_ns']
                    and (body['outcome'], terminal['outcome']) in {
                        ('body-returned', 'body-handled'), ('body-omitted', 'body-omitted')})
        if not valid:
            invalid += 1
            continue
        coherent_batches += 1
        for request, body, terminal in zip(phases['request'], phases['body'], phases['terminal']):
            entry = {key: request[key] for key in ('trace_session', 'peer_session', 'peer_id',
                     'request_id', 'tx_index', 'request_tx_count', 'tx_id')}
            entry['outcome'] = terminal['outcome']
            terminal_outcomes[terminal['outcome']] += 1
            for phase, row in [('request', request), ('body', body), ('terminal', terminal)]:
                entry[phase + '_bracket_ns'] = [row['started_monotonic_ns'], row['ended_monotonic_ns']]
            for boundary, first, last in [('request_to_body', request, body),
                    ('body_to_terminal', body, terminal), ('request_to_terminal', request, terminal)]:
                lower = max(0, last['started_monotonic_ns'] - first['ended_monotonic_ns'])
                upper = last['ended_monotonic_ns'] - first['started_monotonic_ns']
                for bound, value in [('lower', lower), ('upper', upper)]:
                    name = f'{boundary}_{bound}_bound'
                    entry[name + '_ns'] = value
                    samples[name].append({'value': value / 1000})
            transactions.append(entry)
        # Depth is a per-phase native peer-state snapshot, once per batch.
        for phase_rows in phases.values():
            samples['pending_body_depth'].append({'value': phase_rows[0]['pending_depth']})
            samples['inflight_body_depth'].append({'value': phase_rows[0]['inflight_depth']})
    metrics = {name: distribution_summary(rows, unit='transactions' if name.endswith('_depth') else 'microseconds')
               for name, rows in samples.items()}
    observed = bool(transactions)
    exercise = {'status': 'pending-control-evidence' if observed and not (invalid or conflicts or unavailable or incomplete) else 'incomplete',
        'measurement_complete': False, 'required_boundaries_observed': observed,
        'coherent_request_count': coherent_batches, 'coherent_transaction_count': len(transactions),
        'incomplete_request_count': invalid, 'duplicate_event_count': duplicates,
        'conflicting_event_count': conflicts, 'unavailable_event_count': unavailable,
        'terminal_outcomes': dict(sorted(terminal_outcomes.items())),
        'normal_control': 'requires-retained-same-boundary-control',
        'campaign_adapter': 'implemented-joined-projection',
        'reason': 'UNBUILT request/body handling bounds require exact compile/probe, frozen node/scenario identity, non-vacuous N2N workload, same-boundary control, health and paired calibration. Terminal handling is not mempool submission, acceptance, adoption or a security verdict.'}
    return metrics, exercise, {'schema_version': 'v1', 'boundary': BOUNDARY, 'transactions': transactions}
