"""Strict joins for the unbuilt Cardano merged-request queue contract.

Clocks bracket calls outside STM. Residence is an interval, never an exact
measurement. A successful addFetchedBlock service sample does not imply adoption.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Mapping

from profile_manager.measurement_report import distribution_summary

MEASUREMENT_ID = 'cardano-patched-blockfetch-handler-queue'
BOUNDARY = 'client-merged-request'
BASE = {'schema_version': 'v1', 'target': 'cardano-node::measurement',
        'event': 'blockfetch_handler_queue', 'boundary': BOUNDARY}


def normalize(record: Mapping[str, Any]) -> dict[str, Any]:
    if any(record.get(k) != v for k, v in BASE.items()):
        raise ValueError('invalid-blockfetch-queue-boundary')
    if record == {**BASE, 'phase': 'unavailable'}:
        return {'kind': 'blockfetch-handler-queue', **record}
    expected = set(BASE) | {'phase', 'trace_session', 'client_id', 'request_id', 'update_index',
                            'queue_depth', 'started_monotonic_ns', 'ended_monotonic_ns', 'block_hash'}
    if set(record) != expected:
        raise ValueError('invalid-blockfetch-queue-fields')
    phase = record.get('phase')
    if not isinstance(phase, str) or phase not in {'enqueue', 'dequeue', 'service'}:
        raise ValueError('invalid-blockfetch-queue-phase')
    session = record['trace_session']
    if not isinstance(session, str) or not re.fullmatch(r'[0-9a-f]{32}', session):
        raise ValueError('invalid-blockfetch-queue-session')
    for field in ('client_id', 'request_id', 'update_index'):
        value = record[field]
        if type(value) is not int or not 0 < value < 2**64:
            raise ValueError('invalid-blockfetch-queue-identity')
    depth = record['queue_depth']
    if (type(depth) is not int or not 0 <= depth <= 65536
            or (phase == 'enqueue' and depth == 0) or (phase != 'enqueue' and depth != 0)):
        raise ValueError('invalid-blockfetch-queue-depth')
    start, end = record['started_monotonic_ns'], record['ended_monotonic_ns']
    if type(start) is not int or type(end) is not int or not 0 <= start <= end < 2**64:
        raise ValueError('invalid-blockfetch-queue-timing')
    block_hash = record['block_hash']
    if (not isinstance(block_hash, str) or (phase != 'service' and block_hash)
            or (phase == 'service' and not re.fullmatch(r'[0-9a-f]{64}', block_hash))):
        raise ValueError('invalid-blockfetch-queue-block-hash')
    return {'kind': 'blockfetch-handler-queue', **record}


def summarize(events: list[dict[str, Any]], *, incomplete: bool) -> tuple[dict, dict, dict]:
    groups: dict[tuple, list] = defaultdict(list)
    seen: dict[tuple, dict] = {}
    poisoned: set[tuple] = set()
    duplicates = conflicts = unavailable = 0
    for event in events:
        if event.get('kind') != 'blockfetch-handler-queue':
            continue
        if event['phase'] == 'unavailable':
            unavailable += 1
            continue
        request = event['trace_session'], event['client_id'], event['request_id']
        key = (*request, event['phase'], event['update_index'], event['block_hash'])
        if key in seen:
            if seen[key] == event:
                duplicates += 1
            else:
                conflicts += 1
                poisoned.add(request)
            continue
        seen[key] = event
        groups[request].append(event)
    samples: dict[str, list] = {key: [] for key in (
        'queue_residence_lower_bound', 'queue_residence_upper_bound', 'handler_service', 'pending_range_depth')}
    requests = []
    invalid = merged = 0
    for key, rows in sorted(groups.items()):
        enqueue = sorted((e for e in rows if e['phase'] == 'enqueue'), key=lambda e: e['update_index'])
        dequeue = [e for e in rows if e['phase'] == 'dequeue']
        service = sorted((e for e in rows if e['phase'] == 'service'), key=lambda e: e['started_monotonic_ns'])
        valid = bool(key not in poisoned and enqueue and len(dequeue) == 1 and service)
        if valid:
            ack = dequeue[0]
            # All writes contributing to this merged request must be retained.
            # Bound the count against retained rows before constructing a range.
            count = ack['update_index']
            valid = (count == len(enqueue)
                     and [e['update_index'] for e in enqueue] == list(range(1, len(enqueue)+1))
                     and all(e['update_index'] == count for e in service)
                     and all(e['started_monotonic_ns'] <= ack['ended_monotonic_ns'] for e in enqueue)
                     and all(b['started_monotonic_ns'] >= a['ended_monotonic_ns']
                             for a, b in zip(enqueue, enqueue[1:]))
                     and all(e['started_monotonic_ns'] >= ack['ended_monotonic_ns'] for e in service)
                     and all(b['started_monotonic_ns'] >= a['ended_monotonic_ns']
                             for a, b in zip(service, service[1:])))
        if not valid:
            invalid += 1
            continue
        first = enqueue[0]
        lower = max(0, ack['started_monotonic_ns'] - first['ended_monotonic_ns'])
        upper = ack['ended_monotonic_ns'] - first['started_monotonic_ns']
        # A negative/contradictory interval cannot become a numeric sample.
        if lower > upper:
            invalid += 1
            continue
        merged += len(enqueue) > 1
        samples['queue_residence_lower_bound'].append({'value': lower / 1000})
        samples['queue_residence_upper_bound'].append({'value': upper / 1000})
        samples['handler_service'].extend({'value': (e['ended_monotonic_ns']-e['started_monotonic_ns']) / 1000} for e in service)
        samples['pending_range_depth'].extend({'value': e['queue_depth']} for e in [*enqueue, ack])
        requests.append({'trace_session': key[0], 'client_id': key[1], 'request_id': key[2],
            'enqueue_brackets_ns': [[e['started_monotonic_ns'],e['ended_monotonic_ns']] for e in enqueue],
            'dequeue_bracket_ns': [ack['started_monotonic_ns'],ack['ended_monotonic_ns']],
            'residence_lower_bound_ns': lower, 'residence_upper_bound_ns': upper,
            'block_hashes': [e['block_hash'] for e in service],
            'service_brackets_ns': [[e['started_monotonic_ns'],e['ended_monotonic_ns']] for e in service]})
    metrics = {key: distribution_summary(rows, unit='ranges' if key == 'pending_range_depth' else 'microseconds')
               for key, rows in samples.items()}
    observed = bool(requests)
    exercise = {'status': 'pending-control-evidence' if observed and not (invalid or unavailable or incomplete or conflicts) else 'incomplete',
        'measurement_complete': False, 'required_boundaries_observed': observed,
        'coherent_request_count': len(requests), 'incomplete_request_count': invalid,
        'merged_request_count': merged, 'duplicate_event_count': duplicates,
        'conflicting_event_count': conflicts, 'unavailable_event_count': unavailable,
        'normal_control': 'requires-retained-same-boundary-control',
        'reason': 'Queue residence bounds and native addFetchedBlock service require frozen node/scenario/process identity, same-boundary normal control, non-vacuous workload, health evidence and paired calibration. Trace samples cannot establish measurement completeness.'}
    return metrics, exercise, {'schema_version': 'v1', 'boundary': BOUNDARY, 'requests': requests}
