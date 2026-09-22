import csv
import tempfile
import unittest
from pathlib import Path

from session_check import audit_session, write_session_check


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "BATCH_TEST"
        self.root.mkdir()
        (self.root / "meta.yaml").write_text("batch_id: BATCH_TEST\n", encoding="utf-8")

    def write_csv(self, name, rows):
        with (self.root / name).open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    def valid_batch(self):
        row = dict(batch_id="BATCH_TEST", datetime_iso="2026-09-21T10:00:00+08:00",
                   seq=0, host_monotonic_s=100, mcu_t_ms=0, mass_g=4000,
                   chamber_rh_pct=70, ambient_rh_pct=60, chamber_temp_c=27,
                   ambient_temp_c=26, leaf_temp_c=25, bme688_gas_ohm=180000,
                   pump_state=1, sample_valve_state=1, purge_valve_state=0, quality_flag="OK")
        row.update({f"gas_{i}_{u}": 1 for i in range(1, 5) for u in ("adc", "v")})
        rows = [row, dict(row, seq=1, host_monotonic_s=101, mcu_t_ms=1000)]
        self.write_csv("sensor_1hz.csv", rows)
        self.write_csv("events.csv", [dict(batch_id="BATCH_TEST", event_type=t)
                                      for t in ("SESSION_START", "SESSION_END")])
        return rows

    def test_normal_batch_and_no_raw_mutation(self):
        self.valid_batch()
        before = (self.root / "sensor_1hz.csv").read_bytes()
        report = write_session_check(self.root).read_text(encoding="utf-8")
        self.assertIn("基础检查通过", report)
        self.assertIn("传感器行数: 2", report)
        self.assertEqual(before, (self.root / "sensor_1hz.csv").read_bytes())

    def test_gap_reset_nan_range_and_long_interval(self):
        rows = self.valid_batch()
        rows[1].update(seq=4, mcu_t_ms=-1, host_monotonic_s=108, mass_g="nan", ambient_rh_pct=120)
        self.write_csv("sensor_1hz.csv", rows)
        report = audit_session(self.root)
        for expected in ("seq 不连续", "时间未递增", "超过 3 秒", "缺失或非有限数值: mass_g", "湿度越界"):
            self.assertIn(expected, report)

    def test_empty_missing_and_unclosed_batch(self):
        report = audit_session(self.root)
        self.assertIn("没有传感器数据", report)
        self.valid_batch()
        self.write_csv("events.csv", [dict(batch_id="BATCH_TEST", event_type="SESSION_START")])
        self.assertIn("未正确成对", audit_session(self.root))

    def test_bad_headers_and_empty_events_are_reported(self):
        self.valid_batch()
        (self.root / "sensor_1hz.csv").write_text("unexpected\nvalue\n", encoding="utf-8")
        (self.root / "events.csv").write_text("batch_id,event_type\n", encoding="utf-8")
        report = audit_session(self.root)
        self.assertIn("传感器列缺失: seq", report)
        self.assertIn("未正确成对", report)

    def test_existing_quality_flags_are_not_silently_passed(self):
        rows = self.valid_batch()
        rows[0]["quality_flag"] = "SENSOR_ERROR"
        self.write_csv("sensor_1hz.csv", rows)
        self.assertIn("存在非 OK 或缺失的质量标记", audit_session(self.root))

    def test_disabled_channel_is_empty_but_enabled_channel_is_required(self):
        rows = self.valid_batch()
        for row in rows:
            row.update(gas_enabled_mask=7, gas_4_adc="", gas_4_v="")
        self.write_csv("sensor_1hz.csv", rows)
        self.assertIn("基础检查通过", audit_session(self.root))
        rows[1]["gas_1_v"] = ""
        self.write_csv("sensor_1hz.csv", rows)
        self.assertIn("缺失或非有限数值: gas_1_v", audit_session(self.root))

    def test_invalid_channel_mask_is_not_accepted(self):
        rows = self.valid_batch()
        rows[0]["gas_enabled_mask"] = 99
        rows[1]["gas_enabled_mask"] = 7
        self.write_csv("sensor_1hz.csv", rows)
        self.assertIn("无效 gas_enabled_mask", audit_session(self.root))


if __name__ == "__main__":
    unittest.main()
