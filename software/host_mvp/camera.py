from __future__ import annotations
import queue
import threading
import time
import multiprocessing
import cv2
import numpy as np
from PySide6 import QtCore
from schema import now_iso
from camera_process import capture_process, camera_settings

class CameraWorker(QtCore.QObject):
    result = QtCore.Signal(dict)

    def __init__(self, logger, simulate=False, capture_factory=None, index=0, timeout=8, process_target=None):
        super().__init__()
        self.logger, self.simulate, self.index = logger, simulate, index
        self.factory = capture_factory  # Synchronous fake backend for unit tests only.
        self.timeout = timeout
        self.process_target = process_target or capture_process
        self.process = self.connection = None
        self.settings = None
        self.queue = queue.Queue(8)
        self.closing = threading.Event()
        self.thread = threading.Thread(target=self._run, name="camera-worker", daemon=True)
        self.thread.start()

    def request(self, event_id=""):
        job = self.logger.image_request(event_id, "simulate" if self.simulate else "camera")
        if job is None:
            return False
        try:
            if self.closing.is_set():
                raise queue.Full
            self.queue.put_nowait(job)
            return True
        except queue.Full:
            self.logger.image_result(job, dict(ok=False, error="相机队列已满或正在关闭"))
            self.result.emit(dict(ok=False, error="相机队列已满或正在关闭"))
            return False

    def close(self):
        self.closing.set()

    def _dispose_process(self):
        if self.connection:
            self.connection.close(); self.connection = None
        if self.process:
            self.process.join(.2)
            if self.process.is_alive():
                self.process.terminate(); self.process.join(1)
            if self.process.is_alive():
                self.process.kill(); self.process.join(1)
            if self.process.is_alive():
                raise OSError("相机子进程无法退出，请检查系统")
            self.process.close(); self.process = None

    def _capture_isolated(self, settings):
        if self.process is not None and (not self.process.is_alive() or settings != self.settings):
            self._dispose_process()
        if self.process is None:
            context = multiprocessing.get_context("spawn")
            self.connection, child = context.Pipe()
            self.process = context.Process(target=self.process_target, args=(child, settings), daemon=True)
            self.process.start(); child.close(); self.settings = settings
        self.connection.send("capture")
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if self.closing.is_set():
                raise OSError("相机关闭，拍照任务取消")
            if self.connection.poll(.05):
                result = self.connection.recv()
                if not result.get("ok"):
                    raise OSError(result.get("error", "相机子进程失败"))
                return result
            if not self.process.is_alive():
                raise OSError("相机子进程异常退出")
        raise TimeoutError(f"相机驱动超过 {self.timeout} 秒未返回，已终止并可在下次拍照重建")

    def _run(self):
        camera = None
        try:
            while not (self.closing.is_set() and self.queue.empty()):
                try:
                    job = self.queue.get(timeout=0.05)
                except queue.Empty:
                    continue
                try:
                    if self.closing.is_set():
                        raise OSError("相机关闭，拍照任务取消")
                    if not self.simulate and self.factory is None:
                        result = self._capture_isolated(camera_settings(job.get("camera_settings")))
                        data = result.pop("data")
                        (job["root"] / job["filename"]).write_bytes(data)
                        result["preview"] = data
                        self.logger.image_result(job, result)
                        self.result.emit(dict(result, token=job["token"], filename=job["filename"]))
                        continue
                    if self.simulate:
                        frame = np.full((480, 640, 3), (48, 72, 48), np.uint8)
                        cv2.putText(frame, "SIMULATION - NOT EXPERIMENT DATA", (15, 220), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)
                        cv2.putText(frame, now_iso()[:19], (15, 260), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 1)
                        exposure = white_balance = ""
                    else:
                        if camera is None:
                            camera = self.factory(self.index, cv2.CAP_DSHOW)
                            camera.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
                            camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
                        ok, frame = camera.read()
                        if not ok or frame is None:
                            raise OSError("相机读取失败")
                        exposure = camera.get(cv2.CAP_PROP_EXPOSURE)
                        white_balance = camera.get(cv2.CAP_PROP_WB_TEMPERATURE)
                    iso, mono = now_iso(), time.monotonic()
                    ok, encoded = cv2.imencode(".jpg", frame)
                    if not ok:
                        raise OSError("照片编码失败")
                    data = encoded.tobytes()
                    (job["root"] / job["filename"]).write_bytes(data)
                    result = dict(ok=True, datetime_iso=iso, host_monotonic_s=mono, exposure=exposure,
                                  white_balance=white_balance, note="模拟图像" if self.simulate else "", preview=data)
                except Exception as exc:
                    result = dict(ok=False, error=str(exc))
                    self._dispose_process()
                    if camera is not None:
                        try:
                            camera.release()
                        except Exception:
                            pass
                        camera = None
                self.logger.image_result(job, result)
                self.result.emit(dict(result, token=job["token"], filename=job["filename"]))
        finally:
            self._dispose_process()
            if camera is not None:
                camera.release()
