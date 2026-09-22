"""Offline replay window; it has no device or writer dependency."""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg
from replay import BatchSnapshot, SERIES


class Loader(QtCore.QThread):
    loaded = QtCore.Signal(object)
    failed = QtCore.Signal(str)

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = path

    def run(self):
        try:
            self.loaded.emit(BatchSnapshot(self.path))
        except Exception as exc:
            self.failed.emit(str(exc))


class ReportWorker(QtCore.QThread):
    saved = QtCore.Signal(str)
    failed = QtCore.Signal(str)

    def __init__(self, roots, output, params, parent=None):
        super().__init__(parent)
        self.roots, self.output, self.params = roots, output, params

    def run(self):
        from analysis import export_report
        try:
            self.saved.emit(str(export_report(self.roots, self.output, self.params)))
        except Exception as exc:
            self.failed.emit(str(exc))


class ReportDialog(QtWidgets.QDialog):
    def __init__(self, field, parent=None):
        super().__init__(parent)
        self.setWindowTitle("分析参数 · 所有已添加批次（按批次起点）")
        layout = QtWidgets.QFormLayout(self)
        self.windows = []
        for title, value in [("基线起（秒）", 0), ("基线止（秒）", 60), ("稳定起（秒）", 60), ("稳定止（秒）", 120)]:
            entry = QtWidgets.QDoubleSpinBox(); entry.setRange(0, 1e9); entry.setValue(value)
            self.windows.append(entry); layout.addRow(title, entry)
        self.field = QtWidgets.QComboBox()
        for key, title in SERIES.items():
            if key != "loss_pct":
                self.field.addItem(title, key)
        self.field.setCurrentIndex(max(0, self.field.findData(field))); layout.addRow("漂移指标", self.field)
        self.group = QtWidgets.QLineEdit(); self.group.setPlaceholderText("仅确认相同重复实验条件后填写；未知留空")
        layout.addRow("重复实验组", self.group)
        self.error = QtWidgets.QLabel("半开区间；默认仅允许 OK / SERIAL_GAP；其他质量标记排除。")
        self.error.setWordWrap(True); layout.addRow(self.error)
        button = QtWidgets.QPushButton("选择导出目录")
        button.clicked.connect(self.validate); layout.addRow(button)

    def parameters(self):
        from analysis import Parameters
        return Parameters(*(entry.value() for entry in self.windows), field=self.field.currentData(), repeat_group=self.group.text().strip())

    def validate(self):
        try:
            self.parameters().validate(); self.accept()
        except ValueError as exc:
            self.error.setText(str(exc))


