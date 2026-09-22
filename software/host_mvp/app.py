from __future__ import annotations
import argparse
import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from PySide6 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg
from camera import CameraWorker
from device import SensorReader
from labels import SCALE_VERSION, STAGES, ACTIONS, SCORES
from schema import HOST_VERSION, SENSOR_FIELDS, EVENT_FIELDS, now_iso, default_batch_id, finite_number
from storage import SessionLogger as BaseSessionLogger

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = REPO_ROOT / "data" / "raw"

class SessionLogger(BaseSessionLogger):
    def __init__(self):
        super().__init__(DATA_ROOT)

class LabelDialog(QtWidgets.QDialog):
    def __init__(self, parent, previous=None):
        super().__init__(parent)
        self.setWindowTitle("师傅观察记录（草案量表）")
        form = QtWidgets.QFormLayout(self)
        form.addRow(QtWidgets.QLabel(f"{SCALE_VERSION}；未判断请留空，评分不是成熟度概率。"))
        self.inputs = {}
        choices = {
            "stage_label": ("当前阶段", list(zip(["未判断", "初始", "摇青中", "静置中", "可杀青"], STAGES))),
            "next_action": ("下一步建议", list(zip(["未判断", "继续静置", "开始摇青", "停止摇青", "开始杀青", "取样"], ACTIONS))),
        }
        for key, name in zip(SCORES, ["香气强度", "叶片柔软度", "红边程度", "主观失水感", "判断信心"]):
            choices[key] = (name, [("未判断", None), ("1 · 很低", 1), ("2 · 较低", 2), ("3 · 中等", 3), ("4 · 较高", 4), ("5 · 很高", 5)])
        for key, (name, items) in choices.items():
            widget = QtWidgets.QComboBox()
            for label, value in items:
                widget.addItem(label, value)
            if previous:
                widget.setCurrentIndex(max(0, widget.findData(previous.get(key))))
            self.inputs[key] = widget
            form.addRow(name, widget)
        self.note = QtWidgets.QPlainTextEdit((previous or {}).get("free_note", ""))
        self.note.setMaximumHeight(100)
        form.addRow("观察说明", self.note)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self):
        return dict({key: widget.currentData() for key, widget in self.inputs.items()}, free_note=self.note.toPlainText())

