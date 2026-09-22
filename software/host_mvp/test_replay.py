import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import csv
import hashlib
import tempfile
import unittest
from pathlib import Path

from replay import BatchSnapshot


def csv_file(root, name, rows):
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with (root / name).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)


def fixture(root, shift=0):
    root.mkdir()
    (root / "meta.yaml").write_text("source_mode: simulate\ninitial_mass_g: 1000\n", encoding="utf-8")
    csv_file(root, "sensor_1hz.csv", [dict(host_monotonic_s=100+shift+i, mass_g=1000-i*10, gas_1_v=i) for i in range(4)])
    csv_file(root, "events.csv", [dict(host_monotonic_s=100+shift+i, event_id=i+1, event_type=kind)
                                 for i, kind in enumerate(["SESSION_START", "SHAKE_START", "SHAKE_END", "SHAKE_START"])])
    csv_file(root, "master_labels.csv", [dict(host_monotonic_s=102+shift, label_id="one", revision=1, stage_label="rest"),
                                         dict(host_monotonic_s=103+shift, label_id="two", revision=2, supersedes="one", stage_label="ready")])
    csv_file(root, "image_index.csv", [dict(host_monotonic_s=101+shift, filename="images/missing.jpg")])


def hashes(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


class ReplayTests(unittest.TestCase):
    def test_alignment_labels_missing_images_and_read_only(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "BATCH"
            fixture(root)
            before = hashes(root)
            batch = BatchSnapshot(root)
            self.assertEqual(batch.anchor("SHAKE_START", 2), 3)
            self.assertEqual(batch.series("mass_g", 1)[0], [-1, 0, 1, 2])
            self.assertEqual(batch.series("loss_pct")[1], [0, 1, 2, 3])
            self.assertEqual(batch.at("labels", 2.5).values["revision"], "1")
            self.assertEqual(batch.at("labels", 3).values["revision"], "2")
            self.assertIsNone(batch.at("images", 0))
            self.assertIsNone(batch.image_path(batch.at("images", 2)))
            with self.assertRaises(ValueError):
                batch.anchor("SHAKE_START", 3)
            self.assertTrue(any("图片缺失" in x for x in batch.warnings))
            self.assertEqual(hashes(root), before)

    def test_legacy_iso_clock_invalid_rows_and_no_path_escape(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "LEGACY"; root.mkdir()
            csv_file(root, "sensor_1hz.csv", [dict(datetime_iso="2026-01-01T00:00:02+08:00", mass_g=10),
                dict(datetime_iso="2026-01-01T00:00:01+08:00", mass_g=11), dict(datetime_iso="bad", mass_g=12)])
            csv_file(root, "image_index.csv", [dict(datetime_iso="2026-01-01T00:00:01+08:00", filename="../outside.jpg")])
            (Path(folder) / "outside.jpg").write_bytes(b"private")
            batch = BatchSnapshot(root)
            self.assertEqual(batch.clock, "iso")
            self.assertEqual(len(batch.sensors), 2)
            self.assertTrue(any("倒退" in warning for warning in batch.warnings))
            self.assertTrue(any("无效时间" in warning for warning in batch.warnings))
            self.assertIsNone(batch.image_path(batch.images[0]))

    def test_two_batches_align_without_mixing_monotonic_and_iso(self):
        with tempfile.TemporaryDirectory() as folder:
            a, b = Path(folder) / "A", Path(folder) / "B"
            fixture(a); fixture(b, 10000)
            left, right = BatchSnapshot(a), BatchSnapshot(b)
            self.assertEqual(left.series("mass_g", left.anchor("SHAKE_START")), right.series("mass_g", right.anchor("SHAKE_START")))
            csv_file(a, "image_index.csv", [dict(datetime_iso="2026-01-01T00:00:00+08:00", filename="images/x.jpg")])
            self.assertEqual(BatchSnapshot(a).images, [])

    def test_window_cursor_events_and_playback(self):
        from PySide6.QtWidgets import QApplication
        from replay_ui import ReplayWindow
        qt = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as folder:
            a, b = Path(folder) / "A", Path(folder) / "B"
            fixture(a); fixture(b, 10000)
            window = ReplayWindow()
            try:
                window.add_batch(BatchSnapshot(a)); window.add_batch(BatchSnapshot(b))
                window.align.setCurrentIndex(window.align.findData("SHAKE_START"))
                self.assertEqual(window.anchors, {0: 1, 1: 1})
                window.position.setValue(2)
                self.assertIn("label_id: two", window.detail.toPlainText())
                self.assertIn("simulate", window.active.currentText())
                window.jump_event(1, 0)
                self.assertEqual(window.position.value(), 0)
                window.toggle_play(); self.assertTrue(window.timer.isActive())
                window.toggle_play(); self.assertFalse(window.timer.isActive())
                window.occurrence.setValue(3)
                self.assertEqual(window.anchors, {})
                self.assertIn("缺少", window.warnings.toPlainText())
            finally:
                window.close()


if __name__ == "__main__":
    unittest.main()
