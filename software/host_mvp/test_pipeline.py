"""Fault injection and lifecycle checks; all outputs live in temporary directories."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import base64
import csv
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6 import QtWidgets
from camera import CameraWorker
from device import CommandTracker, SensorReader
from labels import SCORES
from schema import now_iso
from session_check import audit_session
from storage import BatchWriter, SessionLogger

def wait_for(predicate, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError("background operation did not complete")

def table(root, name):
    with (root / name).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))

class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.logger = SessionLogger(Path(self.temp.name) / "raw")
        self.readers = []
        self.cameras = []

    def tearDown(self):
        for reader in self.readers:
            reader.stop(); self.assertTrue(reader.wait(4000))
        for camera in self.cameras:
            camera.close(); camera.thread.join(4)
            self.assertFalse(camera.thread.is_alive())
        self.logger.stop()
        self.logger.journal.close()
        if self.logger.journal.thread:
            self.logger.journal.thread.join(4)
        self.temp.cleanup()

    def start(self, name="TEST"):
        return self.logger.start(name, "tester", "simulate")

    def test_raw_invalid_utf8_and_json_survive_before_parse(self):
        root = self.start()
        reader = SensorReader("", 115200, True, self.logger)
        bad = b'\xff invalid JSON\r\n'
        reader.consume(bad)
        reader.consume((json.dumps(reader._mock_frame(1))+"\n").encode())
        self.logger.stop()
        records = [json.loads(line) for line in (root / "raw_serial.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(base64.b64decode(records[0]["data_b64"]), bad)
        self.assertEqual(len(table(root, "sensor_1hz.csv")), 1)
        self.assertIn("PARSE_OR_SCHEMA_ERROR", (root / "device_health.jsonl").read_text(encoding="utf-8"))

    def test_calibration_command_journal_exists_without_batch(self):
        reader = SensorReader("", 115200, True, self.logger)
        self.readers.append(reader); reader.start()
        wait_for(lambda: reader.connected and not reader.tracker.pending)
        reader.submit(dict(cmd="tare"))
        wait_for(lambda: reader.tracker.pending is None)
        reader.stop(); reader.wait(4000)
        self.logger.journal.close(); self.logger.journal.thread.join(4)
        records = [json.loads(line) for path in self.logger.journal.root.glob("*.jsonl") for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertTrue(any(r.get("payload", {}).get("cmd") == "tare" and r.get("phase") == "running" for r in records))
        self.assertTrue(any(r.get("payload", {}).get("cmd") == "tare" and r.get("phase") == "completed" for r in records))
        self.assertFalse(self.logger.data_root.exists())

    def test_background_ingestion_does_not_need_qt_event_loop(self):
        root = self.start()
        reader = SensorReader("", 115200, True, self.logger)
        self.readers.append(reader); reader.start()
        time.sleep(1.3)  # Intentionally no QApplication.processEvents.
        reader.stop(); self.assertTrue(reader.wait(3000))
        self.logger.stop()
        rows = table(root, "sensor_1hz.csv")
        self.assertGreaterEqual(len(rows), 2)
        self.assertGreater(float(rows[1]["host_monotonic_s"]), float(rows[0]["host_monotonic_s"]))

    def test_slow_writer_overflow_preserves_accepted_counts(self):
        entered, release = threading.Event(), threading.Event()
        original = BatchWriter._write_item
        def slow(writer, kind, payload, files, writers):
            entered.set(); release.wait(3)
            return original(writer, kind, payload, files, writers)
        self.logger.queue_capacity = 2
        with patch.object(BatchWriter, "_write_item", slow):
            root = self.start()
            self.assertTrue(entered.wait(2))
            self.logger.event("FIRST"); self.logger.event("SECOND")
            self.assertIsNone(self.logger.event("OVERFLOW"))
            self.assertFalse(self.logger.active)
            release.set(); self.logger.stop()
        summary = json.loads((root / "writer_summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["accepted"], summary["written"])
        self.assertEqual(summary["rejected"]["events"], 1)
        self.assertTrue((root / "INCOMPLETE").exists())
        self.assertIn("发现异常", audit_session(root))

    def test_disk_error_marks_batch_incomplete(self):
        root = self.start()
        wait_for(lambda: self.logger.writer.queue.empty())
        with patch.object(BatchWriter, "_write_item", side_effect=OSError("injected disk full")):
            self.logger.event("DISK_FAILURE")
            wait_for(lambda: self.logger.finished)
        self.assertTrue((root / "INCOMPLETE").exists())
        self.assertIn("injected disk full", self.logger.error)
        summary = json.loads((root / "writer_summary.json").read_text(encoding="utf-8"))
        self.assertTrue(summary["unwritten"])

    def test_stop_waits_for_image_then_allows_next_batch(self):
        root = self.start()
        event = self.logger.event("SNAPSHOT")
        request = self.logger.image_request(event, "simulate")
        self.logger.stop(wait=False)
        with self.assertRaises(RuntimeError):
            self.start("SECOND")
        (root / request["filename"]).write_bytes(b"test image")
        self.logger.image_result(request, dict(ok=True, datetime_iso=now_iso(), host_monotonic_s=time.monotonic()))
        self.logger.stop()
        self.assertEqual(table(root, "image_index.csv")[0]["event_nearby"], str(event))
        second = self.start("SECOND")
        self.logger.image_result(request, dict(ok=True))  # Stale callback is ignored.
        self.logger.stop()
        self.assertEqual(table(second, "image_index.csv"), [])
        self.assertEqual(self.logger.pending_images, 0)

    def test_labels_preserve_unknown_and_revision_history(self):
        root = self.start()
        event = self.logger.event("MASTER_CHECK")
        values = dict(stage_label="unknown", next_action="unknown", free_note="", **{key: None for key in SCORES})
        first = self.logger.label(values, event)
        second = self.logger.label(dict(values, aroma_level=4, free_note="复核"), event, revise=True)
        with self.assertRaises(ValueError):
            self.logger.label(dict(values, aroma_level=0), event)
        with self.assertRaises(ValueError):
            self.logger.label(values, 999)
        self.logger.stop()
        rows = table(root, "master_labels.csv")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["aroma_level"], "")
        self.assertEqual(second["supersedes"], first["label_id"])
        self.assertEqual(rows[1]["revision"], "2")
        self.assertNotIn("标签修订链无效", audit_session(root))

    def test_camera_simulation_and_failure_are_indexed_or_logged(self):
        root = self.start()
        camera = CameraWorker(self.logger, simulate=True)
        self.cameras.append(camera); camera.request(self.logger.event("SNAPSHOT"))
        wait_for(lambda: self.logger.pending_images == 0)
        self.logger.stop()
        rows = table(root, "image_index.csv")
        self.assertEqual(rows[0]["source_mode"], "simulate")
        self.assertGreater((root / rows[0]["filename"]).stat().st_size, 1000)
        second = self.start("BROKEN_CAMERA")
        broken = CameraWorker(self.logger, capture_factory=lambda *a: (_ for _ in ()).throw(OSError("unplugged")))
        self.cameras.append(broken); broken.request()
        wait_for(lambda: self.logger.pending_images == 0)
        self.logger.stop()
        self.assertEqual(table(second, "image_index.csv"), [])
        self.assertIn("unplugged", (second / "device_health.jsonl").read_text(encoding="utf-8"))

    def test_device_change_stops_batch(self):
        root = self.start()
        reader = SensorReader("", 115200, True, self.logger)
        frame = reader._mock_frame(1)
        reader.consume(json.dumps(frame).encode())
        reader.consume(json.dumps(dict(frame, seq=2, device_id="OTHER")).encode())
        self.assertFalse(self.logger.active)
        self.logger.stop()
        self.assertEqual(len(table(root, "sensor_1hz.csv")), 1)
        self.assertTrue((root / "INCOMPLETE").exists())

    def test_serial_fragments_disconnect_and_no_action_replay(self):
        root = self.start()
        connections = []
        class FakeSerial:
            def __init__(self):
                self.buffer = bytearray()
                self.writes = []
                self.fail_read = False
                self.closed = False
            @property
            def in_waiting(self):
                return len(self.buffer)
            def write(self, wire):
                command = json.loads(wire)
                self.writes.append(command)
                if command["cmd"] == "info":
                    self.buffer.extend(b'{"type":"status","message":"firmware configuration"}\n')
                elif command["cmd"] == "air":
                    self.fail_read = True  # Device may have accepted it; acknowledgement is lost.
                return len(wire)
            def read(self, size):
                if self.fail_read:
                    raise OSError("injected cable removal")
                time.sleep(.001)
                data = bytes(self.buffer[:7]); del self.buffer[:7]
                return data  # Deliberately splits every JSON frame.
            def close(self):
                self.closed = True
        def factory(*args, **kwargs):
            connection = FakeSerial(); connections.append(connection)
            return connection
        reader = SensorReader("FAKE", 115200, False, self.logger, serial_factory=factory)
        self.readers.append(reader); reader.start()
        wait_for(lambda: reader.connected and not reader.tracker.pending)
        reader.submit(dict(cmd="air", mode="sample", duration_ms=1000))
        wait_for(lambda: len(connections) >= 2 and reader.connected and not reader.tracker.pending)
        self.assertEqual([c["cmd"] for c in connections[0].writes], ["info", "air"])
        self.assertEqual([c["cmd"] for c in connections[1].writes], ["info"])
        self.assertTrue(reader.tracker.uncertain)
        with self.assertRaises(ValueError):
            reader.submit(dict(cmd="air", mode="sample", duration_ms=1000))
        reader.stop(); self.assertTrue(reader.wait(3000)); self.logger.stop()
        records = [json.loads(line) for line in (root / "commands.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertTrue(any(r["payload"]["cmd"] == "air" and r["phase"] == "unknown" for r in records))
        self.assertTrue(all(c.closed for c in connections))

    def test_reset_invalidates_pending_command_and_logs_sequence_gap(self):
        root = self.start()
        reader = SensorReader("", 115200, True, self.logger)
        reader.connected = True
        reader.consume(json.dumps(reader._mock_frame(10)).encode())
        reader.submit(dict(cmd="air", mode="sample", duration_ms=1000))
        reader.consume(json.dumps(reader._mock_frame(0)).encode())
        self.assertIsNone(reader.tracker.pending)
        self.assertTrue(reader.tracker.uncertain)
        self.logger.stop()
        self.assertIn("SERIAL_GAP", table(root, "sensor_1hz.csv")[1]["quality_flag"])
        self.assertIn("DEVICE_RESET_OR_REORDER", (root / "device_health.jsonl").read_text(encoding="utf-8"))

    def test_audit_detects_missing_image_and_invalid_label_link(self):
        root = self.start()
        job = self.logger.image_request(999)
        self.logger.image_result(job, dict(ok=True, datetime_iso=now_iso(), host_monotonic_s=time.monotonic()))
        self.logger.stop()
        report = audit_session(root)
        self.assertIn("图片索引路径无效或文件缺失", report)
        self.assertIn("图片关联事件不存在", report)

class CommandTests(unittest.TestCase):
    def test_legacy_info_timeout_does_not_block_passive_acquisition(self):
        clock = [0]
        tracker = CommandTracker(lambda: clock[0])
        tracker.start(dict(cmd="info")); clock[0] = 5
        self.assertEqual(tracker.timeout()["phase"], "unknown")
        self.assertFalse(tracker.uncertain)

    def test_calibration_requires_terminal_response(self):
        tracker = CommandTracker()
        tracker.start(dict(cmd="tare"))
        self.assertEqual(tracker.status(dict(message="collecting 20 fresh HX711 readings", ok=True))["phase"], "running")
        with self.assertRaises(ValueError):
            tracker.start(dict(cmd="air"))
        self.assertIsNone(tracker.status(dict(message="unrelated")))
        self.assertEqual(tracker.status(dict(message="calibration saved in NVS", ok=True))["phase"], "completed")
        self.assertIsNone(tracker.pending)

    def test_timeout_blocks_retry_and_late_ack_does_not_complete(self):
        clock = [0]
        tracker = CommandTracker(lambda: clock[0])
        tracker.start(dict(cmd="calibrate", grams=500))
        clock[0] = 16
        self.assertEqual(tracker.timeout()["phase"], "unknown")
        self.assertIsNone(tracker.status(dict(message="calibration saved in NVS", ok=True)))
        with self.assertRaises(ValueError):
            tracker.start(dict(cmd="tare"))
        tracker.start(dict(cmd="stop"))
        self.assertEqual(tracker.status(dict(message="air stopped", ok=True))["phase"], "completed")

    def test_air_acceptance_and_rejection_are_distinct(self):
        tracker = CommandTracker()
        tracker.start(dict(cmd="air"))
        self.assertEqual(tracker.status(dict(message="timed air command accepted", ok=True))["phase"], "accepted")
        tracker.start(dict(cmd="air"))
        self.assertEqual(tracker.status(dict(message="air disabled or duration outside 1000..120000 ms", ok=False))["phase"], "rejected")

if __name__ == "__main__":
    unittest.main()
