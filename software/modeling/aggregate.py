"""Verify and assemble independently curated batches into one immutable evaluation table."""
from __future__ import annotations

import argparse
import csv
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from software.analysis.pipeline import sha256, read_csv
from software.modeling.curate import FIELDS

VERSION = 'qy-curated-aggregate-v0.1'


def aggregate(curated_dirs, *, allow_simulation=False):
    if not curated_dirs:
        raise ValueError('no curated batch folders')
    all_rows, sources, provenances = [], set(), []
    seen_batches = set()
    for directory in curated_dirs:
        directory = Path(directory).resolve()
        if (directory / 'INCOMPLETE').exists():
            raise ValueError(f'incomplete curation: {directory}')
        meta_file, data_file = directory / 'curation_manifest.json', directory / 'curated.csv'
        metadata = json.loads(meta_file.read_text(encoding='utf-8'))
        if metadata.get('version') != 'qy-curation-v0.1' or sha256(data_file) != metadata.get('output_sha256'):
            raise ValueError(f'curated source/manifest mismatch: {directory}')
        batch = metadata.get('batch_id')
        source = metadata.get('source_mode')
        if not isinstance(batch, str) or not batch or batch in seen_batches:
            raise ValueError(f'duplicate/invalid batch_id: {batch}')
        seen_batches.add(batch)
        if source == 'physical':
            if not metadata.get('physical_verified_asserted') or not metadata.get('external_evidence_ref'):
                raise ValueError('missing physical evidence assertion')
        elif not (allow_simulation and source == 'simulation'):
            raise ValueError('simulation/unverified batch refused')
        data = read_csv(data_file)
        if not data or len(data) != metadata.get('rows'):
            raise ValueError(f'curated record count mismatch: {batch}')
        for row in data:
            if row.get('batch_id') != batch or row.get('source_mode') != source:
                raise ValueError(f'curated row provenance mismatch: {batch}')
            if set(row) != set(FIELDS):
                raise ValueError(f'curation schema changed: {batch}')
        all_rows.extend(data)
        sources.add(source)
        provenances.append({'batch_id': batch, 'curated_dir': str(directory),
                            'curation_manifest_sha256': sha256(meta_file),
                            'curated_csv_sha256': sha256(data_file),
                            'evidence_ref': metadata.get('external_evidence_ref')})
    if len(seen_batches) < 3:
        raise ValueError('at least 3 independently curated batches required')
    if len(sources) != 1:
        raise ValueError('do not mix physical and simulation in one evaluation')
    return all_rows, {'version': VERSION, 'batch_count': len(seen_batches), 'row_count': len(all_rows),
                      'source_mode': next(iter(sources)), 'inputs': provenances,
                      'generated_at_utc': datetime.now(timezone.utc).isoformat()}


def export(curated_dirs, output_dir, **kwargs):
    rows, manifest = aggregate(curated_dirs, **kwargs)
    output = Path(output_dir).resolve()
    if 'raw' in output.parts:
        raise ValueError('no derived output under raw')
    output.mkdir(parents=True, exist_ok=True)
    path = output / f'{VERSION}_{uuid.uuid4().hex[:12]}'
    path.mkdir()
    with (path / 'curated_all.csv').open('x', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader(); writer.writerows(rows)
    manifest['output_sha256'] = sha256(path / 'curated_all.csv')
    (path / 'aggregate_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('curated_dirs', nargs='+', type=Path)
    parser.add_argument('--output', default=Path('data/processed/aggregate'), type=Path)
    parser.add_argument('--allow-simulation', action='store_true')
    args = parser.parse_args()
    print(export(args.curated_dirs, args.output, allow_simulation=args.allow_simulation))


if __name__ == '__main__':
    main()
