import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import json
import tempfile
import unittest
from pathlib import Path

import yaml
from metadata import normalize_metadata, load_profile, save_profile, metadata_digest
from storage import SessionLogger
from device import SensorReader
from schema import now_iso


class MetadataTests(unittest.TestCase):
    def test_invalid_metadata_cannot_create_batch_or_override_provenance(self):
        with tempfile.TemporaryDirectory() as folder:
            logger = SessionLogger(Path(folder) / "raw")
            for values in ({"source_mode": "hardware"}, {"batch_id": "fake"},
                           {"initial_mass_g": "nan"}, {"initial_mass_g": -1},
                           {"initial_mass_g": True}, {"picking_conditions": []}):
                with self.assertRaises(ValueError):
                    logger.start("TEST", "tester", "simulate", values)
            self.assertFalse(logger.data_root.exists())

    def test_profile_roundtrip_no_overwrite_and_integrity(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "profile.json"
            values = {"cultivar": "  单丛 ", "calibration_version": "CAL-01",
                      "experiment_protocol_version": "SOP-03", "initial_mass_g": "1000"}
            save_profile(path, values)
            self.assertEqual(load_profile(path), normalize_metadata(values))
            with self.assertRaises(FileExistsError):
                save_profile(path, {})
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["values"]["calibration_version"] = "changed"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_profile(path)

    def test_batch_snapshot_and_mass_are_immutable_across_configuration_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            logger = SessionLogger(Path(folder) / "raw")
            values = normalize_metadata({"initial_mass_g": 1000, "sensor_model_and_batch": "HX711 #01",
                                         "picking_conditions": "2026-09-22 晴", "calibration_version": "CAL-01"})
            root = logger.start("TEST", "tester", "simulate", values)
            values["calibration_version"] = "later"
            frame = SensorReader("", 115200, True)._mock_frame(0)
            frame.update(mass_g=900, hx_counts_per_g=12)
            logger.ingest(frame, now_iso(), 1)
            frame.update(seq=1, hx_counts_per_g=13)
            logger.ingest(frame, now_iso(), 2)
            logger.stop(); logger.journal.close(); logger.journal.thread.join(3)
            meta = yaml.safe_load((root / "meta.yaml").read_text(encoding="utf-8"))
            self.assertEqual(meta["initial_mass_g"], 1000)
            self.assertEqual(meta["initial_mass_source"], "operator")
            self.assertEqual(meta["operator_metadata"]["calibration_version"], "CAL-01")
            self.assertEqual(meta["operator_metadata_sha256"], metadata_digest(meta["operator_metadata"]))
            self.assertEqual(meta["initial_device_configuration"]["hx_counts_per_g"], 12)
            self.assertEqual(meta["device_configuration"]["hx_counts_per_g"], 13)

    def test_dialog_preserves_unknown_and_validates_mass(self):
        from PySide6.QtWidgets import QApplication
        from app import MetadataDialog
        qt = QApplication.instance() or QApplication([])
        dialog = MetadataDialog(normalize_metadata())
        self.assertTrue(all(value is None for value in dialog.values().values()))
        dialog.inputs["initial_mass_g"].setText("-2")
        dialog.apply()
        self.assertIn("正数", dialog.error.text())
        self.assertEqual(dialog.result(), 0)
        dialog.inputs["initial_mass_g"].setText("25.5")
        dialog.apply()
        self.assertEqual(dialog.result(), 1)
        dialog.close()


if __name__ == "__main__":
    unittest.main()
