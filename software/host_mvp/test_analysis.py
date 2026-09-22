import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml
from analysis import Parameters, analyze_batch, export_report, repeatability, sequence_metrics
from replay import BatchSnapshot
from test_replay import csv_file, hashes


def make_batch(root, offset=0):
    root.mkdir()
    (root / "meta.yaml").write_text(yaml.safe_dump(dict(source_mode="simulate", initial_mass_g=100,
        cultivar="tea", origin="test", protocol_id="P1", experiment_protocol_version="SOP1",
        calibration_version="CAL1", sensor_model_and_batch="MOS1")), encoding="utf-8")
    rows = [dict(seq=i, mcu_t_ms=i*1000, host_monotonic_s=100+i, quality_flag="OK", gas_enabled_mask=1,
                 gas_1_v=10+i*2+offset, mass_g=100-i) for i in range(5)]
    csv_file(root, "sensor_1hz.csv", rows)
    csv_file(root, "events.csv", [dict(host_monotonic_s=100, event_type="SESSION_START")])
    return rows


class AnalysisTests(unittest.TestCase):
    def test_known_drift_windows_and_traceable_exclusions(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "A"; rows = make_batch(root)
            params = Parameters(0, 2, 2, 4)
            result = analyze_batch(BatchSnapshot(root), params)
            self.assertEqual(result["baseline"]["mean"], 11)
            self.assertEqual(result["baseline"]["slope_per_s"], 2)
            self.assertEqual(result["stable_minus_baseline"], 4)
            self.assertEqual(result["baseline"]["records"], [2, 3])
            self.assertEqual(result["curve"][-1]["loss_pct"], 4)
            rows[0]["quality_flag"] = "WARMUP"
            rows[1]["gas_enabled_mask"] = 0
            rows[2]["gas_1_v"] = "nan"
            csv_file(root, "sensor_1hz.csv", rows)
            result = analyze_batch(BatchSnapshot(root), params)
            self.assertIsNone(result["baseline"]["mean"])
            self.assertIsNone(result["stable_minus_baseline"])
            self.assertIsNone(result["stable"]["sample_sd"])
            self.assertEqual([p["reason"] for p in result["exclusions"]], ["quality_flag", "disabled_or_invalid_channel", "missing_or_nonfinite"])

    def test_restart_reordering_duplicates_are_not_missing_packets(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "A"; make_batch(root)
            csv_file(root, "sensor_1hz.csv", [dict(seq=s, mcu_t_ms=m, host_monotonic_s=i)
                for i, (s, m) in enumerate([(10, 100), (12, 110), (0, 0), (1, 10), (0, 11), (2, 20), (2, 20)])])
            result = sequence_metrics(BatchSnapshot(root))
            self.assertEqual(result["inferred_missing"], 1)
            self.assertEqual(result["reset_evidence"], 1)
            self.assertEqual(result["backward_or_reordered"], 1)
            self.assertEqual(result["duplicates"], 1)
            csv_file(root, "sensor_1hz.csv", [dict(seq=s, mcu_t_ms=i*1000, host_monotonic_s=i)
                for i, s in enumerate([1, 3, 2, 4])])
            self.assertEqual(sequence_metrics(BatchSnapshot(root))["inferred_missing"], 0)

    def test_repeatability_requires_comparable_group_and_null_cv(self):
        with tempfile.TemporaryDirectory() as folder:
            a, b = Path(folder) / "A", Path(folder) / "B"
            make_batch(a); make_batch(b, 2)
            batches = [BatchSnapshot(a), BatchSnapshot(b)]
            params = Parameters(0, 2, 2, 4)
            results = [analyze_batch(batch, params) for batch in batches]
            self.assertFalse(repeatability(batches, results, params)["eligible"])
            params = Parameters(0, 2, 2, 4, repeat_group="TEST")
            result = repeatability(batches, results, params)
            self.assertTrue(result["eligible"])
            self.assertAlmostEqual(result["sample_sd"], 2**.5)
            batches[1].meta["calibration_version"] = "other"
            self.assertFalse(repeatability(batches, results, params)["eligible"])
            batches[1].meta["calibration_version"] = "CAL1"
            for r in results:
                r["stable"]["mean"] = 0
            self.assertIsNone(repeatability(batches, results, params)["cv_pct"])

    def test_export_is_reproducible_and_never_overwrites_raw(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "A"; make_batch(root)
            before = hashes(root)
            params = Parameters(0, 2, 2, 4)
            output = export_report([root], Path(folder) / "reports", params)
            second = export_report([root], Path(folder) / "reports", params)
            self.assertNotEqual(output, second)
            first_json = json.loads((output / "report.json").read_text(encoding="utf-8"))
            second_json = json.loads((second / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(first_json["batches"], second_json["batches"])
            self.assertEqual(first_json["inputs"], second_json["inputs"])
            self.assertEqual(hashes(root), before)
            self.assertTrue((output / "curves.png").stat().st_size > 1000)
            self.assertFalse((output / "INCOMPLETE").exists())
            with self.assertRaises(ValueError):
                export_report([root], root / "reports", params)
            (root / "INCOMPLETE").touch()
            with self.assertRaises(ValueError):
                export_report([root], Path(folder) / "reports", params)

    def test_source_mutation_and_invalid_parameters_fail(self):
        for params in [Parameters(0, 0, 2, 4), Parameters(0, 3, 2, 4), Parameters(0, 2, 2, float("inf"))]:
            with self.assertRaises(ValueError):
                params.validate()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "A"; make_batch(root)
            with patch("analysis.manifest", side_effect=[{"sensor_1hz.csv": "first"}, {"sensor_1hz.csv": "changed"}]):
                with self.assertRaisesRegex(ValueError, "变化"):
                    export_report([root], Path(folder) / "reports", Parameters(0, 2, 2, 4))
            self.assertFalse((Path(folder) / "reports").exists())


if __name__ == "__main__":
    unittest.main()
