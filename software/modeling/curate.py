"""Manually authorized, timestamp-bounded join of raw expert labels to computed windows.

This script does not itself verify physical truth or expert identity; evidence_ref
is an external audit reference that must be reviewed before model reporting.
"""
from __future__ import annotations

import argparse
import csv
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from software.analysis.pipeline import read_csv, time_of, sha256
from software.modeling.evaluate import FEATURES, STAGES

VERSION = 'qy-curation-v0.1'
FIELDS = ('batch_id', 'source_mode', 'stage_label', *FEATURES, 'label_event_id',
          'label_id', 'label_revision', 'label_operator', 'label_lag_s', 'feature_window_end_s')


def curate(batch_dir, feature_dir, *, reviewer, evidence_ref='', physical_verified=False,
           allow_simulation=False, max_label_lag_s=120):
    root, processed = Path(batch_dir).resolve(), Path(feature_dir).resolve()
    if not reviewer.strip() or max_label_lag_s <= 0:
        raise ValueError('reviewer and positive lag required')
    if (root / 'INCOMPLETE').exists() or (processed / 'INCOMPLETE').exists():
        raise ValueError('incomplete source rejected')
    manifest = json.loads((processed / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('source_batch') != root.name:
        raise ValueError('processed batch does not match original')
    for name, digest in manifest.get('source_sha256', {}).items():
        file = root / name
        actual = sha256(file) if file.is_file() else None
        if actual != digest:
            raise ValueError(f'raw source changed since processing: {name}')
    if sha256(processed / 'features.csv') != manifest.get('output_sha256'):
        raise ValueError('processed features altered since export')
    raw_source = manifest.get('source_mode')
    if physical_verified and evidence_ref.strip():
        source = 'physical'
    elif allow_simulation and raw_source in ('simulate', 'simulation'):
        source = 'simulation'
    else:
        raise ValueError('external physical evidence/reviewer needed; simulation requires explicit flag')
    clock = manifest.get('clock')
    if clock not in ('host_monotonic_s', 'datetime_iso'):
        raise ValueError('unknown input clock')
    origin = manifest.get('time_origin')
    if not isinstance(origin, (int, float)):
        raise ValueError('missing input time origin')
    events = {}
    for row in read_csv(root / 'events.csv'):
        timestamp = time_of(row, clock)
        event_id = str(row.get('event_id') or '')
        if event_id and row.get('event_type') == 'MASTER_CHECK' and timestamp is not None:
            events[event_id] = timestamp
    if not events:
        raise ValueError('no timestamped MASTER_CHECK events')
    latest = {}
    for ordinal, label in enumerate(read_csv(root / 'master_labels.csv')):
        event_id = str(label.get('event_id') or '')
        if event_id not in events or label.get('stage_label') not in STAGES or not label.get('operator'):
            continue
        try:
            revision = int(label.get('revision') or 1)
        except (ValueError, TypeError):
            continue
        if revision < 1:
            continue
        key = (revision, ordinal)
        if event_id not in latest or key > latest[event_id][0]:
            latest[event_id] = (key, label)
    features = read_csv(processed / 'features.csv')
    rows = []
    for event_id, (_, label) in sorted(latest.items(), key=lambda item: events[item[0]]):
        mark = events[event_id] - origin
        eligible = []
        for f in features:
            try:
                end = float(f['window_end_s'])
            except (ValueError, KeyError):
                continue
            lag = mark - end
            # Do not use features computed after the expert observation.
            if not 0 <= lag <= max_label_lag_s:
                continue
            flags = set(filter(None, (f.get('warnings') or '').split(';')))
            if flags - {'source_unverified'}:
                continue
            if not any(f.get(field) not in (None, '') for field in FEATURES):
                continue
            eligible.append((end, lag, f))
        if not eligible:
            continue
        end, lag, f = max(eligible, key=lambda item: item[0])
        rows.append({'batch_id': root.name, 'source_mode': source, 'stage_label': label['stage_label'],
                     **{name: f.get(name, '') for name in FEATURES},
                     'label_event_id': event_id, 'label_id': label.get('label_id', ''),
                     'label_revision': label.get('revision', '1'), 'label_operator': label['operator'],
                     'label_lag_s': round(lag, 6), 'feature_window_end_s': end})
    if not rows:
        raise ValueError('no quality-passing labeled windows with causal alignment')
    proof = {'version': VERSION, 'batch_id': root.name, 'source_mode': source,
             'original_source_mode': raw_source, 'physical_verified_asserted': physical_verified,
             'reviewer': reviewer, 'external_evidence_ref': evidence_ref,
             'max_label_lag_s': max_label_lag_s, 'rows': len(rows),
             'source_manifest_sha256': sha256(processed / 'manifest.json'),
             'created_at_utc': datetime.now(timezone.utc).isoformat(),
             'caveat': 'manual evidence assertion only; no automatic physical or expert identity verification'}
    return rows, proof


def export(batch_dir, feature_dir, output_dir, **kwargs):
    rows, proof = curate(batch_dir, feature_dir, **kwargs)
    target = Path(output_dir).resolve()
    raw = Path(batch_dir).resolve()
    if target == raw or target.is_relative_to(raw) or 'raw' in target.parts:
        raise ValueError('cannot write into raw data')
    target.mkdir(parents=True, exist_ok=True)
    dest = target / f'{VERSION}_{raw.name}_{uuid.uuid4().hex[:12]}'
    dest.mkdir()
    with (dest / 'curated.csv').open('x', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader(); writer.writerows(rows)
    proof['output_sha256'] = sha256(dest / 'curated.csv')
    (dest / 'curation_manifest.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding='utf-8')
    return dest


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('raw_batch', type=Path)
    p.add_argument('feature_dir', type=Path)
    p.add_argument('--output', type=Path, default=Path('data/processed/curated'))
    p.add_argument('--reviewer', required=True)
    p.add_argument('--evidence-ref', default='')
    p.add_argument('--physical-verified', action='store_true')
    p.add_argument('--allow-simulation', action='store_true')
    p.add_argument('--max-lag', type=float, default=120)
    args = p.parse_args()
    print(export(args.raw_batch, args.feature_dir, args.output, reviewer=args.reviewer,
                 evidence_ref=args.evidence_ref, physical_verified=args.physical_verified,
                 allow_simulation=args.allow_simulation, max_label_lag_s=args.max_lag))


if __name__ == '__main__':
    main()
