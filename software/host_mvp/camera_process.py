"""Native camera backend in a disposable process; no disk access or Qt objects."""
from __future__ import annotations
import math
import time
from datetime import datetime


def camera_settings(values=None):
    values = dict(values or {})
    if values.keys() - {"index", "exposure", "white_balance"}:
        raise ValueError("未知相机参数")
    index = values.get("index", 0)
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index <= 20:
        raise ValueError("相机编号必须为0～20的整数")
    result = dict(index=index)
    for key in ("exposure", "white_balance"):
        value = values.get(key)
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
                raise ValueError("相机参数必须是有限数值或留空")
        result[key] = value
    return result


def capture_process(connection, settings):
    import cv2
    camera = None
    try:
        while connection.recv() == "capture":
            try:
                if camera is None:
                    camera = cv2.VideoCapture(settings["index"], cv2.CAP_DSHOW)
                    if not camera.isOpened():
                        raise OSError("相机无法打开或被占用")
                    camera.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
                    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
                    for key, auto, prop in [("exposure", cv2.CAP_PROP_AUTO_EXPOSURE, cv2.CAP_PROP_EXPOSURE),
                                             ("white_balance", cv2.CAP_PROP_AUTO_WB, cv2.CAP_PROP_WB_TEMPERATURE)]:
                        if settings[key] is not None:
                            if not camera.set(auto, 0) or not camera.set(prop, settings[key]):
                                raise OSError(f"相机拒绝固定 {key}，请检查驱动支持及单位")
                ok, frame = camera.read()
                if not ok or frame is None:
                    raise OSError("相机读取失败")
                iso, mono = datetime.now().astimezone().isoformat(timespec="milliseconds"), time.monotonic()
                ok, encoded = cv2.imencode(".jpg", frame)
                if not ok:
                    raise OSError("照片编码失败")
                actual = dict(exposure=camera.get(cv2.CAP_PROP_EXPOSURE), white_balance=camera.get(cv2.CAP_PROP_WB_TEMPERATURE),
                              auto_exposure=camera.get(cv2.CAP_PROP_AUTO_EXPOSURE), auto_white_balance=camera.get(cv2.CAP_PROP_AUTO_WB))
                for key in ("auto_exposure", "auto_white_balance"):
                    if actual[key] not in (0, 1):
                        actual[key] = None  # DirectShow can return -1 for unsupported getters.
                for key, auto_key in [("exposure", "auto_exposure"), ("white_balance", "auto_white_balance")]:
                    if settings[key] is not None and (not math.isfinite(actual[key]) or abs(actual[key]-settings[key]) > 1e-3 or actual[auto_key] == 1):
                        raise OSError(f"相机 {key} 请求与驱动读回不一致，未保存为成功照片")
                connection.send(dict(ok=True, data=encoded.tobytes(), datetime_iso=iso, host_monotonic_s=mono,
                                     exposure=actual["exposure"], white_balance=actual["white_balance"],
                                     note=f"requested={settings}; driver_readback={actual}; 不代表光学标定"))
            except Exception as exc:
                connection.send(dict(ok=False, error=str(exc)))
                break
    except (EOFError, BrokenPipeError, OSError):
        pass
    finally:
        if camera is not None:
            camera.release()  # Parent also bounds native release() hangs.
        connection.close()
