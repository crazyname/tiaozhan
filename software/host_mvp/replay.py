"""Read-only snapshots of legacy and current batches. No Qt or write side effects."""
from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

SERIES = {
    **{f"gas_{i}_v": f"气敏{i}（V）" for i in range(1, 5)},
    "mass_g": "质量（g）", "loss_pct": "失水（%）", "leaf_temp_c": "叶温（℃）",
    "ambient_temp_c": "环境温度（℃）", "ambient_rh_pct": "环境湿度（%RH）",
    "chamber_temp_c": "采样腔温度（℃）", "chamber_rh_pct": "采样腔湿度（%RH）",
    "bme688_gas_ohm": "BME688（Ω）",
}


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def iso_seconds(value):
    try:
        dt = datetime.fromisoformat(value)
        # Naive legacy dates have no provable timezone; do not use machine-local TZ.
        return dt.timestamp() if dt.tzinfo is not None else None
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def inside(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"批次路径越界: {relative}")
    return path


@dataclass
class Record:
    line: int  # CSV record ordinal (header is 1), including multiline field records.
    time: float
    values: dict


class BatchSnapshot:
    def __init__(self, root):
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise ValueError("批次目录不存在")
        self.warnings = []
        self.meta = {}
        try:
            value = yaml.safe_load(inside(self.root, "meta.yaml").read_text(encoding="utf-8-sig"))
            if not isinstance(value, dict):
                raise ValueError("meta.yaml 不是对象")
            self.meta = value
        except (OSError, ValueError, yaml.YAMLError) as exc:
            self.warnings.append(f"元数据不可用: {exc}")
        if (self.root / "INCOMPLETE").exists():
            self.warnings.append("批次未完成，当前仅为读取时快照")
        tables = {key: self._read(name) for key, name in {
            "sensors": "sensor_1hz.csv", "events": "events.csv",
            "images": "image_index.csv", "labels": "master_labels.csv"}.items()}
        sensors = tables["sensors"]
        if not sensors:
            self.warnings.append("没有传感器记录")
        fields = set(sensors[0][1]) if sensors else set()
        missing = sorted(set(SERIES) - {"loss_pct"} - fields)
        if missing:
            self.warnings.append("缺失曲线列: " + ", ".join(missing))
        # A single time basis for all tables; never mix epoch seconds and uptime.
        monotonic = [number(row.get("host_monotonic_s")) for _, row in sensors]
        self.clock = "monotonic" if any(t is not None for t in monotonic) else "iso"
        if self.clock == "iso":
            self.warnings.append("使用带时区日期作为时间轴；无时区/无效时间记录不参与回放")
        raw_times = [(self._time(row), row) for _, row in tables["events"]]
        starts = [t for t, row in raw_times if t is not None and row.get("event_type") == "SESSION_START"]
        all_times = [self._time(row) for records in tables.values() for _, row in records]
        valid = [t for t in all_times if t is not None]
        self.origin = starts[0] if starts else min(valid, default=0)
        self.tables = {}
        for key, rows in tables.items():
            timed = []
            invalid = reversed_count = 0
            previous = None
            for line, row in rows:
                stamp = self._time(row)
                if stamp is None:
                    invalid += 1
                    continue
                if previous is not None and stamp < previous:
                    reversed_count += 1
                previous = stamp
                timed.append(Record(line, stamp - self.origin, row))
            if invalid:
                self.warnings.append(f"{key}: {invalid} 条无效时间已排除")
            if reversed_count:
                self.warnings.append(f"{key}: {reversed_count} 处时间倒退；仅显示按时间排序的快照，不修复原文件")
            self.tables[key] = sorted(timed, key=lambda item: item.time)
        self.sensors = self.tables["sensors"]
        self.events = self.tables["events"]
        self.images = self.tables["images"]
        self.labels = self.tables["labels"]
        self.initial_mass = number(self.meta.get("initial_mass_g"))
        if self.initial_mass is None or self.initial_mass <= 0:
            self.initial_mass = next((number(r.values.get("mass_g")) for r in self.sensors
                                      if (number(r.values.get("mass_g")) or 0) > 0), None)
            self.warnings.append("失水基准取首个有效正质量（元数据没有可用初始质量）")
        sources = {str(self.meta.get("source_mode") or "unknown")}
        for record in self.sensors:
            try:
                frame = json.loads(record.values.get("device_frame_json") or "{}")
                if isinstance(frame, dict) and frame.get("source_mode"):
                    sources.add(str(frame["source_mode"]))
            except (ValueError, TypeError):
                pass
        self.source = " / ".join(sorted(sources))
        for record in self.images:
            if self.image_path(record) is None:
                self.warnings.append(f"图片缺失或越界: {record.values.get('filename')}")

    def _read(self, name):
        try:
            with inside(self.root, name).open(encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                return [(i, row) for i, row in enumerate(reader, 2)]
        except (OSError, ValueError, UnicodeError, csv.Error) as exc:
            self.warnings.append(f"{name} 不可用（旧批次可能没有此表）: {exc}")
            return []

    def _time(self, row):
        if self.clock == "monotonic":
            return number(row.get("host_monotonic_s"))
        return iso_seconds(row.get("datetime_iso") or row.get("host_time_iso"))

    def anchor(self, event_type=None, occurrence=1):
        if not event_type:
            return 0.0
        if occurrence < 1:
            raise ValueError("事件序号从1开始")
        matches = [row.time for row in self.events if row.values.get("event_type") == event_type]
        if len(matches) < occurrence:
            raise ValueError(f"{self.root.name} 缺少第 {occurrence} 次 {event_type}")
        return matches[occurrence - 1]

    def value(self, record, field):
        if field == "loss_pct":
            mass = number(record.values.get("mass_g"))
            return (self.initial_mass - mass) / self.initial_mass * 100 if mass is not None and self.initial_mass else None
        return number(record.values.get(field))

    def series(self, field, anchor=0):
        return ([row.time - anchor for row in self.sensors],
                [self.value(row, field) if self.value(row, field) is not None else math.nan for row in self.sensors])

    def at(self, table, seconds):
        return next((row for row in reversed(self.tables[table]) if row.time <= seconds), None)

    def image_path(self, record):
        try:
            path = inside(self.root, record.values.get("filename") or "")
            return path if path.is_relative_to((self.root / "images").resolve()) and path.is_file() else None
        except ValueError:
            return None
