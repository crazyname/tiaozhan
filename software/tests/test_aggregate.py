import csv
import json
import tempfile
import unittest
from pathlib import Path

from software.analysis.pipeline import sha256
from software.modeling.aggregate import aggregate, export
from software.modeling.curate import FIELDS


class AggregateTests(unittest.TestCase):
    def test_valid_batch_grouping_and_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            dirs=[]
            for i in range(3):
                batch=f'B{i}'
                folder=root/f'curated_{batch}';folder.mkdir()
                with (folder/'curated.csv').open('w',newline='') as f:
                    w=csv.DictWriter(f,FIELDS);w.writeheader()
                    w.writerow({'batch_id':batch,'source_mode':'physical','stage_label':'resting',
                                'label_event_id':f'E{i}', 'gas_1_v_median':0.5})
                (folder/'curation_manifest.json').write_text(json.dumps({
                    'version':'qy-curation-v0.1','batch_id':batch,'source_mode':'physical',
                    'physical_verified_asserted':True,'external_evidence_ref':f'paper-{i}',
                    'rows':1,'output_sha256':sha256(folder/'curated.csv')}))
                dirs.append(folder)
            rows,manifest=aggregate(dirs)
            self.assertEqual(manifest['batch_count'],3)
            self.assertEqual(len(rows),3)
            exported=export(dirs,root/'processed')
            self.assertTrue((exported/'curated_all.csv').exists())
            with self.assertRaises(ValueError):aggregate([dirs[0]]*3)
            (dirs[0]/'curated.csv').write_text('changed')
            with self.assertRaises(ValueError):aggregate(dirs)


if __name__=='__main__':unittest.main()
