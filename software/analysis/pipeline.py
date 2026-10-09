"""Read-only, conservative feature extraction from host_mvp raw batch archives.

No fill/interpolation, automatic calibration, actuator access or tea-state claims.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml

VERSION = 'qy-features-v0.1'
GASES = tuple(f'gas_{i}_v' for i in range(1, 5))
NUMERIC = GASES + ('bme688_gas_ohm', 'ambient_temp_c', 'ambient_rh_pct',
                   'chamber_temp_c', 'chamber_rh_pct', 'leaf_temp_c', 'mass_g')
FEATURE_FIELDS = ('batch_id', 'source_mode', 'window_start_s', 'window_end_s',
                  'n_rows', 'n_quality_ok', 'event_phase', 'last_shake_elapsed_s',
                  'relative_mass_loss_pct', 'mass_slope_g_per_min',
                  *[f'{name}_median' for name in NUMERIC],
                  *[f'{name}_slope_per_min' for name in NUMERIC],
                  *[f'{name}_available' for name in NUMERIC],
                  'warnings')


def finite(value):
    try:
        if value is None or isinstance(value, bool) or str(value).strip() == '':
            return None
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(part)
    return h.hexdigest()


def read_csv(path):
    if not path.is_file():
        return []
    with path.open(newline='', encoding='utf-8-sig') as stream:
        return list(csv.DictReader(stream))


def time_of(row, clock):
    if clock == 'host_monotonic_s':
        return finite(row.get(clock))
    try:
        from datetime import datetime
        d = datetime.fromisoformat(row.get('datetime_iso') or row.get('host_time_iso') or '')
        return d.timestamp() if d.tzinfo else None
    except (ValueError, OverflowError, OSError):
        return None


def phase_at(events, t):
    phase, last_shake = 'unknown', None
    for e in events:
        if e['_time'] > t:
            break
        kind = e.get('event_type')
        if kind == 'SHAKE_START':
            phase = 'shaking'
            last_shake = None
        elif kind in ('SHAKE_END', 'REST_START'):
            phase = 'resting'
            if kind == 'SHAKE_END':
                last_shake = e['_time']
        elif kind in ('KILL_GREEN_START', 'SESSION_END'):
            phase = 'ended'
    return phase, last_shake


def stats(points):
    if not points:
        return None, None
    med = statistics.median(v for _, v in points)
    if len(points) < 2:
        return med, None
    t0 = statistics.mean(t for t, _ in points)
    v0 = statistics.mean(v for _, v in points)
    den = sum((t-t0)**2 for t, _ in points)
    slope = 60 * sum((t-t0)*(v-v0) for t, v in points) / den if den else None
    return med, slope


def extract_batch(batch_dir: Path, window_s=60, step_s=30, min_count=5):
    """Yield window dictionaries and provenance; only read files under batch_dir."""
    root = Path(batch_dir).resolve()
    if not root.is_dir() or root.is_symlink():
        raise ValueError('batch directory unavailable')
    if window_s <= 0 or step_s <= 0 or min_count < 2:
        raise ValueError('invalid window/step/sample count')
    meta_path, sensor_path, event_path = [root / n for n in ('meta.yaml', 'sensor_1hz.csv', 'events.csv')]
    if not sensor_path.is_file() or not meta_path.is_file():
        raise ValueError('missing source sensor CSV or meta.yaml')
    metadata = yaml.safe_load(meta_path.read_text(encoding='utf-8-sig'))
    if not isinstance(metadata, dict):
        raise ValueError('meta.yaml must be mapping')
    raw = read_csv(sensor_path)
    if not raw:
        raise ValueError('empty sensor CSV')
    clock = ('host_monotonic_s' if any(finite(r.get('host_monotonic_s')) is not None for r in raw)
             else 'datetime_iso')
    rows = []
    for idx, r in enumerate(raw, 2):
        t = time_of(r, clock)
        if t is not None:
            rows.append(dict(r, _time=t, _record=idx))
    if not rows:
        raise ValueError('no valid timestamped sensor records')
    rows.sort(key=lambda r: (r['_time'], r['_record']))
    events = []
    for r in read_csv(event_path):
        t = time_of(r, clock)
        if t is not None:
            events.append(dict(r, _time=t))
    events.sort(key=lambda r: r['_time'])
    start = next((e['_time'] for e in events if e.get('event_type') == 'SESSION_START'), rows[0]['_time'])
    end = rows[-1]['_time']
    origin_mass = finite(metadata.get('initial_mass_g'))
    if origin_mass is not None and origin_mass <= 0:
        origin_mass = None
    source = str(metadata.get('source_mode') or 'unknown')
    if not metadata.get('source_mode'):
        source = 'unknown'
    incomplete = (root / 'INCOMPLETE').exists()
    outputs = []
    t_end = start + float(window_s)
    while t_end <= end + 1e-6:
        left = t_end - window_s
        subset = [r for r in rows if left <= r['_time'] < t_end]
        phase, last_shake = phase_at(events, t_end)
        # A phase boundary inside a window invalidates phase-specific features.
        crossed = any(left < e['_time'] <= t_end and e.get('event_type') in
                      ('SHAKE_START', 'SHAKE_END', 'REST_START', 'SESSION_END') for e in events)
        if crossed:
            phase = 'transition'
        good = [r for r in subset if (r.get('quality_flag') or '') == 'OK']
        warnings = []
        if len(good) < min_count:
            warnings.append('insufficient_clean_samples')
        if incomplete:
            warnings.append('incomplete_batch')
        if source not in ('physical', 'simulation'):
            warnings.append('source_unverified')
        if crossed:
            warnings.append('event_transition')
        obj = dict(batch_id=root.name, source_mode=source,
                   window_start_s=round(left-start, 6), window_end_s=round(t_end-start, 6),
                   n_rows=len(subset), n_quality_ok=len(good), event_phase=phase,
                   last_shake_elapsed_s=(round(t_end-last_shake, 6) if last_shake is not None and
                                         t_end >= last_shake else None),
                   relative_mass_loss_pct=None, mass_slope_g_per_min=None)
        for i, name in enumerate(NUMERIC):
            values = []
            for row in good:
                x = finite(row.get(name))
                if x is None:
                    continue
                if name in GASES:
                    mask = finite(row.get('gas_enabled_mask'))
                    if mask is None or not mask.is_integer() or not (int(mask) & (1 << i)):
                        continue
                    # Unproven gas-path state must not be treated as a valid sample.
                    pump, sampling, purge = [finite(row.get(n)) for n in
                                             ('pump_state', 'sample_valve_state', 'purge_valve_state')]
                    if (pump, sampling, purge) != (1, 1, 0):
                        continue
                if name == 'mass_g':
                    if phase != 'resting' or str(row.get('motor_running')).lower() not in ('false', '0'):
                        continue
                values.append((row['_time'], x))
            med, slope = stats(values) if len(good) >= min_count and len(values) >= min_count else (None, None)
            obj[f'{name}_median'] = med
            obj[f'{name}_slope_per_min'] = slope
            obj[f'{name}_available'] = med is not None
        obj['mass_slope_g_per_min'] = obj['mass_g_slope_per_min']
        if origin_mass and obj['mass_g_median'] is not None:
            loss = 100 * (origin_mass - obj['mass_g_median']) / origin_mass
            if 0 <= loss <= 100:
                obj['relative_mass_loss_pct'] = loss
            else:
                warnings.append('mass_baseline_inconsistent')
        elif phase == 'resting':
            warnings.append('missing_mass_baseline_or_reading')
        obj['warnings'] = ';'.join(warnings)
        outputs.append(obj)
        t_end += step_s
    manifest = {'version': VERSION, 'source_batch': root.name, 'source_mode': source,
                'clock': clock, 'time_origin': start, 'window_s': window_s, 'step_s': step_s,
                'min_count': min_count, 'initial_mass_source': ('meta.initial_mass_g' if origin_mass else None),
                'source_sha256': {n: sha256(root / n) if (root / n).is_file() else None
                                  for n in ('meta.yaml', 'sensor_1hz.csv', 'events.csv', 'master_labels.csv')},
                'rows': len(outputs), 'incomplete_source': incomplete,
                'semantics': 'Missing/null is not imputed; VOC requires sample air-path; mass only in known rest windows.'}
    return outputs, manifest


def export(batch_dir, output_root, **kwargs):
    features, manifest = extract_batch(Path(batch_dir), **kwargs)
    out_root = Path(output_root).resolve()
    if out_root == Path(batch_dir).resolve() or out_root.is_relative_to(Path(batch_dir).resolve()):
        raise ValueError('cannot write into raw batch')
    out_root.mkdir(parents=True, exist_ok=True)
    out = out_root / f'{VERSION}_{Path(batch_dir).name}_{uuid.uuid4().hex[:12]}'
    out.mkdir()
    (out / 'INCOMPLETE').write_text('pending write\n', encoding='utf-8')
    with (out / 'features.csv').open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=FEATURE_FIELDS)
        writer.writeheader(); writer.writerows(features)
    manifest['output_sha256'] = sha256(out / 'features.csv')
    manifest['generated_at_utc'] = datetime.now(timezone.utc).isoformat()
    (out / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    (out / 'INCOMPLETE').unlink()
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('batch', type=Path)
    p.add_argument('--output', type=Path, default=Path('data/processed'))
    p.add_argument('--window', type=float, default=60)
    p.add_argument('--step', type=float, default=30)
    p.add_argument('--min-count', type=int, default=5)
    a = p.parse_args()
    print(export(a.batch, a.output, window_s=a.window, step_s=a.step, min_count=a.min_count))


if __name__ == '__main__':
    main()
