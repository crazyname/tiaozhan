import copy
import itertools
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from preflight import I2C_REQUIRED, collect, evaluate, main, probe_camera, probe_disk


def hung_camera(connection, settings):
    time.sleep(30)


def failed_camera(connection, settings):
    connection.recv()
    connection.send(dict(ok=False, error="camera disconnected"))


def fixture():
    info = dict(device_id="UNIT-1", firmware="QY-FW-0.4.0", hardware="QY-HW-0.2-N8R8", source_mode="hardware",
                gas_enabled_mask=7, offset_known=True, hx_offset=100, hx_counts_per_g=40,
                i2c_present={key: True for key in I2C_REQUIRED})
    frame = dict(info, type="sensor", gas_adc=[100, 200, 300, None], gas_v=[.1, .2, .3, None], quality_flag="OK",
                 mass_g=1000, ambient_t_c=25, ambient_rh_pct=60, chamber_t_c=25, chamber_rh_pct=60,
                 leaf_t_c=25, bme688_gas_ohm=100000, pump=0, valve_sample=0, valve_purge=0,
                 motor_compiled=True, motor_running=False, motor_fault="NONE", shake_actual_rpm=0, motor_pwm_duty=0)
    return info, [(100+i, dict(frame, seq=i, t_ms=1200000+i*1000)) for i in range(6)]


class PreflightTests(unittest.TestCase):
    def report(self, info, samples):
        return evaluate(info, samples, dict(state="pass"), dict(state="pass"))

    def test_complete_readiness_and_no_physical_claim(self):
        report = self.report(*fixture())
        self.assertEqual(report["overall"], "pass")
        self.assertFalse(report["hardware_verified"])
        self.assertEqual(len(report["checks"]), 10)

    def test_each_required_device_and_missing_legacy_information(self):
        for key in I2C_REQUIRED:
            info, samples = fixture(); info["i2c_present"][key] = False
            self.assertEqual(self.report(info, samples)["overall"], "fail", key)
        info, samples = fixture(); del info["i2c_present"]
        self.assertEqual(self.report(info, samples)["overall"], "unknown")

    def test_identity_reset_gaps_burst_and_invalid_samples(self):
        for change in (dict(device_id="OTHER"), dict(source_mode="simulate"), dict(seq=1), dict(t_ms=0), dict(gas_adc=[])):
            info, samples = fixture(); samples[3][1].update(change)
            self.assertEqual(self.report(info, samples)["overall"], "fail", change)
        info, samples = fixture()
        self.assertEqual(self.report(info, [(100+i*.01, f) for i, (_, f) in enumerate(samples)])["overall"], "fail")
        samples[4] = (110, samples[4][1])
        self.assertEqual(self.report(info, samples)["overall"], "fail")

    def test_mask_calibration_warmup_and_actuators(self):
        for change in (dict(gas_enabled_mask=15), dict(quality_flag="WARMUP"), dict(mass_g=None), dict(pump=1),
                       dict(motor_running=True), dict(shake_actual_rpm=2), dict(motor_pwm_duty=.1), dict(motor_fault="HOST_TIMEOUT"), dict(hx_counts_per_g=50)):
            info, samples = fixture(); samples[-1][1].update(change)
            self.assertEqual(self.report(info, samples)["overall"], "fail", change)
        for change in (dict(offset_known=False), dict(hx_counts_per_g=0)):
            info, samples = fixture(); info.update(change)
            self.assertEqual(self.report(info, samples)["overall"], "fail")

    def test_skip_camera_and_insufficient_disk_do_not_pass(self):
        info, samples = fixture()
        self.assertEqual(evaluate(info, samples, dict(state="unknown"), dict(state="pass"))["overall"], "unknown")
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(probe_disk(directory, 2**63)["state"], "fail")
            self.assertEqual(probe_disk(directory, 1)["state"], "pass")
            self.assertFalse(list(Path(directory).iterdir()))

    def test_camera_timeout_and_disconnect_are_bounded(self):
        started = time.monotonic()
        self.assertEqual(probe_camera({}, timeout=.2, target=hung_camera)["state"], "fail")
        self.assertLess(time.monotonic()-started, 4)
        self.assertIn("disconnected", probe_camera({}, timeout=5, target=failed_camera)["error"])

    def test_serial_preserves_invalid_bytes_and_only_queries_info(self):
        info, samples = fixture()
        chunks = iter([b'\xff\n', json.dumps(dict(info, message="firmware configuration")).encode()+b'\n']+
                      [json.dumps(frame).encode()+b'\n' for _, frame in samples])
        writes = []

        class Port:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def write(self, value): writes.append(value); return len(value)
            def read_until(self, *args): return next(chunks, b'')

        with tempfile.TemporaryDirectory() as directory, patch("preflight.time.monotonic", side_effect=itertools.count(0, .5)):
            raw = Path(directory)/"raw.jsonl"
            actual_info, actual_samples, error = collect("TEST", 115200, 12, raw, serial_factory=lambda *a, **kw: Port())
            self.assertEqual(actual_info["device_id"], info["device_id"])
            self.assertEqual(len(actual_samples), 6)
            self.assertIsNotNone(error)
            self.assertIn('"data_b64": "/wo="', raw.read_text())
            self.assertEqual(writes, [b'{"cmd":"info"}\n'])

    def test_cli_report_failure_is_archived_without_overwriting(self):
        info, samples = fixture()
        def fake_collect(port, baud, duration, path):
            path.write_text("evidence\n")
            return copy.deepcopy(info), copy.deepcopy(samples), None
        with tempfile.TemporaryDirectory() as directory, patch("preflight.collect", side_effect=fake_collect), patch("builtins.print"):
            for _ in range(2):
                self.assertEqual(main(["--port", "TEST", "--skip-camera", "--output", directory, "--data-root", directory]), 2)
            runs = list(Path(directory).iterdir())
            self.assertEqual(len(runs), 2)
            for run in runs:
                report = json.loads((run/"report.json").read_text(encoding="utf-8"))
                self.assertEqual(report["overall"], "unknown")
                self.assertEqual(len(report["raw_sha256"]), 64)
                self.assertFalse((run/"INCOMPLETE").exists())

    def test_complete_serial_window_passes_without_action_commands(self):
        info, samples = fixture()
        chunks = iter([json.dumps(dict(info, message="firmware configuration")).encode()+b'\n']+
                      [json.dumps(frame).encode()+b'\n' for _, frame in samples])
        writes = []

        class Port:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def write(self, value): writes.append(value); return len(value)
            def read_until(self, *args): return next(chunks, b'')

        with tempfile.TemporaryDirectory() as directory, patch("preflight.time.monotonic", side_effect=itertools.count(0, .5)):
            actual_info, actual_samples, error = collect("TEST", 115200, 7.5, Path(directory)/"raw.jsonl",
                                                        serial_factory=lambda *a, **kw: Port())
            self.assertIsNone(error)
            self.assertEqual(self.report(actual_info, actual_samples)["overall"], "pass")
            self.assertEqual(writes, [b'{"cmd":"info"}\n'])


if __name__ == "__main__":
    unittest.main()