class ReplayWindow(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("历史批次回放与对比 · 只读")
        self.resize(1120, 800)
        self.batches = []
        self.loader = None
        self.report_worker = None
        self.anchors = {}
        layout = QtWidgets.QVBoxLayout(self)
        controls = QtWidgets.QHBoxLayout()
        self.add = QtWidgets.QPushButton("添加批次")
        self.add.clicked.connect(self.choose)
        self.export = QtWidgets.QPushButton("导出分析报告")
        self.export.clicked.connect(self.export_analysis)
        self.active = QtWidgets.QComboBox()
        self.field = QtWidgets.QComboBox()
        for key, title in SERIES.items():
            self.field.addItem(title, key)
        self.align = QtWidgets.QComboBox()
        self.align.addItem("按批次起点", None)
        self.occurrence = QtWidgets.QSpinBox(); self.occurrence.setRange(1, 999)
        self.occurrence.setPrefix("第 "); self.occurrence.setSuffix(" 次事件")
        for widget in (self.add, self.active, self.field, self.align, self.occurrence, self.export):
            controls.addWidget(widget)
        layout.addLayout(controls)
        self.plot = pg.PlotWidget()
        self.plot.addLegend(); self.plot.setLabel("bottom", "对齐时间", units="s")
        layout.addWidget(self.plot, 1)
        self.cursor = pg.InfiniteLine(angle=90, movable=False)
        playback = QtWidgets.QHBoxLayout()
        self.play = QtWidgets.QPushButton("播放")
        self.play.clicked.connect(self.toggle_play)
        self.position = QtWidgets.QDoubleSpinBox()
        self.position.setDecimals(2); self.position.setSuffix(" s"); self.position.setSingleStep(1)
        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal); self.slider.setRange(0, 10000)
        self.slider.valueChanged.connect(self.scrub)
        self.speed = QtWidgets.QComboBox()
        for multiplier in (1, 5, 30, 60):
            self.speed.addItem(f"{multiplier}×", multiplier)
        for widget in (self.play, self.position, self.slider, self.speed):
            playback.addWidget(widget)
        layout.addLayout(playback)
        middle = QtWidgets.QHBoxLayout()
        self.photo = QtWidgets.QLabel("无照片"); self.photo.setFixedSize(300, 200)
        self.photo.setAlignment(QtCore.Qt.AlignCenter); middle.addWidget(self.photo)
        self.detail = QtWidgets.QPlainTextEdit(); self.detail.setReadOnly(True)
        middle.addWidget(self.detail); layout.addLayout(middle)
        self.events = QtWidgets.QTableWidget(0, 4)
        self.events.setHorizontalHeaderLabels(["对齐秒", "事件", "编号", "备注"])
        self.events.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        self.events.horizontalHeader().setStretchLastSection(True)
        self.events.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.events.setMaximumHeight(150)
        self.events.cellClicked.connect(self.jump_event); layout.addWidget(self.events)
        self.warnings = QtWidgets.QPlainTextEdit(); self.warnings.setReadOnly(True)
        self.warnings.setMaximumHeight(100); layout.addWidget(self.warnings)
        self.active.currentIndexChanged.connect(self.refresh)
        self.field.currentIndexChanged.connect(self.refresh)
        self.align.currentIndexChanged.connect(self.refresh)
        self.occurrence.valueChanged.connect(self.refresh)
        self.position.valueChanged.connect(self.show_position)
        self.timer = QtCore.QTimer(self); self.timer.setInterval(100)
        self.timer.timeout.connect(self.advance)
        self.elapsed = QtCore.QElapsedTimer()

    def choose(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "选择含 meta.yaml 的批次目录")
        if path:
            self.load_path(path)

    def export_analysis(self):
        if not self.batches or (self.report_worker and self.report_worker.isRunning()):
            self.warnings.setPlainText("请先添加批次，或等待当前报告完成")
            return
        dialog = ReportDialog(self.field.currentData(), self)
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        output = QtWidgets.QFileDialog.getExistingDirectory(self, "选择原始批次之外的报告父目录")
        if output:
            self.export.setEnabled(False)
            self.report_worker = ReportWorker([batch.root for batch in self.batches], output, dialog.parameters(), self)
            self.report_worker.saved.connect(lambda path: self.warnings.setPlainText("报告已保存（report.html / report.json / curves.csv / curves.png）：\n" + path))
            self.report_worker.failed.connect(self.warnings.setPlainText)
            self.report_worker.finished.connect(lambda: self.export.setEnabled(True))
            self.report_worker.start()

    def load_path(self, path):
        if self.loader and self.loader.isRunning():
            return
        self.add.setEnabled(False)
        self.loader = Loader(path, self)
        self.loader.loaded.connect(self.add_batch)
        self.loader.failed.connect(self.warnings.setPlainText)
        self.loader.finished.connect(lambda: self.add.setEnabled(True))
        self.loader.start()

    def add_batch(self, batch):
        if any(old.root == batch.root for old in self.batches):
            self.warnings.setPlainText("该批次已添加；重新打开回放窗口可获取新快照。")
            return
        self.batches.append(batch)
        self.active.blockSignals(True)
        self.active.addItem(f"{batch.root.name} [{batch.source}]")
        self.active.setCurrentIndex(len(self.batches)-1); self.active.blockSignals(False)
        known = {self.align.itemData(i) for i in range(self.align.count())}
        self.align.blockSignals(True)
        for kind in sorted({r.values.get("event_type") for r in batch.events if r.values.get("event_type")} - known):
            self.align.addItem(kind, kind)
        self.align.blockSignals(False)
        self.refresh()

    def refresh(self):
        self.timer.stop(); self.play.setText("播放")
        self.plot.clear(); self.plot.addItem(self.cursor)
        self.anchors = {}
        notices = []
        lower = upper = 0.0
        field = self.field.currentData()
        for i, batch in enumerate(self.batches):
            notices.extend(f"{batch.root.name}: {warning}" for warning in batch.warnings)
            try:
                anchor = batch.anchor(self.align.currentData(), self.occurrence.value())
                self.anchors[i] = anchor
            except ValueError as exc:
                notices.append(str(exc) + "；该批次未参与当前对齐")
                continue
            x, y = batch.series(field, anchor)
            times = [r.time-anchor for records in batch.tables.values() for r in records]
            if times:
                lower = min(lower, min(times)); upper = max(upper, max(times))
            self.plot.plot(x, y, pen=pg.intColor(i), name=f"{batch.root.name} [{batch.source}]", connect="finite")
        self.plot.setLabel("left", SERIES[field])
        self.position.setRange(lower, upper)
        self.warnings.setPlainText("\n".join(notices) or "只读快照；simulate/mock 为模拟数据，来源 hardware 也不代表通过实物验收。")
        self.events.setRowCount(0)
        index = self.active.currentIndex()
        if index in self.anchors:
            for event in self.batches[index].events:
                row = self.events.rowCount(); self.events.insertRow(row)
                values = [f"{event.time-self.anchors[index]:.3f}", event.values.get("event_type", ""),
                          event.values.get("event_id", ""), event.values.get("note", "")]
                for column, value in enumerate(values):
                    self.events.setItem(row, column, QtWidgets.QTableWidgetItem(value))
        self.show_position()

    def scrub(self, value):
        span = self.position.maximum()-self.position.minimum()
        self.position.setValue(self.position.minimum()+span*value/10000)

    def show_position(self):
        seconds = self.position.value(); self.cursor.setValue(seconds)
        span = self.position.maximum()-self.position.minimum()
        self.slider.blockSignals(True)
        self.slider.setValue(round((seconds-self.position.minimum())/span*10000) if span else 0)
        self.slider.blockSignals(False)
        index = self.active.currentIndex()
        self.photo.clear()
        if index not in self.anchors:
            self.detail.setPlainText("当前批次无法按所选事件对齐")
            return
        batch = self.batches[index]; absolute = seconds + self.anchors[index]
        lines = [f"批次: {batch.root.name} | 来源: {batch.source}", f"批次相对时间: {absolute:.3f} s"]
        for table, title in [("sensors", "传感器"), ("events", "最近事件"), ("labels", "最近标签/修订")]:
            record = batch.at(table, absolute)
            lines.append(f"\n{title}: " + (f"t={record.time:.3f}s CSV记录 {record.line}" if record else "暂无"))
            if record:
                lines.extend(f"{key}: {value}" for key, value in record.values.items() if key != "device_frame_json")
        image = batch.at("images", absolute)
        path = batch.image_path(image) if image else None
        if path:
            pixmap = QtGui.QPixmap(str(path))
            if pixmap.isNull():
                self.photo.setText("图像解码失败")
            else:
                self.photo.setPixmap(pixmap.scaled(self.photo.size(), QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
            lines.append(f"\n照片: {image.values.get('filename')} | t={image.time:.3f}s")
        else:
            self.photo.setText("当前时刻无照片 / 文件缺失")
        self.detail.setPlainText("\n".join(lines))

    def jump_event(self, row, column):
        self.position.setValue(float(self.events.item(row, 0).text()))

    def toggle_play(self):
        if self.timer.isActive():
            self.timer.stop(); self.play.setText("播放")
        elif self.batches:
            if self.position.value() >= self.position.maximum():
                self.position.setValue(self.position.minimum())
            self.elapsed.start(); self.timer.start(); self.play.setText("暂停")

    def advance(self):
        step = self.elapsed.restart()/1000*self.speed.currentData()
        self.position.setValue(min(self.position.maximum(), self.position.value()+step))
        if self.position.value() >= self.position.maximum():
            self.timer.stop(); self.play.setText("播放")

    def closeEvent(self, event):
        if self.busy():
            self.warnings.setPlainText("正在读取批次或导出报告，请完成后关闭")
            event.ignore()
        else:
            self.timer.stop(); event.accept()

    def busy(self):
        return bool((self.loader and self.loader.isRunning()) or (self.report_worker and self.report_worker.isRunning()))
