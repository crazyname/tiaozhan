import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import json
import tempfile
import time
import unittest
from pathlib import Path

from PySide6.QtWidgets import QApplication
from camera import CameraWorker
from camera_process import camera_settings
from schema import now_iso
from storage import DiagnosticJournal, SessionLogger
from test_pipeline import wait_for, table


def hung_camera(connection, settings):
    connection.recv()
    time.sleep(60)  # Parent must kill the process, not wait for this sleep.


def successful_camera(connection, settings):
    import cv2
    import numpy as np
    try:
        while connection.recv() == "capture":
            ok, data = cv2.imencode(".jpg", np.zeros((20, 20, 3), dtype=np.uint8))
            connection.send(dict(ok=True, data=data.tobytes(), datetime_iso=now_iso(), host_monotonic_s=time.monotonic(),
                                 exposure=-5, white_balance=4000, note="fake process"))
    except EOFError:
        pass


class RecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def test_hung_camera_is_killed_and_next_request_recovers(self):
        with tempfile.TemporaryDirectory() as folder:
            logger = SessionLogger(Path(folder) / "raw")
            root = logger.start("A", "tester", "simulate", camera_settings={"index": 2, "exposure": -5})
            worker = CameraWorker(logger, timeout=.3, process_target=hung_camera)
            try:
                worker.request()
                wait_for(lambda: logger.pending_images == 0, 5)
                self.assertIsNone(worker.process)
                worker.process_target = successful_camera; worker.timeout = 5
                worker.request()
                wait_for(lambda: logger.pending_images == 0, 8)
                self.assertEqual(worker.settings["index"], 2)
                logger.stop()
                self.assertEqual(len(table(root, "image_index.csv")), 1)
                self.assertIn("未返回", (root / "device_health.jsonl").read_text(encoding="utf-8"))
            finally:
                worker.close(); worker.thread.join(4)
                self.assertFalse(worker.thread.is_alive())
                self.assertIsNone(worker.process)
                logger.stop(); logger.journal.close()

    def test_close_cancels_hung_and_queued_photos_without_stuck_batch(self):
        with tempfile.TemporaryDirectory() as folder:
            logger = SessionLogger(Path(folder) / "raw")
            root = logger.start("A", "tester", "simulate")
            worker = CameraWorker(logger, timeout=30, process_target=hung_camera)
            try:
                worker.request(); worker.request()
                wait_for(lambda: worker.process is not None)
                worker.close(); worker.thread.join(4)
                self.assertFalse(worker.thread.is_alive())
                self.assertEqual(logger.pending_images, 0)
                logger.stop()
                self.assertEqual(table(root, "image_index.csv"), [])
                self.assertIn("取消", (root / "device_health.jsonl").read_text(encoding="utf-8"))
            finally:
                worker.close(); worker.thread.join(4); logger.stop(); logger.journal.close()

    def test_journal_rotation_preserves_all_records_and_stops_at_capacity(self):
        with tempfile.TemporaryDirectory() as folder:
            journal = DiagnosticJournal(Path(folder), segment_bytes=150, max_segments=20)
            for i in range(10):
                journal.submit("test", dict(i=i, text="中文记录"))
            journal.close(); journal.thread.join(4)
            self.assertEqual(journal.error, "")
            paths = sorted(Path(folder).glob("*.jsonl"))
            self.assertGreater(len(paths), 1)
            self.assertTrue(all(path.stat().st_size <= 150 for path in paths))
            records = [json.loads(line) for path in paths for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["i"] for row in records], list(range(10)))
            before = {path: path.read_bytes() for path in paths}
            limited = DiagnosticJournal(Path(folder), segment_bytes=70, max_segments=1)
            for i in range(3):
                limited.submit("test", dict(i=i, text="中文记录"))
            limited.close(); limited.thread.join(4)
            self.assertIn("上限", limited.error)
            self.assertTrue(all(path.read_bytes() == data for path, data in before.items()))

    def test_invalid_camera_configuration(self):
        self.assertEqual(camera_settings()["index"], 0)
        for values in ({"index": -1}, {"exposure": float("nan")}, {"white_balance": True}, {"index": "0"}):
            with self.assertRaises(ValueError):
                camera_settings(values)


if __name__ == "__main__":
    unittest.main()
