import csv
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from software.analysis.pipeline import extract_batch, export
from software.decision.shadow import ExpertLimits, suggest, append_review
from software.modeling.evaluate import load_curated_table, evaluate, FEATURES


class FeatureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.batch = self.base / 'raw' / 'BATCH_A'
        self.batch.mkdir(parents=True)
        (self.batch / 'meta.yaml').write_text(json.dumps({'source_mode': 'serial_unverified', 'initial_mass_g': 1000}))
        with (self.batch / 'events.csv').open('w', newline='') as f:
            w = csv.DictWriter(f, ['event_type', 'host_monotonic_s'])
            w.writeheader()
            w.writerows([{'event_type': 'SESSION_START', 'host_monotonic_s': '100'},
                         {'event_type': 'REST_START', 'host_monotonic_s': '100'}])
        with (self.batch / 'sensor_1hz.csv').open('w', newline='') as f:
            cols = ['host_monotonic_s', 'quality_flag', 'gas_enabled_mask', 'gas_1_v', 'mass_g',
                    'pump_state', 'sample_valve_state', 'purge_valve_state', 'motor_running']
            w = csv.DictWriter(f, cols)
            w.writeheader()
            for i in range(0, 123):
                w.writerow({'host_monotonic_s': 100+i, 'quality_flag': 'SENSOR_ERROR' if i == 30 else 'OK',
                            'gas_enabled_mask': 1, 'gas_1_v': 1 + i / 100,
                            'mass_g': 1000 - i, 'pump_state': 1 if i >= 60 else 0,
                            'sample_valve_state': 1 if i >= 60 else 0,
                            'purge_valve_state': 0, 'motor_running': 'false'})

    def test_mass_only_rest_and_gas_only_sampling(self):
        rows, manifest = extract_batch(self.batch, window_s=60, step_s=60)
        self.assertEqual(len(rows), 2)
        self.assertIsNone(rows[0]['gas_1_v_median'])
        self.assertGreater(rows[1]['gas_1_v_median'], 1)
        self.assertIsNotNone(rows[1]['relative_mass_loss_pct'])
        self.assertEqual(manifest['source_mode'], 'serial_unverified')
        self.assertIn('source_unverified', rows[1]['warnings'])

    def test_raw_unchanged_by_export(self):
        original = (self.batch / 'sensor_1hz.csv').read_bytes()
        out = export(self.batch, self.base / 'processed', window_s=60)
        self.assertEqual(original, (self.batch / 'sensor_1hz.csv').read_bytes())
        self.assertTrue((out / 'manifest.json').exists())
        self.assertFalse((out / 'INCOMPLETE').exists())
        with self.assertRaises(ValueError):
            export(self.batch, self.batch / 'processed')

    def test_motion_and_missing_reference_excluded(self):
        p = self.batch / 'meta.yaml'
        p.write_text(json.dumps({'source_mode': 'simulate'}))
        data = (self.batch / 'sensor_1hz.csv').read_text().replace(',false\n', ',true\n')
        (self.batch / 'sensor_1hz.csv').write_text(data)
        rows, _ = extract_batch(self.batch, window_s=60)
        self.assertTrue(all(x['mass_g_median'] is None for x in rows))
        self.assertTrue(all(x['relative_mass_loss_pct'] is None for x in rows))


class DecisionTests(unittest.TestCase):
    def ready(self):
        return dict(batch_id='B1', observed_at_iso='2026-10-09T01:00:00+00:00',
                    source_mode='physical', quality_flag='OK', model_validated=True,
                    calibration_verified=True, tea_type='Dancong', confidence=.9,
                    motor_running=False, motor_fault=False, hardware_interlock_verified=True,
                    last_shake_elapsed_s=1500, stage='ready_for_shake')

    def test_default_fails_closed(self):
        r = suggest(self.ready(), now=datetime(2026, 10, 9, 1, 0, tzinfo=timezone.utc))
        self.assertEqual(r['action'], 'REVIEW_REQUIRED')
        self.assertFalse(r['dispatched'])
        self.assertIsNone(r['actuator_command'])

    def test_verified_preview_never_dispatches(self):
        p = ExpertLimits(expert_approved=True, supported_tea='Dancong')
        t = datetime(2026, 10, 9, 1, 0, 2, tzinfo=timezone.utc)
        r = suggest(self.ready(), p, now=t)
        self.assertEqual(r['action'], 'REVIEW_SHAKE_CANDIDATE')
        self.assertFalse(r['dispatched'])
        self.assertIsNone(r['actuator_command'])
        with tempfile.TemporaryDirectory() as d:
            entry = append_review(Path(d) / 'audit.jsonl', r, 'inspector', 'accept_for_review')
            self.assertFalse(entry['dispatched'])

    def test_stale_fault_confidence(self):
        p = ExpertLimits(expert_approved=True, supported_tea='Dancong')
        o = self.ready(); o.update(motor_fault=True, confidence=0.1)
        r = suggest(o, p, now=datetime(2026, 10, 9, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(r['action'], 'REVIEW_REQUIRED')
        self.assertIn('stale_or_invalid_time', r['reasons'])
        self.assertIn('low_or_invalid_confidence', r['reasons'])


class ModelingTests(unittest.TestCase):
    def test_reject_unverified_or_insufficient_groups(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'curated.csv'
            cols = ['batch_id', 'stage_label', 'source_mode', *FEATURES]
            with p.open('w', newline='') as f:
                w = csv.DictWriter(f, cols); w.writeheader()
                w.writerow({'batch_id': 'B1', 'stage_label': 'resting', 'source_mode': 'serial_unverified',
                            'gas_1_v_median': 1})
            with self.assertRaises(ValueError):
                load_curated_table(p)

    def test_grouped_folds_with_synthetic_labels(self):
        rows = []
        for i in range(4):
            for stage in ('resting', 'ready_for_fixation'):
                x = [0.2 + i / 10] * 8 if stage == 'resting' else [1.0 + i / 10] * 8
                rows.append((f'B{i}', stage, x))
        out = evaluate(rows)
        self.assertEqual(out['protocol'], 'leave-one-batch-out')
        self.assertEqual(out['models']['majority']['n_test_batches'], 4)
        self.assertEqual(out['models']['logistic_all']['n_test_batches'], 4)


if __name__ == '__main__':
    unittest.main()
