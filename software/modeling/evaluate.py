"""Strict grouped offline stage baseline evaluation on an explicitly labeled feature CSV.

A model is NOT deployed or saved. Samples sharing a batch never cross folds.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from pathlib import Path

FEATURES = ('gas_1_v_median', 'gas_2_v_median', 'gas_3_v_median',
            'gas_4_v_median', 'ambient_temp_c_median', 'ambient_rh_pct_median',
            'relative_mass_loss_pct', 'last_shake_elapsed_s')
STAGES = {'initial', 'shaking', 'resting', 'ready_for_fixation'}


def load_curated_table(path, *, allow_simulation=False):
    """Require an explicit, reviewed stage_label column; never infer labels from events."""
    with Path(path).open(encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise ValueError('empty curated table')
    prepared = []
    for i, r in enumerate(rows, 2):
        batch, stage, source = r.get('batch_id'), r.get('stage_label'), r.get('source_mode')
        if not batch or stage not in STAGES:
            raise ValueError(f'row {i}: missing batch or unreviewed/unknown stage')
        if source != 'physical' and not (allow_simulation and source == 'simulation'):
            raise ValueError(f'row {i}: unverified/simulation source; default requires physical')
        vector = []
        for field in FEATURES:
            value = r.get(field)
            try:
                x = float(value) if value not in (None, '') else math.nan
                vector.append(x if math.isfinite(x) else math.nan)
            except (ValueError, TypeError):
                vector.append(math.nan)
        if all(math.isnan(x) for x in vector):
            raise ValueError(f'row {i}: no usable modality')
        prepared.append((batch, stage, vector))
    if len(set(b for b, _, _ in prepared)) < 3:
        raise ValueError('>=3 independent batch_ids required for held-out evaluation')
    return prepared


def evaluate(rows):
    """Leave-one-batch-out; preprocessing and feature imputation trained in each fold."""
    try:
        import numpy as np
        from sklearn.dummy import DummyClassifier
        from sklearn.impute import SimpleImputer
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import accuracy_score, f1_score, confusion_matrix
        from sklearn.pipeline import make_pipeline
    except ImportError as e:
        raise RuntimeError('install software/modeling/requirements.txt') from e
    groups = sorted(set(b for b, _, _ in rows))
    names = ('majority', 'logistic_all', 'logistic_time_only')
    predictions = {name: [] for name in names}
    skipped = []
    for batch in groups:
        train = [(y, x) for b, y, x in rows if b != batch]
        test = [(y, x) for b, y, x in rows if b == batch]
        classes = sorted(set(y for y, _ in train))
        # Do not silently score a model trained on one single label.
        if len(classes) < 2:
            skipped.append({'batch_id': batch, 'reason': 'train_fold_single_class'})
            continue
        xtrain = np.asarray([x for _, x in train], dtype=float)
        xtest = np.asarray([x for _, x in test], dtype=float)
        ytrain = [y for y, _ in train]
        ytest = [y for y, _ in test]
        for name in names:
            if name == 'majority':
                model = DummyClassifier(strategy='most_frequent')
                tr, te = xtrain, xtest
            else:
                cols = [7] if name == 'logistic_time_only' else list(range(len(FEATURES)))
                tr, te = xtrain[:, cols], xtest[:, cols]
                if np.all(np.isnan(tr)):
                    skipped.append({'batch_id': batch, 'model': name, 'reason': 'train_fold_no_valid_features'})
                    continue
                # Empty columns are retained (sklearn >=1.2) for consistent shapes.
                model = make_pipeline(SimpleImputer(strategy='median', keep_empty_features=True,
                                                    add_indicator=True),
                                      LogisticRegression(max_iter=1000, class_weight='balanced'))
            model.fit(tr, ytrain)
            yp = list(model.predict(te))
            predictions[name].extend({'batch_id': batch, 'truth': y, 'prediction': p}
                                     for y, p in zip(ytest, yp))
    if not predictions['majority']:
        raise ValueError('no evaluable held-out folds: more labeled independent batches needed')
    results = {}
    for name, records in predictions.items():
        if not records:
            results[name] = {'status': 'not_evaluable'}
            continue
        labels = sorted(STAGES)
        yt = [r['truth'] for r in records]
        yp = [r['prediction'] for r in records]
        results[name] = {'n_windows': len(records), 'n_test_batches': len(set(r['batch_id'] for r in records)),
                         'macro_f1': float(f1_score(yt, yp, labels=labels, average='macro', zero_division=0)),
                         'accuracy': float(accuracy_score(yt, yp)),
                         'labels': labels, 'confusion': confusion_matrix(yt, yp, labels=labels).tolist(),
                         'predictions': records}
    return {'evaluation_version': 'qy-grouped-baseline-v0.1', 'protocol': 'leave-one-batch-out',
            'features': list(FEATURES), 'groups': groups, 'class_counts': dict(Counter(y for _, y, _ in rows)),
            'models': results, 'skipped': skipped,
            'limitations': 'No deployment, no probability calibration, no tea-quality claim; curated labels required.'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('curated_csv', type=Path)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--allow-simulation', action='store_true')
    args = p.parse_args()
    out = args.output.resolve()
    if out == args.curated_csv.resolve():
        raise ValueError('will not overwrite curated source')
    if out.exists():
        raise FileExistsError('evaluation report already exists')
    data = evaluate(load_curated_table(args.curated_csv, allow_simulation=args.allow_simulation))
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open('x', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(out)


if __name__ == '__main__':
    main()
