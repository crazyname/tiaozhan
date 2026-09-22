"""Explicit pre-experiment check. Never sends actuator, scan or calibration commands."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import tempfile
import time
import uuid

from camera_process import capture_process, camera_settings
from schema import HOST_VERSION, finite_number, now_iso, sensor_row

I2C_REQUIRED = {"ads_gas", "sht_ambient", "sht_chamber", "mlx", "bme"}


def evaluate(info, samples, camera, disk, *, expected_mask=7, simulated=False, transport_error=None):
    """Samples are (host monotonic receipt time, original frame), in receipt order."""
    checks = []

    def add(name, state, detail):
        checks.append(dict(name=name, state=state, detail=detail))

    frames = [f for _, f in samples]
    add("transport", "fail" if transport_error else "pass", transport_error or "采集窗口完成")
    identity_keys = ("device_id", "firmware", "hardware", "source_mode")
    identity_complete = frames and all(isinstance(f.get(k), str) and f[k] for f in [info, *frames] for k in identity_keys)
    identity_matches = identity_complete and all(f[k] == info[k] for f in frames for k in identity_keys)
    source = "simulate" if simulated else "hardware"
    add("identity", "pass" if identity_matches and info.get("source_mode") == source else "unknown" if not identity_complete else "fail",
        {k: info.get(k) for k in identity_keys})
    devices = info.get("i2c_present")
    if not isinstance(devices, dict) or not I2C_REQUIRED <= devices.keys():
        add("i2c", "unknown", "固件未提供完整的已配置器件应答信息")
    else:
        add("i2c", "pass" if all(v is True for v in devices.values()) else "fail", devices)
    masks = [f.get("gas_enabled_mask") for f in [info, *frames]]
    add("channels", "unknown" if not frames or any(type(m) is not int for m in masks) else
        "pass" if all(m == expected_mask for m in masks) else "fail", dict(expected=expected_mask, observed=masks))
    calibrated = (info.get("offset_known") is True and finite_number(info.get("hx_offset"))
                  and finite_number(info.get("hx_counts_per_g")) and abs(info["hx_counts_per_g"]) >= .001)
    masses = [f.get("mass_g") for f in frames]
    add("calibration", "unknown" if "offset_known" not in info else "pass" if calibrated and masses and
        all(finite_number(m) and 0 <= m <= 20000 for m in masses) and
        all(f.get("hx_counts_per_g") == info.get("hx_counts_per_g") and f.get("hx_offset") == info.get("hx_offset") for f in frames) else "fail",
        dict(offset_known=info.get("offset_known"), offset=info.get("hx_offset"), counts_per_g=info.get("hx_counts_per_g"),
             note="仅检查已保存参数与当前有效称重；多点精度仍需实物验收"))
    continuous = len(samples) >= 5 and samples[-1][0]-samples[0][0] >= 4
    for index, (host, frame) in enumerate(samples):
        try:
            sensor_row(frame, "", "", host)
        except (ValueError, TypeError):
            continuous = False
        if not finite_number(frame.get("t_ms")) or frame["t_ms"] < 0:
            continuous = False
        if index:
            prev_host, prev = samples[index-1]
            continuous &= (type(frame.get("seq")) is int and type(prev.get("seq")) is int and frame["seq"] == prev["seq"]+1
                           and finite_number(frame.get("t_ms")) and finite_number(prev.get("t_ms")) and 0 < frame["t_ms"]-prev["t_ms"] <= 3000
                           and 0 < host-prev_host <= 3)
    add("continuity", "pass" if continuous else "fail", dict(samples=len(samples), minimum=5, maximum_gap_s=3))
    numeric = ["mass_g", "ambient_t_c", "ambient_rh_pct", "chamber_t_c", "chamber_rh_pct", "leaf_t_c", "bme688_gas_ohm"]
    valid = bool(frames) and all(f.get("quality_flag") == "OK" and all(finite_number(f.get(k)) for k in numeric) for f in frames)
    for frame in frames:
        for field in ("gas_adc", "gas_v"):
            values = frame.get(field)
            valid &= isinstance(values, list) and len(values) == 4 and all(
                finite_number(values[i]) if expected_mask & (1 << i) else values[i] is None for i in range(4))
    add("sensor_readiness", "pass" if valid else "fail", "全部窗口须质量OK、启用通道有数值；预热和标定中不通过")
    known_outputs = bool(frames) and all(all(type(f.get(k)) in (int, bool) and f[k] in (0, 1) for k in ("pump", "valve_sample", "valve_purge"))
                                       and type(f.get("motor_compiled")) is bool and type(f.get("motor_running")) is bool for f in frames)
    off = known_outputs and all(not any(f[k] for k in ("pump", "valve_sample", "valve_purge", "motor_running")) and
                               (not f["motor_compiled"] or (finite_number(f.get("shake_actual_rpm")) and abs(f["shake_actual_rpm"]) < 1
                                and f.get("motor_pwm_duty") == 0 and f.get("motor_fault") == "NONE")) for f in frames)
    add("outputs_off", "unknown" if not known_outputs else "pass" if off else "fail",
        "仅检查窗口内输出命令与编码器静止；不证明上电瞬态、继电器触点或物理急停")
    add("camera", camera.get("state", "unknown"), camera)
    add("disk", disk.get("state", "unknown"), disk)
    return dict(schema="QY-PREFLIGHT-1", created_at=now_iso(), host_version=HOST_VERSION,
                source_mode=source, overall="fail" if any(c["state"] == "fail" for c in checks) else
                "unknown" if any(c["state"] != "pass" for c in checks) else "pass",
                hardware_verified=False, checks=checks,
                limitations="自检不替代传感器校准、实际安全链验收或8小时实机测试；模拟通过仅验证软件流程")


def probe_disk(root, minimum_bytes):
    try:
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(root).free
        with tempfile.TemporaryFile(dir=root) as handle:
            handle.write(b"QY preflight write probe\n"); handle.flush(); os.fsync(handle.fileno())
        return dict(state="pass" if free >= minimum_bytes else "fail", free_bytes=free,
                    minimum_bytes=minimum_bytes, path=str(root.resolve()), note="瞬时空间与小文件写入检查，不是全程容量保证")
    except OSError as exc:
        return dict(state="fail", error=str(exc))


def probe_camera(settings, timeout=8, target=capture_process):
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=target, args=(child, settings), daemon=True)
    try:
        process.start(); child.close()
        parent.send("capture")
        if not parent.poll(timeout):
            raise TimeoutError(f"相机超过{timeout}秒未响应")
        result = parent.recv()
        if not result.get("ok") or not result.get("data"):
            raise OSError(result.get("error", "未返回照片"))
        return dict(state="pass", settings=settings, sha256=hashlib.sha256(result["data"]).hexdigest(),
                    bytes=len(result["data"]), note=result.get("note"),
                    exposure=result.get("exposure") if finite_number(result.get("exposure")) else None,
                    white_balance=result.get("white_balance") if finite_number(result.get("white_balance")) else None)
    except (OSError, EOFError, TimeoutError) as exc:
        return dict(state="fail", settings=settings, error=str(exc))
    finally:
        parent.close(); child.close()
        if process.pid:
            process.join(.2)
            if process.is_alive():
                process.terminate(); process.join(1)
            if process.is_alive():
                process.kill(); process.join(1)
            if process.is_alive():
                raise OSError("相机自检子进程未退出")
            process.close()


def collect(port, baud, duration, raw_path, *, serial_factory=None):
    import serial
    info, samples, error = {}, [], None
    factory = serial_factory or serial.Serial
    # Bounded line reads; preserve even invalid/truncated input for diagnosis.
    with Path(raw_path).open("x", encoding="utf-8") as log:
        try:
            with factory(port, baud, timeout=.25, write_timeout=1) as connection:
                payload = b'{"cmd":"info"}\n'
                if connection.write(payload) != len(payload):
                    raise OSError("info写入不完整")
                deadline = time.monotonic() + duration
                retried_info = False
                buffer = bytearray()
                while time.monotonic() < deadline:
                    chunk = connection.read_until(b"\n", 16384)
                    if not chunk:
                        continue
                    mono = time.monotonic()
                    log.write(json.dumps(dict(host_monotonic_s=mono, host_time_iso=now_iso(), data_b64=base64.b64encode(chunk).decode("ascii")))+"\n")
                    buffer.extend(chunk)
                    if len(buffer) > 16384:
                        raise OSError("串口行超过16KiB")
                    if not buffer.endswith(b"\n"):
                        continue
                    try:
                        def reject_constant(value):
                            raise ValueError(f"非有限JSON数值: {value}")
                        def parse_finite(value):
                            number = float(value)
                            if not finite_number(number):
                                reject_constant(value)
                            return number
                        frame = json.loads(buffer.decode("utf-8"), parse_constant=reject_constant, parse_float=parse_finite)
                        if not isinstance(frame, dict):
                            raise ValueError("非对象JSON")
                        if frame.get("type") == "sensor":
                            samples.append((mono, frame))
                            # USB opening may reset a board and discard the initial query.
                            # Retry only the read-only query after the first sensor frame.
                            if not info and not retried_info:
                                if connection.write(payload) != len(payload):
                                    raise OSError("info重查写入不完整")
                                retried_info = True
                        elif frame.get("message") == "firmware configuration":
                            info = frame
                    except (ValueError, UnicodeError) as exc:
                        error = f"窗口存在无法解析的帧: {exc}"
                    buffer.clear()
                if buffer:
                    error = "采集窗口结束时存在不完整串口行"
                if not samples or time.monotonic()-samples[-1][0] > 3:
                    error = "窗口结束时设备遥测缺失或已超过3秒未更新"
        except (OSError, serial.SerialException) as exc:
            error = str(exc)
    return info, samples, error


def main(argv=None):
    parser = argparse.ArgumentParser(description="实验前自检；先关闭占用串口/相机的上位机。仅发送info，不改变执行器。")
    parser.add_argument("--port", required=True)
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--seconds", type=float, default=12)
    parser.add_argument("--expected-mask", type=int, choices=range(1, 16), default=7)
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--exposure", type=float)
    parser.add_argument("--white-balance", type=float)
    parser.add_argument("--skip-camera", action="store_true", help="相机标记未知，总体不通过")
    parser.add_argument("--minimum-free-gib", type=float, default=2)
    parser.add_argument("--data-root", type=Path, default=Path(__file__).resolve().parents[2]/"data"/"raw",
                        help="实际批次保存目录；磁盘检查针对该目录")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[2]/"data"/"preflight")
    args = parser.parse_args(argv)
    if not finite_number(args.seconds) or not 5 <= args.seconds <= 120 or not finite_number(args.minimum_free_gib) or args.minimum_free_gib <= 0:
        parser.error("采集时长须为5～120秒，最小剩余空间须为正有限值")
    settings = camera_settings(dict(index=args.camera_index, exposure=args.exposure, white_balance=args.white_balance))
    run = args.output / (time.strftime("%Y%m%d_%H%M%S")+"_"+uuid.uuid4().hex[:8])
    run.mkdir(parents=True, exist_ok=False)
    (run / "INCOMPLETE").write_text("自检尚未完成", encoding="utf-8")
    disk = probe_disk(args.data_root, int(args.minimum_free_gib*1024**3))
    camera = dict(state="unknown", note="操作员跳过相机检查") if args.skip_camera else probe_camera(settings)
    info, samples, error = collect(args.port, args.baud, args.seconds, run/"raw_serial.jsonl")
    report = evaluate(info, samples, camera, disk, expected_mask=args.expected_mask, transport_error=error)
    report.update(port=args.port, baud=args.baud, settings=settings, info=info, samples=samples,
                  raw_sha256=hashlib.sha256((run/"raw_serial.jsonl").read_bytes()).hexdigest())
    with (run/"report.json").open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
    (run/"INCOMPLETE").unlink()
    print(json.dumps(dict(overall=report["overall"], report=str(run/"report.json"), checks=report["checks"]), ensure_ascii=False, indent=2))
    return 0 if report["overall"] == "pass" else 2


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
