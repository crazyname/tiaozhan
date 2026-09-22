from __future__ import annotations
import queue
import threading
import time
import cv2
import numpy as np
from PySide6 import QtCore
from schema import now_iso

class CameraWorker(QtCore.QObject):
    result = QtCore.Signal(dict)

    def __init__(self, logger, simulate=False, capture_factory=None, index=0):
        super().__init__()
        self.logger, self.simulate, self.index = logger, simulate, index
        self.factory = capture_factory or cv2.VideoCapture
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

    def _run(self):
        camera = None
        try:
            while not (self.closing.is_set() and self.queue.empty()):
                try:
                    job = self.queue.get(timeout=0.05)
                except queue.Empty:
                    continue
                try:
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
                    if camera is not None:
                        try:
                            camera.release()
                        except Exception:
                            pass
                        camera = None
                self.logger.image_result(job, result)
                self.result.emit(dict(result, token=job["token"], filename=job["filename"]))
        finally:
            if camera is not None:
                camera.release()
