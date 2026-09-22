from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import yaml
from PySide6 import QtCore, QtWidgets
import pyqtgraph as pg
import serial
from session_check import write_session_check


REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = REPO_ROOT / "data" / "raw"

SENSOR_FIELDS = [
    "batch_id",
    "seq",
    "datetime_iso",
    "host_monotonic_s",
    "mcu_t_ms",
    "gas_1_adc",
    "gas_2_adc",
    "gas_3_adc",
    "gas_4_adc",
    "gas_1_v",
    "gas_2_v",
    "gas_3_v",
    "gas_4_v",
    "bme688_gas_ohm",
    "chamber_temp_c",
    "chamber_rh_pct",
    "ambient_temp_c",
    "ambient_rh_pct",
    "leaf_temp_c",
    "mass_g",
    "pump_state",
    "sample_valve_state",
    "purge_valve_state",
    "quality_flag",
    "gas_enabled_mask",
    "device_frame_json",
]

EVENT_FIELDS = [
    "event_id",
    "batch_id",
    "host_time_iso",
    "host_monotonic_s",
    "event_type",
    "event_value",
    "operator",
    "note",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def default_batch_id() -> str:
    return datetime.now().astimezone().strftime("BATCH_%Y%m%d_%H%M%S")


class SensorReader(QtCore.QThread):
    sensor = QtCore.Signal(dict)
    status = QtCore.Signal(str)
    error = QtCore.Signal(str)

    def __init__(self, port: str, baud: int, simulate: bool = False):
        super().__init__()
        self.port = port
        self.baud = baud
        self.simulate = simulate
        self._running = True
        self._serial: serial.Serial | None = None

    def stop(self) -> None:
        self._running = False
        try:
            if self._serial and self._serial.is_open:
                self._serial.close()
        except Exception:
            pass

    def _mock_frame(self, seq: int) -> dict[str, Any]:
        t = time.monotonic()
        gas_adc = [
            int(15100 + 700 * math.sin(t / 15.0)),
            int(18800 + 950 * math.sin(t / 20.0 + 0.6)),
            int(13900 + 520 * math.sin(t / 12.5 + 1.2)),
            int(22000 + 1100 * math.sin(t / 25.0 + 1.8)),
        ]
        gas_v = [x * 4.096 / 32767.0 for x in gas_adc]
        return {
            "type": "sensor",
            "seq": seq,
            "t_ms": int(t * 1000),
            "gas_adc": gas_adc,
            "gas_v": gas_v,
            "bme688_gas_ohm": int(183000 + 12000 * math.sin(t / 18.0)),
            "chamber_t_c": 27.2 + 0.3 * math.sin(t / 30.0),
            "chamber_rh_pct": 71.0 + 2.0 * math.sin(t / 22.0),
            "ambient_t_c": 26.8 + 0.2 * math.sin(t / 40.0),
            "ambient_rh_pct": 69.0 + 1.2 * math.sin(t / 32.0),
            "leaf_t_c": 26.5 + 0.2 * math.sin(t / 27.0),
            "mass_g": 4000.0 - seq * 0.012,
            "pump": 1,
            "valve_sample": 1,
            "valve_purge": 0,
        }

    def run(self) -> None:
        if self.simulate:
            self.status.emit("SIMULATE")
            seq = 0
            while self._running:
                self.sensor.emit(self._mock_frame(seq))
                seq += 1
                self.msleep(1000)
            return

        try:
            self._serial = serial.Serial(self.port, self.baud, timeout=1.0)
            self.status.emit(f"CONNECTED {self.port}")
            while self._running:
                raw = self._serial.readline()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw.decode("utf-8", errors="strict").strip())
                except Exception as exc:
                    self.error.emit(f"JSON parse error: {exc}")
                    continue
                if not isinstance(obj, dict):
                    self.error.emit("JSON frame must be an object")
                    continue
                if obj.get("type") == "sensor":
                    self.sensor.emit(obj)
                elif obj.get("type") == "status":
                    self.status.emit(str(obj.get("message", "status")))
        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            if self._serial and self._serial.is_open:
                self._serial.close()
            self.status.emit("DISCONNECTED")


