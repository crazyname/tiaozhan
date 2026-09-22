import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from device import CommandTracker, SensorReader
from schema import sensor_row, now_iso
from storage import SessionLogger
from test_pipeline import wait_for, table


class MotorTests(unittest.TestCase):
    def test_motor_responses_require_matching_command_and_request(self):
        tracker = CommandTracker()
        record = tracker.start(dict(cmd="motor", rpm=10, direction=1, duration_ms=1000))
        self.assertIsNone(tracker.status(dict(cmd="motor", request_id=record["request_id"]+1, ok=True)))
        self.assertIsNone(tracker.status(dict(cmd="motor_stop", request_id=record["request_id"], ok=True)))
        result = tracker.status(dict(cmd="motor", request_id=record["request_id"], ok=True))
        self.assertEqual(result["phase"], "accepted")
        record = tracker.start(dict(cmd="motor_reset"))
        result = tracker.status(dict(cmd="motor_reset", request_id=record["request_id"], ok=False))
        self.assertEqual(result["phase"], "rejected")

    def test_unsupported_stale_and_invalid_motor_commands_rejected(self):
        reader = SensorReader("", 115200, True)
        reader.connected = True
        with self.assertRaises(ValueError):
            reader.submit(dict(cmd="motor", rpm=10, direction=1, duration_ms=1000))
        reader.latest = dict(motor_compiled=True); reader.latest_received = time.monotonic()
        for rpm, direction, duration in [(31, 1, 1000), (True, 1, 1000), (10, True, 1000), (10, 1, 300001)]:
            with self.assertRaises(ValueError):
                reader.submit(dict(cmd="motor", rpm=rpm, direction=direction, duration_ms=duration))
        reader.latest_received -= 4
        with self.assertRaises(ValueError):
            reader.submit(dict(cmd="motor_reset"))
        reader.latest = dict(motor_running=True)
        with self.assertRaises(ValueError):
            reader.submit(dict(cmd="tare"))

    def test_telemetry_and_end_events_and_simulated_lease_timeout(self):
        with tempfile.TemporaryDirectory() as folder:
            logger = SessionLogger(Path(folder)/"raw")
            root = logger.start("A", "tester", "simulate")
            reader = SensorReader("", 115200, True, logger)
            reader.start()
            try:
                wait_for(lambda: reader.latest and not reader.tracker.pending)
                reader.operator_alive = time.monotonic()
                reader.submit(dict(cmd="motor", rpm=12, direction=-1, duration_ms=10000))
                wait_for(lambda: not reader.tracker.pending)
                wait_for(lambda: reader.latest.get("motor_running"), 3)
                # Deliberately don't renew the UI liveness timestamp.
                wait_for(lambda: reader.latest.get("motor_fault") == "HOST_TIMEOUT", 4)
                self.assertFalse(reader.latest["motor_running"])
                self.assertFalse(reader.motor_lease_active)
                reader.stop(); self.assertTrue(reader.wait(3000))
                logger.stop()
                rows = table(root, "sensor_1hz.csv")
                self.assertTrue(any(float(row["shake_actual_rpm"]) < 0 for row in rows))
                events = [row["event_type"] for row in table(root, "events.csv")]
                self.assertIn("MOTOR_START", events); self.assertIn("MOTOR_END", events)
                self.assertTrue(all(row["motor_current_a"] == "" for row in rows))
            finally:
                reader.stop(); reader.wait(3000); logger.stop(); logger.journal.close()
                if logger.journal.thread: logger.journal.thread.join(3)

    def test_legacy_frames_have_unknown_motor_fields(self):
        frame = dict(seq=0, gas_adc=[], gas_v=[])
        row = sensor_row(frame, "A", now_iso(), 1)
        self.assertIsNone(row["motor_running"])
        self.assertIsNone(row["shake_actual_rpm"])

    def test_unconfirmed_motor_stop_marks_batch_incomplete_once(self):
        import app
        qt = app.QtWidgets.QApplication.instance() or app.QtWidgets.QApplication([])
        with tempfile.TemporaryDirectory() as folder, patch.object(app, "DATA_ROOT", Path(folder)/"raw"):
            window = app.MainWindow("", 115200, True)
            window.reader = SensorReader("", 115200, True, window.logger)
            window.reader.connected = True
            window.reader.latest = dict(motor_running=True)
            root = window.logger.start("TIMEOUT", "tester", "simulate")
            try:
                window.stop_session()
                window.motor_stop_requested_at -= 6
                window.health_check()
                wait_for(lambda: window.logger.finished)
                window.health_check()
                self.assertFalse(window.stop_after_motor)
                self.assertTrue((root/"INCOMPLETE").exists())
                self.assertIn("静止遥测", window.logger.error)
            finally:
                window.close()
                if window.logger.journal.thread: window.logger.journal.thread.join(3)

    def test_ui_waits_for_motor_stop_before_batch_finishes(self):
        import app
        from PySide6.QtTest import QTest
        qt = app.QtWidgets.QApplication.instance() or app.QtWidgets.QApplication([])
        def poll(predicate, seconds=5):
            end = time.monotonic()+seconds
            while time.monotonic() < end:
                QTest.qWait(20); time.sleep(.01)
                if predicate(): return
            self.fail("UI transition timed out")
        with tempfile.TemporaryDirectory() as folder, patch.object(app, "DATA_ROOT", Path(folder)/"raw"):
            window = app.MainWindow("", 115200, True)
            window.auto_photo.setChecked(False)
            try:
                window.toggle_connection()
                poll(lambda: window.reader.latest and not window.reader.tracker.pending)
                window.batch_edit.setText("MOTOR_UI"); window.start_session()
                window.motor_command("motor")
                poll(lambda: window.reader.latest.get("motor_running"))
                window.stop_session()
                self.assertTrue(window.stop_after_motor)
                self.assertTrue(window.logger.active)
                poll(lambda: window.logger.finished and not window.finishing and not window.stop_after_motor)
                self.assertFalse(window.reader.latest["motor_running"])
            finally:
                window.close()
                poll(lambda: not window.reader.isRunning() and window.logger.finished and window.logger.journal.finished)


if __name__ == "__main__":
    unittest.main()