class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, default_port: str, baud: int, simulate: bool):
        super().__init__()
        self.setWindowTitle(f"青韵智控 {HOST_VERSION}")
        self.resize(1280, 820)

        self.baud = baud
        self.default_simulate = simulate
        self.reader: SensorReader | None = None
        self.logger = SessionLogger()
        self.finishing = self.closing = self.disconnecting = False
        self.shown_error = ""
        self.last_seq: int | None = None
        self.last_frame_mono = 0.0
        self.m0: float | None = None
        self.camera = None

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
        self.health_timer.start(100)
        self.setup_extensions(layout)


    def setup_extensions(self, layout):
        self.setWindowTitle("青韵智控 · 采集工作台 v0.3")
        self.resize(1220, 880)
        self.sim_check.setText("模拟传感器与图像（非实验数据）")
        self.operator_edit.clear(); self.operator_edit.setPlaceholderText("操作员，未知可空")
        metadata = QtWidgets.QHBoxLayout()
        self.meta_inputs = {}
        for key, name in [("cultivar", "品种"), ("origin", "产地"), ("protocol_id", "实验方案编号")]:
            entry = QtWidgets.QLineEdit(); entry.setPlaceholderText(name + "（未知可空）")
            self.meta_inputs[key] = entry; metadata.addWidget(entry)
        layout.insertLayout(2, metadata)
        commands = QtWidgets.QHBoxLayout()
        self.command_buttons = []
        for name, payload in [("设备信息", {"cmd": "info"}), ("空载去皮", {"cmd": "tare"})]:
            button = QtWidgets.QPushButton(name)
            button.clicked.connect(lambda checked=False, p=payload: self.send_command(p))
            commands.addWidget(button); self.command_buttons.append((button, payload["cmd"]))
        self.grams = QtWidgets.QDoubleSpinBox()
        self.grams.setRange(0.1, 20000); self.grams.setValue(500); self.grams.setSuffix(" g 砝码")
        commands.addWidget(self.grams)
        calibration = QtWidgets.QPushButton("砝码标定")
        calibration.clicked.connect(lambda: self.send_command(dict(cmd="calibrate", grams=self.grams.value())))
        commands.addWidget(calibration); self.command_buttons.append((calibration, "calibrate"))
        self.duration = QtWidgets.QSpinBox()
        self.duration.setRange(1, 120); self.duration.setValue(10); self.duration.setSuffix(" 秒")
        commands.addWidget(self.duration)
        for name, mode in [("采样", "sample"), ("吹扫", "purge")]:
            button = QtWidgets.QPushButton(name)
            button.clicked.connect(lambda checked=False, m=mode: self.send_command(dict(cmd="air", mode=m, duration_ms=self.duration.value()*1000)))
            commands.addWidget(button); self.command_buttons.append((button, "air"))
        stop = QtWidgets.QPushButton("停止气路")
        stop.clicked.connect(lambda: self.send_command(dict(cmd="stop")))
        commands.addWidget(stop); self.command_buttons.append((stop, "stop"))
        layout.insertLayout(3, commands)
        self.command_label = QtWidgets.QLabel("去皮/标定仅在批次外进行；气路状态是输出命令，不是阀位或流量反馈。")
        self.command_label.setWordWrap(True); layout.insertWidget(4, self.command_label)
        photo = QtWidgets.QHBoxLayout()
        self.auto_photo = QtWidgets.QCheckBox("每30秒自动拍照，关键事件补拍")
        self.auto_photo.setChecked(True); photo.addWidget(self.auto_photo)
        self.preview = QtWidgets.QLabel("照片预览 · 默认相机0")
        self.preview.setFixedSize(220, 130); self.preview.setAlignment(QtCore.Qt.AlignCenter)
        photo.addWidget(self.preview)
        self.photo_status = QtWidgets.QLabel("后台保存照片；结束批次等待已提交照片")
        self.photo_status.setWordWrap(True); photo.addWidget(self.photo_status, 1)
        revise = QtWidgets.QPushButton("修订最近标签")
        revise.clicked.connect(self.revise_label); photo.addWidget(revise)
        layout.insertLayout(layout.count()-1, photo)
        self.photo_timer = QtCore.QTimer(self)
        self.photo_timer.timeout.connect(self.auto_snapshot); self.photo_timer.start(30000)

    def append_log(self, text):
        self.log.appendPlainText(f"[{datetime.now():%H:%M:%S}] {text}")

    def toggle_connection(self):
        if self.reader and self.reader.isRunning():
            self.disconnecting = True; self.reader.stop(); self.connect_btn.setEnabled(False)
            return
        if self.camera and self.camera.thread.is_alive():
            self.camera.close(); self.append_log("相机正在关闭，请稍后连接"); return
        port, simulate = self.port_edit.text().strip(), self.sim_check.isChecked()
        if not simulate and not port:
            self.on_error("请输入串口，例如 COM5"); return
        self.reader = SensorReader(port, self.baud, simulate, self.logger)
        self.reader.sensor.connect(self.on_sensor); self.reader.status.connect(self.on_status)
        self.reader.error.connect(self.on_error); self.reader.command_result.connect(self.on_command)
        self.port_edit.setEnabled(False); self.sim_check.setEnabled(False)
        self.reader.start(); self.connect_btn.setText("断开")
        self.last_frame_mono = time.monotonic()

    def on_status(self, text):
        self.conn_label.setText(text); self.append_log(text)

    def on_error(self, text):
        self.conn_label.setText("故障 / 请查看记录"); self.append_log(f"ERROR: {text}")

    def send_command(self, payload):
        try:
            if not self.reader:
                raise ValueError("请先连接设备")
            self.reader.submit(payload)
        except (ValueError, RuntimeError) as exc:
            self.on_error(str(exc))

    def on_command(self, record):
        phases = dict(queued="已排队", sent="已发送", running="执行中", completed="设备报告完成", accepted="已接受（不代表动作完成）", rejected="设备拒绝", unknown="结果未知，未重发")
        text = f"{record['payload']['cmd']}: {phases.get(record['phase'], record['phase'])}"
        text += " " + str(record.get("error") or record.get("response", {}).get("message", ""))
        self.command_label.setText(text); self.append_log(text)

    def start_session(self):
        try:
            if self.logger.journal.error:
                raise RuntimeError(self.logger.journal.error + "；请修复存储后重启程序")
            if not self.reader or not self.reader.connected:
                raise RuntimeError("请先连接设备或启动模拟数据")
            if self.reader.tracker.uncertain:
                raise RuntimeError("设备操作结果尚未确认，请检查设备后断开并重新连接")
            if self.reader.tracker.pending or "CALIBRATING" in str(self.reader.latest.get("quality_flag", "")):
                raise RuntimeError("请等待设备命令或标定完成")
            root = self.logger.start(self.batch_edit.text(), self.operator_edit.text(), "simulate" if self.reader.simulate else "serial",
                                     {k: w.text().strip() or None for k, w in self.meta_inputs.items()})
        except Exception as exc:
            self.on_error(f"开始失败: {exc}"); return
        self.finishing = False; self.shown_error = ""
        self.start_btn.setEnabled(False); self.stop_btn.setEnabled(True); self.connect_btn.setEnabled(False)
        for w in [self.batch_edit, self.operator_edit, *self.meta_inputs.values()]:
            w.setEnabled(False)
        self.loss_label.setText("失水率: --")
        self.append_log(f"批次开始: {root}"); self.photo_timer.start(30000); self.auto_snapshot()

    def stop_session(self):
        if not self.logger.writer:
            return
        self.logger.stop(wait=False); self.finishing = True; self.stop_btn.setEnabled(False)
        self.append_log("正在排空数据和照片队列…")

    def mark_event(self, event_type):
        if not self.logger.active:
            self.on_error("请先开始批次"); return
        event_id = self.logger.event(event_type, note=self.note_edit.text().strip())
        self.note_edit.clear()
        if event_type == "SNAPSHOT" or self.auto_photo.isChecked():
            self.capture_snapshot(event_id)
        if event_type == "MASTER_CHECK" and event_id:
            self.edit_label(event_id)
        self.append_log(f"事件: {event_type}")

    def edit_label(self, event_id, revise=False):
        dialog = LabelDialog(self, self.logger.latest_label if revise else None)
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            try:
                row = self.logger.label(dialog.values(), event_id, revise)
                self.append_log(f"标签已排队保存，修订号 {row['revision']}")
            except (ValueError, OSError) as exc:
                self.on_error(str(exc))

    def revise_label(self):
        if self.logger.active and self.logger.latest_label:
            self.edit_label(self.logger.latest_label["event_id"], True)
        else:
            self.on_error("当前批次没有可修订标签")

    def auto_snapshot(self):
        if self.logger.active and self.auto_photo.isChecked():
            self.capture_snapshot()

    def capture_snapshot(self, event_id=""):
        if not self.logger.active:
            return False
        if self.camera is None or not self.camera.thread.is_alive():
            self.camera = CameraWorker(self.logger, simulate=self.reader.simulate if self.reader else self.sim_check.isChecked())
            self.camera.result.connect(self.on_photo)
        return self.camera.request(event_id)

    def on_photo(self, result):
        if result.get("token") and result["token"] != self.logger.token:
            return
        if not result.get("ok"):
            self.photo_status.setText(f"拍照失败: {result.get('error')}"); return
        pixmap = QtGui.QPixmap(); pixmap.loadFromData(result["preview"])
        self.preview.setPixmap(pixmap.scaled(220, 130, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
        self.photo_status.setText("照片已保存 · " + result["datetime_iso"][11:19])

    def on_sensor(self, message):
        try:
            if "frame" in message:
                frame, row, received = message["frame"], message["row"], message["received_mono"]
            else:  # Direct injection for offline verification.
                frame, received = message, time.monotonic()
                row = self.logger.ingest(frame, now_iso(), received)
        except (ValueError, TypeError) as exc:
            self.on_error(f"无效传感器帧: {exc}"); return
        self.last_frame_mono = received
        self.seq_label.setText(f"seq: {frame['seq']} · {row['quality_flag']}")
        mass = frame.get("mass_g")
        self.mass_label.setText(f"质量: {mass:.2f} g" if finite_number(mass) else "质量: -- g")
        if finite_number(mass) and self.logger.m0:
            self.loss_label.setText(f"失水率: {(self.logger.m0-mass)/self.logger.m0*100:.3f}%")
        self.leaf_label.setText(f"叶温: {frame.get('leaf_t_c', '--')} °C")
        self.env_label.setText(f"环境: {frame.get('ambient_t_c', '--')} °C / {frame.get('ambient_rh_pct', '--')} %RH")
        self.chamber_label.setText(f"采样腔: {frame.get('chamber_t_c', '--')} °C / {frame.get('chamber_rh_pct', '--')} %RH")
        self.time_buf.append(received-self.t0_plot)
        gas = (frame["gas_v"] + [None]*4)[:4]
        for i, value in enumerate(gas):
            self.gas_buf[i].append(float(value) if finite_number(value) else float('nan'))
            self.curves[i].setData(list(self.time_buf), list(self.gas_buf[i]))

    def health_check(self):
        failure = self.logger.error or self.logger.journal.error
        if failure and failure != self.shown_error:
            self.shown_error = failure; self.on_error(failure)
            if self.logger.writer and (self.logger.active or not self.logger.finished):
                self.stop_session()
        if self.finishing and self.logger.finished and not self.logger.pending_images:
            self.finishing = False
            self.append_log(f"批次收尾完成: {self.logger.writer.report}；错误: {self.logger.error or '无'}")
            self.start_btn.setEnabled(True); self.connect_btn.setEnabled(True)
            for w in [self.batch_edit, self.operator_edit, *self.meta_inputs.values()]:
                w.setEnabled(True)
            self.batch_edit.setText(default_batch_id())
        if self.disconnecting and self.reader and not self.reader.isRunning():
            self.reader = None; self.disconnecting = False
            if self.camera:
                self.camera.close()
            self.connect_btn.setEnabled(True); self.connect_btn.setText("连接")
            self.port_edit.setEnabled(True); self.sim_check.setEnabled(True)
        connected = bool(self.reader and self.reader.connected)
        pending = self.reader.tracker.pending if self.reader else None
        for button, cmd in self.command_buttons:
            button.setEnabled(connected and (cmd == "stop" or not pending) and (cmd not in ("tare", "calibrate") or not (self.logger.active or self.finishing)))
        if connected and time.monotonic()-self.last_frame_mono > 3:
            self.conn_label.setText("传感器数据超时（已连接）")
        if self.closing:
            reader_done = not self.reader or not self.reader.isRunning()
            if reader_done:
                self.logger.journal.close()
            if reader_done and self.logger.finished and not self.logger.pending_images and (not self.camera or not self.camera.thread.is_alive()) and self.logger.journal.finished:
                self.close()

    def closeEvent(self, event):
        if not self.closing:
            self.closing = True; self.stop_session()
            if self.reader:
                self.reader.stop()
            if self.camera:
                self.camera.close()
            self.setEnabled(False)
        ready = (not self.reader or not self.reader.isRunning()) and self.logger.finished and not self.logger.pending_images and (not self.camera or not self.camera.thread.is_alive())
        if ready:
            self.logger.journal.close()
        if ready and self.logger.journal.finished:
            event.accept()
        else:
            event.ignore()

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