class SessionLogger:
    def __init__(self) -> None:
        self.active = False
        self.batch_id = ""
        self.operator = ""
        self.root: Path | None = None
        self.sensor_file = None
        self.events_file = None
        self.sensor_writer = None
        self.events_writer = None
        self.event_id = 0
        self.started_monotonic = 0.0

    def start(self, batch_id: str, operator: str, mode: str) -> Path:
        if self.active:
            raise RuntimeError("session already active")
        self.batch_id = batch_id.strip() or default_batch_id()
        if (not re.fullmatch(r"[\w-]{1,80}", self.batch_id)
                or self.batch_id.upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}):
            raise ValueError("批次编号只能包含字母、数字、下划线或短横线（1–80 字），不能使用系统保留名称")
        self.operator = operator.strip() or "unknown"
        self.root = DATA_ROOT / self.batch_id
        self.root.mkdir(parents=True, exist_ok=False)
        images = self.root / "images"
        images.mkdir()
        self.event_id = 0

        meta = {
            "batch_id": self.batch_id,
            "project": "qingyun-zhikong",
            "operator": self.operator,
            "created_at": now_iso(),
            "device": {
                "hardware_version": "MVP-v0.1",
                "firmware": "esp32_s3_mock-or-real-v0.1",
                "host_software": "host_mvp-v0.1",
            },
            "experiment": {
                "mode": "passive_monitoring",
                "source_mode": mode,
                "protocol_version": "JSONL-v0.1",
            },
            "tea": {
                "cultivar": None,
                "origin": None,
                "picking_standard": None,
                "fresh_mass_g": None,
            },
        }
        (self.root / "meta.yaml").write_text(
            yaml.safe_dump(meta, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )

        self.sensor_file = None
        self.events_file = None
        try:
            self.sensor_file = (self.root / "sensor_1hz.csv").open("w", newline="", encoding="utf-8-sig")
            self.events_file = (self.root / "events.csv").open("w", newline="", encoding="utf-8-sig")
            self.sensor_writer = csv.DictWriter(self.sensor_file, fieldnames=SENSOR_FIELDS)
            self.events_writer = csv.DictWriter(self.events_file, fieldnames=EVENT_FIELDS)
            self.sensor_writer.writeheader()
            self.events_writer.writeheader()
            self.sensor_file.flush()
            self.events_file.flush()
            self.started_monotonic = time.monotonic()
            self.active = True
            self.event("SESSION_START", "", "session started")
        except Exception:
            self.active = False
            try:
                if self.sensor_file:
                    self.sensor_file.close()
            finally:
                if self.events_file:
                    self.events_file.close()
            raise
        return self.root

    def event(self, event_type: str, event_value: str = "", note: str = "") -> None:
        if not self.active or self.events_writer is None or self.events_file is None:
            return
        self.event_id += 1
        self.events_writer.writerow(
            {
                "event_id": self.event_id,
                "batch_id": self.batch_id,
                "host_time_iso": now_iso(),
                "host_monotonic_s": f"{time.monotonic():.6f}",
                "event_type": event_type,
                "event_value": event_value,
                "operator": self.operator,
                "note": note,
            }
        )
        self.events_file.flush()

    def write_sensor(self, row: dict[str, Any]) -> None:
        if not self.active or self.sensor_writer is None or self.sensor_file is None:
            return
        self.sensor_writer.writerow({k: row.get(k, "") for k in SENSOR_FIELDS})
        self.sensor_file.flush()

    def image_path(self) -> Path | None:
        if not self.active or self.root is None:
            return None
        stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        return self.root / "images" / f"IMG_{stamp}.jpg"

    def stop(self) -> Path | None:
        if not self.active:
            return
        try:
            self.event("SESSION_END", "", "session ended")
        finally:
            self.active = False
            try:
                if self.sensor_file:
                    self.sensor_file.close()
            finally:
                if self.events_file:
                    self.events_file.close()
        return write_session_check(self.root)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, default_port: str, baud: int, simulate: bool):
        super().__init__()
        self.setWindowTitle("青韵智控 Host MVP v0.1")
        self.resize(1280, 820)

        self.baud = baud
        self.default_simulate = simulate
        self.reader: SensorReader | None = None
        self.logger = SessionLogger()
        self.last_seq: int | None = None
        self.last_frame_mono = 0.0
        self.m0: float | None = None
        self.camera: cv2.VideoCapture | None = None

        self.time_buf: deque[float] = deque(maxlen=600)
        self.gas_buf = [deque(maxlen=600) for _ in range(4)]
        self.t0_plot = time.monotonic()

        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        layout = QtWidgets.QVBoxLayout(root)

        top = QtWidgets.QHBoxLayout()
        self.port_edit = QtWidgets.QLineEdit(default_port)
        self.sim_check = QtWidgets.QCheckBox("模拟数据")
        self.sim_check.setChecked(simulate)
        self.connect_btn = QtWidgets.QPushButton("连接")
        self.conn_label = QtWidgets.QLabel("未连接")
        top.addWidget(QtWidgets.QLabel("串口"))
        top.addWidget(self.port_edit)
        top.addWidget(self.sim_check)
        top.addWidget(self.connect_btn)
        top.addWidget(self.conn_label, 1)
        layout.addLayout(top)

        session = QtWidgets.QHBoxLayout()
        self.batch_edit = QtWidgets.QLineEdit(default_batch_id())
        self.operator_edit = QtWidgets.QLineEdit("Wang")
        self.start_btn = QtWidgets.QPushButton("开始批次")
        self.stop_btn = QtWidgets.QPushButton("结束批次")
        self.stop_btn.setEnabled(False)
        session.addWidget(QtWidgets.QLabel("Batch"))
        session.addWidget(self.batch_edit, 2)
        session.addWidget(QtWidgets.QLabel("操作员"))
        session.addWidget(self.operator_edit, 1)
        session.addWidget(self.start_btn)
        session.addWidget(self.stop_btn)
        layout.addLayout(session)

        stats = QtWidgets.QGridLayout()
        self.mass_label = QtWidgets.QLabel("质量: -- g")
        self.loss_label = QtWidgets.QLabel("失水率: --")
        self.leaf_label = QtWidgets.QLabel("叶温: -- °C")
        self.env_label = QtWidgets.QLabel("环境: -- °C / -- %RH")
        self.chamber_label = QtWidgets.QLabel("采样腔: -- °C / -- %RH")
        self.seq_label = QtWidgets.QLabel("seq: --")
        for i, w in enumerate([
            self.mass_label,
            self.loss_label,
            self.leaf_label,
            self.env_label,
            self.chamber_label,
            self.seq_label,
        ]):
            stats.addWidget(w, i // 3, i % 3)
        layout.addLayout(stats)

        self.plot = pg.PlotWidget(title="MOS / Gas channels (V)")
        self.plot.setLabel("bottom", "time", units="s")
        self.plot.setLabel("left", "signal", units="V")
        self.plot.addLegend()
        self.curves = [
            self.plot.plot([], [], pen=pg.intColor(i, 4), name=f"gas_{i+1}") for i in range(4)
        ]
        layout.addWidget(self.plot, 1)

        events = QtWidgets.QHBoxLayout()
        self.event_buttons: list[tuple[QtWidgets.QPushButton, str]] = []
        for text, event_type in [
            ("摇青开始", "SHAKE_START"),
            ("摇青结束", "SHAKE_END"),
            ("师傅检查", "MASTER_CHECK"),
            ("取样", "SAMPLE_TAKEN"),
            ("拍照", "SNAPSHOT"),
        ]:
            btn = QtWidgets.QPushButton(text)
            events.addWidget(btn)
            self.event_buttons.append((btn, event_type))
        self.note_edit = QtWidgets.QLineEdit()
        self.note_edit.setPlaceholderText("事件备注（可选）")
        events.addWidget(self.note_edit, 2)
        layout.addLayout(events)

        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(300)
        self.log.setMaximumHeight(150)
        layout.addWidget(self.log)

        self.connect_btn.clicked.connect(self.toggle_connection)
        self.start_btn.clicked.connect(self.start_session)
        self.stop_btn.clicked.connect(self.stop_session)
        for btn, event_type in self.event_buttons:
            btn.clicked.connect(lambda _checked=False, e=event_type: self.mark_event(e))

        self.health_timer = QtCore.QTimer(self)
        self.health_timer.timeout.connect(self.health_check)
        self.health_timer.start(1000)

    def append_log(self, text: str) -> None:
        self.log.appendPlainText(f"[{datetime.now().strftime('%H:%M:%S')}] {text}")

    def toggle_connection(self) -> None:
        if self.reader and self.reader.isRunning():
            self.reader.stop()
            self.reader.wait(1500)
            self.reader = None
            self.connect_btn.setText("连接")
            self.port_edit.setEnabled(True)
            self.sim_check.setEnabled(True)
            return

        port = self.port_edit.text().strip()
        simulate = self.sim_check.isChecked()
        if not simulate and not port:
            QtWidgets.QMessageBox.warning(self, "串口", "请输入串口，例如 COM5")
            return
        self.reader = SensorReader(port, self.baud, simulate)
        self.last_seq = None
        self.last_frame_mono = time.monotonic()
        self.port_edit.setEnabled(False)
        self.sim_check.setEnabled(False)
        self.reader.sensor.connect(self.on_sensor)
        self.reader.status.connect(self.on_status)
        self.reader.error.connect(self.on_error)
        self.reader.start()
        self.connect_btn.setText("断开")

    @QtCore.Slot(str)
    def on_status(self, text: str) -> None:
        self.conn_label.setText(text)
        if text == "DISCONNECTED" and not self.logger.active:
            self.port_edit.setEnabled(True)
            self.sim_check.setEnabled(True)
            self.connect_btn.setText("连接")
        self.append_log(text)

    @QtCore.Slot(str)
    def on_error(self, text: str) -> None:
        self.append_log(f"ERROR: {text}")
        self.conn_label.setText("故障")

    def start_session(self) -> None:
        try:
            if not self.reader or not self.reader.isRunning():
                raise RuntimeError("请先连接设备或启动模拟数据")
            mode = "simulate" if self.reader.simulate else "serial"
            root = self.logger.start(self.batch_edit.text(), self.operator_edit.text(), mode)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "开始失败", str(exc))
            return
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.connect_btn.setEnabled(False)
        self.batch_edit.setEnabled(False)
        self.operator_edit.setEnabled(False)
        self.m0 = None
        self.last_seq = None
        self.append_log(f"session started: {root}")

    def stop_session(self) -> None:
        try:
            report = self.logger.stop()
            if report:
                self.append_log(f"完整性报告: {report}")
        except Exception as exc:
            self.on_error(f"结束批次或生成报告失败: {exc}")
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.connect_btn.setEnabled(True)
        self.batch_edit.setEnabled(True)
        self.operator_edit.setEnabled(True)
        self.batch_edit.setText(default_batch_id())
        if not self.reader or not self.reader.isRunning():
            self.port_edit.setEnabled(True)
            self.sim_check.setEnabled(True)
        self.append_log("session ended")

    def mark_event(self, event_type: str) -> None:
        if not self.logger.active:
            QtWidgets.QMessageBox.information(self, "事件", "请先开始批次")
            return
        note = self.note_edit.text().strip()
        if event_type == "SNAPSHOT":
            ok = self.capture_snapshot()
            self.logger.event("SNAPSHOT", "manual", note if ok else f"camera failed; {note}")
        else:
            self.logger.event(event_type, "", note)
        self.note_edit.clear()
        self.append_log(f"event: {event_type}")

    def capture_snapshot(self) -> bool:
        path = self.logger.image_path()
        if path is None:
            return False
        if self.camera is None:
            self.camera = cv2.VideoCapture(0, cv2.CAP_DSHOW)
            self.camera.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
            self.camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
        ok, frame = self.camera.read()
        if not ok or frame is None:
            return False
        return bool(cv2.imwrite(str(path), frame))

    @QtCore.Slot(dict)
    def on_sensor(self, frame: dict[str, Any]) -> None:
        recv_mono = time.monotonic()
        seq = frame.get("seq")
        if (not isinstance(seq, int) or isinstance(seq, bool) or seq < 0
                or not isinstance(frame.get("gas_adc"), list)
                or not isinstance(frame.get("gas_v"), list)):
            self.on_error("无效传感器帧：seq 或气敏数组格式错误")
            return
        self.last_frame_mono = recv_mono
        device_quality = frame.get("quality_flag", "OK")
        quality = device_quality if isinstance(device_quality, str) and device_quality else "SENSOR_ERROR"
        if self.last_seq is not None and seq != self.last_seq + 1:
            quality = "SERIAL_GAP" if quality == "OK" else f"{quality};SERIAL_GAP"
            self.append_log(f"seq gap: {self.last_seq} -> {seq}")
        self.last_seq = seq

        gas_adc = list(frame.get("gas_adc", []))
        gas_v = list(frame.get("gas_v", []))
        gas_adc = (gas_adc + [None] * 4)[:4]
        gas_v = (gas_v + [None] * 4)[:4]

        mass = frame.get("mass_g")
        if (self.logger.active and self.m0 is None and isinstance(mass, (int, float))
                and not isinstance(mass, bool) and math.isfinite(mass) and mass > 0):
            self.m0 = float(mass)
            self.logger.event("T0_INITIAL", f"mass_g={self.m0:.3f}", "automatic first valid mass")

        row = {
            "batch_id": self.logger.batch_id if self.logger.active else "",
            "seq": seq,
            "datetime_iso": now_iso(),
            "host_monotonic_s": f"{recv_mono:.6f}",
            "mcu_t_ms": frame.get("t_ms", ""),
            "gas_1_adc": gas_adc[0],
            "gas_2_adc": gas_adc[1],
            "gas_3_adc": gas_adc[2],
            "gas_4_adc": gas_adc[3],
            "gas_1_v": gas_v[0],
            "gas_2_v": gas_v[1],
            "gas_3_v": gas_v[2],
            "gas_4_v": gas_v[3],
            "bme688_gas_ohm": frame.get("bme688_gas_ohm", ""),
            "chamber_temp_c": frame.get("chamber_t_c", ""),
            "chamber_rh_pct": frame.get("chamber_rh_pct", ""),
            "ambient_temp_c": frame.get("ambient_t_c", ""),
            "ambient_rh_pct": frame.get("ambient_rh_pct", ""),
            "leaf_temp_c": frame.get("leaf_t_c", ""),
            "mass_g": mass if mass is not None else "",
            "pump_state": frame.get("pump", ""),
            "sample_valve_state": frame.get("valve_sample", ""),
            "purge_valve_state": frame.get("valve_purge", ""),
            "quality_flag": quality,
            "gas_enabled_mask": frame.get("gas_enabled_mask", 15),
            "device_frame_json": json.dumps(frame, ensure_ascii=False, separators=(",", ":")),
        }
        try:
            self.logger.write_sensor(row)
        except OSError as exc:
            self.on_error(f"数据写入失败: {exc}")
            self.stop_session()
            return

        self.seq_label.setText(f"seq: {seq}")
        if isinstance(mass, (int, float)):
            self.mass_label.setText(f"质量: {float(mass):.2f} g")
            if self.m0 and self.m0 > 0:
                loss = (self.m0 - float(mass)) / self.m0
                self.loss_label.setText(f"失水率: {loss * 100:.3f}%")
        leaf = frame.get("leaf_t_c")
        if isinstance(leaf, (int, float)):
            self.leaf_label.setText(f"叶温: {float(leaf):.2f} °C")
        self.env_label.setText(
            f"环境: {frame.get('ambient_t_c', '--')} °C / {frame.get('ambient_rh_pct', '--')} %RH"
        )
        self.chamber_label.setText(
            f"采样腔: {frame.get('chamber_t_c', '--')} °C / {frame.get('chamber_rh_pct', '--')} %RH"
        )

        x = recv_mono - self.t0_plot
        self.time_buf.append(x)
        for i in range(4):
            if isinstance(gas_v[i], (int, float)):
                self.gas_buf[i].append(float(gas_v[i]))
            else:
                self.gas_buf[i].append(float("nan"))
            self.curves[i].setData(list(self.time_buf), list(self.gas_buf[i]))

    def health_check(self) -> None:
        if self.reader and self.reader.isRunning() and self.last_frame_mono:
            age = time.monotonic() - self.last_frame_mono
            if age > 3.0:
                self.conn_label.setText(f"数据超时 {age:.1f}s")

    def closeEvent(self, event) -> None:  # noqa: N802
        self.stop_session()
        if self.reader:
            self.reader.stop()
            self.reader.wait(1000)
        if self.camera is not None:
            self.camera.release()
        event.accept()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="青韵智控第一阶段上位机 MVP")
    parser.add_argument("--port", default="COM5", help="串口，例如 COM5")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--simulate", action="store_true", help="不用硬件，直接生成模拟传感器数据")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    app = QtWidgets.QApplication(sys.argv)
    pg.setConfigOptions(antialias=True)
    win = MainWindow(args.port, args.baud, args.simulate)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
