"""Desktop integration checks using simulated frames; no physical devices."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import time
import csv
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import app
from PySide6.QtTest import QTest


class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = app.QtWidgets.QApplication.instance() or app.QtWidgets.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "raw"
        self.patcher = patch.object(app, "DATA_ROOT", self.root)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def stop_and_wait(self, window):
        window.stop_session()
        deadline = time.monotonic() + 5
        while (not window.logger.finished or window.logger.pending_images or window.finishing) and time.monotonic() < deadline:
            QTest.qWait(20)
        self.assertTrue(window.logger.finished)
        self.assertFalse(window.finishing)

    def close_and_wait(self, window):
        window.close()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            QTest.qWait(20)
            if ((not window.reader or not window.reader.isRunning()) and window.logger.finished
                    and (not window.camera or not window.camera.thread.is_alive()) and window.logger.journal.finished):
                break
        self.assertTrue(window.logger.journal.finished)

    def test_batch_cannot_escape_or_overwrite(self):
        logger = app.SessionLogger()
        for name in ("../escape", "D:\\escape", "CON", "a/b"):
            with self.assertRaises(ValueError):
                logger.start(name, "Tester", "simulate")
        existing = self.root / "EXISTING"
        existing.mkdir(parents=True)
        marker = existing / "meta.yaml"
        marker.write_text("preserve", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            logger.start("EXISTING", "Tester", "simulate")
        self.assertEqual(marker.read_text(), "preserve")

    def test_simulated_ui_to_report_and_second_batch(self):
        window = app.MainWindow("", 115200, True)
        try:
            reader = app.SensorReader("", 115200, True)
            window.logger.start("BATCH_TEST", "Tester", "simulate")
            for seq in range(3):
                frame = reader._mock_frame(seq)
                frame["t_ms"] = seq * 1000
                window.on_sensor(frame)
            window.mark_event("SHAKE_START")
            window.mark_event("SHAKE_END")
            self.stop_and_wait(window)
            report = (self.root / "BATCH_TEST" / "session_check.txt").read_text(encoding="utf-8")
            self.assertIn("基础检查通过", report)
            self.assertIn("传感器行数: 3", report)
            window.logger.start("BATCH_SECOND", "Tester", "simulate")
            self.assertEqual(window.logger.event_id, 1)
            self.stop_and_wait(window)
            self.assertIn("没有传感器数据", (self.root / "BATCH_SECOND" / "session_check.txt").read_text(encoding="utf-8"))
        finally:
            self.close_and_wait(window)

    def test_invalid_frame_does_not_crash(self):
        window = app.MainWindow("", 115200, True)
        try:
            window.on_sensor({"seq": "bad", "gas_adc": None})
            self.assertIn("无效传感器帧", window.log.toPlainText())
        finally:
            self.close_and_wait(window)

    def test_simulation_thread_lifecycle(self):
        window = app.MainWindow("", 115200, True)
        try:
            window.toggle_connection()
            deadline = time.monotonic() + 3
            while (not window.reader.connected or window.reader.tracker.pending or not window.reader.latest) and time.monotonic() < deadline:
                QTest.qWait(20)
                time.sleep(.01)
            window.batch_edit.setText("BATCH_THREAD")
            window.batch_metadata["initial_mass_g"] = 1000
            window.start_session()
            self.assertTrue(window.logger.active)
            self.assertFalse(window.metadata_button.isEnabled())
            self.assertFalse(window.sim_check.isEnabled())
            self.assertFalse(window.connect_btn.isEnabled())
            QTest.qWait(2200)
            self.stop_and_wait(window)
            self.assertTrue(window.metadata_button.isEnabled())
            self.assertIsNone(window.batch_metadata["initial_mass_g"])
            self.assertTrue(window.connect_btn.isEnabled())
            report = (self.root / "BATCH_THREAD" / "session_check.txt").read_text(encoding="utf-8")
            self.assertIn("基础检查通过", report)
            window.toggle_connection()
            deadline = time.monotonic() + 4
            while window.reader is not None and time.monotonic() < deadline:
                QTest.qWait(20)
                time.sleep(.01)
            self.assertIsNone(window.reader)
            self.assertTrue(window.sim_check.isEnabled())
        finally:
            self.close_and_wait(window)

    def test_firmware_faults_and_details_are_retained(self):
        window = app.MainWindow("", 115200, False)
        try:
            window.logger.start("BATCH_HARDWARE", "Tester", "serial")
            frame = app.SensorReader("", 115200, True)._mock_frame(0)
            frame.update(quality_flag="WARMUP;UNCALIBRATED", mass_g=None,
                         gas_enabled_mask=7, firmware="QY-FW-0.2.0", errors=["HX711_NO_FRESH_DATA"])
            frame["gas_adc"][3] = frame["gas_v"][3] = None
            window.on_sensor(frame)
            frame["seq"] = 2
            window.on_sensor(frame)
            self.stop_and_wait(window)
            with (self.root / "BATCH_HARDWARE" / "sensor_1hz.csv").open(encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(rows[1]["quality_flag"], "WARMUP;UNCALIBRATED;SERIAL_GAP")
            self.assertEqual(rows[0]["mass_g"], "")
            self.assertEqual(rows[0]["gas_enabled_mask"], "7")
            saved = json.loads(rows[0]["device_frame_json"])
            self.assertEqual(saved["firmware"], "QY-FW-0.2.0")
            self.assertEqual(saved["errors"], ["HX711_NO_FRESH_DATA"])
        finally:
            self.close_and_wait(window)


if __name__ == "__main__":
    unittest.main()
