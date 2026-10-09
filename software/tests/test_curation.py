import csv
import json
import tempfile
import unittest
from pathlib import Path

from software.analysis.pipeline import export as export_features
from software.modeling.curate import curate, export


class CurateTests(unittest.TestCase):
    def test_raw_hash_event_time_manual_gate(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'raw' / 'B1'; root.mkdir(parents=True)
            (root / 'meta.yaml').write_text(json.dumps({'source_mode': 'serial_unverified', 'initial_mass_g': 1000}))
            with (root / 'sensor_1hz.csv').open('w', newline='') as f:
                w = csv.DictWriter(f, ['host_monotonic_s','quality_flag','gas_1_v','gas_enabled_mask',
                                       'pump_state','sample_valve_state','purge_valve_state','motor_running','mass_g'])
                w.writeheader()
                for i in range(130):
                    w.writerow({'host_monotonic_s':100+i,'quality_flag':'OK','gas_1_v':.5,'gas_enabled_mask':1,
                                'pump_state':1,'sample_valve_state':1,'purge_valve_state':0,
                                'motor_running':'false','mass_g':1000-i/10})
            with (root / 'events.csv').open('w', newline='') as f:
                w = csv.DictWriter(f,['event_id','event_type','host_monotonic_s']);w.writeheader()
                w.writerows([{'event_id':'start','event_type':'SESSION_START','host_monotonic_s':100},
                             {'event_id':'rest','event_type':'REST_START','host_monotonic_s':100},
                             {'event_id':'checked','event_type':'MASTER_CHECK','host_monotonic_s':224}])
            with (root / 'master_labels.csv').open('w',newline='') as f:
                w=csv.DictWriter(f,['event_id','stage_label','operator','revision','label_id']);w.writeheader()
                w.writerow({'event_id':'checked','stage_label':'resting','operator':'EXPERT_A','revision':1,'label_id':'L1'})
            processed=export_features(root,Path(d)/'processed',window_s=60,step_s=30)
            with self.assertRaises(ValueError):curate(root,processed,reviewer='R')
            rows,proof=curate(root,processed,reviewer='R',physical_verified=True,evidence_ref='local-log-1')
            self.assertEqual(len(rows),1)
            self.assertGreaterEqual(rows[0]['label_lag_s'],0)
            self.assertEqual(proof['original_source_mode'],'serial_unverified')
            dest=export(root,processed,Path(d)/'curated',reviewer='R',physical_verified=True,evidence_ref='local-log-1')
            self.assertTrue((dest/'curated.csv').exists())
            (root/'sensor_1hz.csv').write_text('altered',encoding='utf-8')
            with self.assertRaises(ValueError):curate(root,processed,reviewer='R',physical_verified=True,evidence_ref='local-log-1')


if __name__ == '__main__':unittest.main()
