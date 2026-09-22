"""Single-owner serial transport, command tracking and simulator."""
from __future__ import annotations
import json
import math
import queue
import threading
import time
import uuid

import serial
from PySide6 import QtCore
from schema import now_iso, sensor_row, finite_number

class CommandTracker:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.RLock()
        self.pending = None
        self.uncertain = False
        self.number = 0

    def start(self, payload):
        with self.lock:
            cmd = payload.get("cmd")
            if cmd not in ("info", "tare", "calibrate", "air", "stop", "motor", "motor_stop", "motor_reset"):
                raise ValueError("不支持的命令")
            if self.pending and cmd not in ("stop", "motor_stop"):
                raise ValueError("等待当前命令完成")
            if self.uncertain and cmd not in ("info", "stop", "motor_stop"):
                raise ValueError("上次操作结果未知，请确认设备后重新连接；不会自动重发")
            if cmd in ("stop", "motor_stop") and self.pending:
                self.uncertain = True
            self.number += 1
            self.pending = dict(request_id=self.number, payload=dict(payload), phase="queued",
                                deadline=self.clock() + (15 if cmd in ("tare", "calibrate") else 4))
            if cmd.startswith("motor"):
                self.pending["payload"]["request_id"] = self.number
            return dict(self.pending)

    def status(self, frame):
        with self.lock:
            if not self.pending:
                return None
            cmd = self.pending["payload"]["cmd"]
            message = frame.get("message", "")
            recognized = False
            phase = "completed"
            if cmd.startswith("motor"):
                recognized = frame.get("cmd") == cmd and frame.get("request_id") == self.pending["request_id"]
                phase = "accepted"
            elif cmd == "info":
                recognized = message == "firmware configuration"
            elif cmd == "stop":
                recognized = message == "air stopped"
            elif cmd == "air":
                recognized = message.startswith(("timed air command", "air disabled", "air requires", "unknown air mode"))
                phase = "accepted"  # Acceptance is not valve/flow feedback or run completion.
            else:
                recognized = message.startswith(("collecting 20", "calibration saved", "calibration rejected", "calibration timeout",
                                                  "tare first", "NVS unavailable", "calibration already"))
                if message.startswith("collecting 20"):
                    self.pending["phase"] = "running"
                    return dict(self.pending, phase="running", response=frame)
            if not recognized:
                return None
            if frame.get("ok") is False:
                phase = "rejected"
            result = dict(self.pending, phase=phase, response=frame)
            self.pending = None
            return result

    def abort(self, reason):
        with self.lock:
            self.uncertain = True
            if self.pending:
                result = dict(self.pending, phase="unknown", error=reason)
                self.pending = None
                return result
            return None

    def timeout(self):
        with self.lock:
            if self.pending and self.clock() >= self.pending["deadline"]:
                if self.pending["payload"]["cmd"] == "info":
                    result = dict(self.pending, phase="unknown", error="信息查询超时；旧设备可能不支持该命令")
                    self.pending = None
                    return result
                return self.abort("命令超时，结果未知，未重试")
            return None

class SensorReader(QtCore.QThread):
    sensor = QtCore.Signal(dict)
    status = QtCore.Signal(str)
    error = QtCore.Signal(str)
    command_result = QtCore.Signal(dict)

    def __init__(self, port, baud, simulate=False, logger=None, serial_factory=None):
        super().__init__()
        self.port, self.baud, self.simulate, self.logger = port, baud, simulate, logger
        self.serial_factory = serial_factory or serial.Serial
        self._stop = threading.Event()
        self.commands = queue.Queue(8)
        self.tracker = CommandTracker()
        self.connected = False
        self.identity = None
        self.latest = {}
        self.sim_air_until = 0
        self.sim_air_mode = "off"
        self.sim_cal_until = 0
        self.sim_calibrated = True
        self.last_seq = None
        self.motor_session = uuid.uuid4().hex
        self.motor_lease_active = False
        self.operator_alive = time.monotonic()
        self.last_motor_lease = 0
        self.latest_received = 0
        self.sim_motor = dict(running=False, target=0, direction=1, started=0, ended=0, duration=0, lease=0, fault="NONE")

    def log(self, kind, payload):
        if self.logger:
            self.logger.log(kind, payload)

    def result(self, result):
        if result:
            cmd = result.get("payload", {}).get("cmd")
            if cmd == "motor" and result["phase"] == "accepted":
                self.motor_lease_active = True
            elif cmd == "motor_stop" or result["phase"] == "unknown":
                self.motor_lease_active = False
            self.log("commands", result)
            self.command_result.emit(result)

    def submit(self, payload):
        if not self.connected:
            raise ValueError("设备未连接")
        if payload["cmd"] in ("tare", "calibrate") and self.logger and self.logger.active:
            raise ValueError("批次采集中禁止修改称重标定，请先结束批次")
        if payload["cmd"] in ("tare", "calibrate") and self.latest.get("motor_running"):
            raise ValueError("请先停止滚筒并确认静止再标定")
        if payload["cmd"] in ("motor", "motor_reset"):
            if not self.latest.get("motor_compiled") or time.monotonic()-self.latest_received > 3:
                raise ValueError("设备未报告可用滚筒固件或遥测过期")
            if "CALIBRATING" in str(self.latest.get("quality_flag", "")):
                raise ValueError("标定期间禁止滚筒动作")
        if payload["cmd"] == "motor":
            if not finite_number(payload.get("rpm")) or not 5 <= payload["rpm"] <= 30 or type(payload.get("direction")) is not int or payload["direction"] not in (-1, 1) or type(payload.get("duration_ms")) is not int or not 1000 <= payload["duration_ms"] <= 300000:
                raise ValueError("滚筒参数：5～30RPM、方向±1、时长1～300秒")
            payload = dict(payload, session_id=self.motor_session)
        if payload["cmd"] == "air" and "CALIBRATING" in str(self.latest.get("quality_flag", "")):
            raise ValueError("设备正在标定")
        if payload["cmd"] == "calibrate" and not 0 < float(payload.get("grams", 0)) <= 20000:
            raise ValueError("砝码质量必须在0至20000g之间")
        if payload["cmd"] == "air" and (payload.get("mode") not in ("sample", "purge") or
                type(payload.get("duration_ms")) is not int or not 1000 <= payload["duration_ms"] <= 120000):
            raise ValueError("气路模式或时长无效")
        # Never leave a queued action ahead of an operator STOP.
        if payload["cmd"] in ("stop", "motor_stop"):
            if self.tracker.pending:
                self.result(self.tracker.abort("被停止命令中断，原操作结果需确认"))
            self._clear_commands()
        record = self.tracker.start(payload)
        self.commands.put_nowait(record)
        self.result(record)
        return record

    def _clear_commands(self):
        while True:
            try:
                self.commands.get_nowait()
            except queue.Empty:
                return

    def stop(self):
        self.motor_lease_active = False
        self._stop.set()  # Only the run thread closes the serial handle.

    def _mock_frame(self, seq):
        t = time.monotonic()
        adc = [int(15000 + i * 2000 + 600 * math.sin(t / (15 + i * 3))) for i in range(4)]
        moving = t < self.sim_air_until
        motor = self.sim_motor
        if motor["running"] and (t-motor["lease"] >= 1.2 or t-motor["started"] >= motor["duration"]):
            motor["running"] = False; motor["ended"] = t
            if t-motor["lease"] >= 1.2:
                motor["fault"] = "HOST_TIMEOUT"
        return dict(type="sensor", seq=seq, t_ms=int(t * 1000), gas_adc=adc, gas_v=[v * 4.096 / 32768 for v in adc],
                    bme688_gas_ohm=183000, chamber_t_c=27.2, chamber_rh_pct=71.0, ambient_t_c=26.8,
                    ambient_rh_pct=69.0, leaf_t_c=26.5, mass_g=4000 - seq * 0.012 if self.sim_calibrated else None,
                    pump=int(moving), valve_sample=int(moving and self.sim_air_mode == "sample"),
                    valve_purge=int(moving and self.sim_air_mode == "purge"), source_mode="simulate",
                    firmware="HOST-SIM-0.3", device_id="SIMULATOR", gas_enabled_mask=15,
                    motor_compiled=True, motor_interlock=True, motor_running=motor["running"], motor_fault=motor["fault"],
                    shake_target_rpm=motor["target"], shake_actual_rpm=motor["target"]*motor["direction"] if motor["running"] else 0,
                    shake_direction=motor["direction"], shake_start_time=int(motor["started"]*1000), shake_end_time=int(motor["ended"]*1000),
                    shake_duration_s=motor["duration"], motor_current_a=None, shake_time_kind="mcu_uptime_ms",
                    quality_flag="CALIBRATING" if self.sim_cal_until else "OK")

    def consume(self, raw):
        iso, mono = now_iso(), time.monotonic()
        lock = self.logger.lock if self.logger else threading.RLock()
        with lock:
            if self.logger:
                self.logger.raw(raw, iso, mono)
            try:
                frame = json.loads(raw.decode("utf-8"))
                if not isinstance(frame, dict):
                    raise ValueError("JSON根节点必须是对象")
                if frame.get("type") == "sensor":
                    if self.last_seq is not None and isinstance(frame.get("seq"), int) and frame["seq"] <= self.last_seq:
                        self.result(self.tracker.abort("设备复位或序号倒退"))
                        self.log("device_health", dict(kind="DEVICE_RESET_OR_REORDER"))
                    ident = frame.get("device_id")
                    if self.identity and ident and ident != self.identity and self.logger and self.logger.active:
                        self.logger.writer.fail("设备身份改变，终止当前批次")
                        self.logger.stop(wait=False)
                    if ident:
                        self.identity = ident
                    row = self.logger.ingest(frame, iso, mono) if self.logger else sensor_row(frame, "", iso, mono)
                    self.last_seq = frame["seq"]
                    self.latest = frame
                    self.latest_received = mono
                    if frame.get("motor_running") is False:
                        self.motor_lease_active = False
                    self.sensor.emit(dict(frame=frame, row=row, received_mono=mono))
                elif frame.get("type") == "status":
                    self.log("device_health", dict(kind="DEVICE_STATUS", frame=frame))
                    self.result(self.tracker.status(frame))
                    self.status.emit(str(frame.get("message", "status")))
            except (ValueError, TypeError, UnicodeError) as exc:
                self.log("device_health", dict(kind="PARSE_OR_SCHEMA_ERROR", error=str(exc)))
                self.error.emit(str(exc))

    def _simulate_command(self, payload):
        cmd = payload["cmd"]
        response = dict(type="status", ok=True)
        if cmd == "info":
            response.update(message="firmware configuration", firmware="HOST-SIM-0.3", source_mode="simulate", air_enable=True)
        elif cmd.startswith("motor"):
            motor = self.sim_motor
            response.update(cmd=cmd, request_id=payload.get("request_id"), message="motor command accepted; inspect encoder telemetry")
            if cmd == "motor":
                if motor["running"] or motor["fault"] != "NONE":
                    response.update(ok=False, message="motor rejected: running or fault")
                else:
                    motor.update(running=True, target=payload["rpm"], direction=payload["direction"],
                                 duration=payload["duration_ms"]/1000, started=time.monotonic(), ended=0, lease=time.monotonic())
            elif cmd == "motor_stop":
                motor.update(running=False, ended=time.monotonic())
            elif cmd == "motor_reset":
                if motor["running"]:
                    response.update(ok=False, message="motor rejected: running")
                else:
                    motor["fault"] = "NONE"
        elif cmd == "stop":
            self.sim_air_until = 0
            response["message"] = "air stopped"
        elif cmd == "air":
            self.sim_air_mode = payload["mode"]
            self.sim_air_until = time.monotonic() + payload["duration_ms"] / 1000
            response["message"] = "timed air command accepted"
        else:
            self.sim_cal_until = time.monotonic() + 0.5
            response["message"] = "collecting 20 fresh HX711 readings; keep platform stationary"
        self.consume((json.dumps(response) + "\n").encode())

    def run(self):
        seq, next_frame = 0, 0
        while not self._stop.is_set():
            connection = None
            buffered = bytearray()
            try:
                if not self.simulate:
                    connection = self.serial_factory(self.port, self.baud, timeout=0.1, write_timeout=0.5)
                # Install handshake before exposing connected=True to the UI/test thread.
                handshake = self.tracker.start({"cmd": "info"})
                self.commands.put_nowait(handshake)
                self.result(handshake)
                self.connected = True
                self.log("device_health", dict(kind="CONNECTED", port=self.port, simulate=self.simulate))
                self.status.emit("SIMULATE" if self.simulate else f"CONNECTED {self.port}")
                # Information query is the only automatic command. Actions are never replayed.
                while not self._stop.is_set():
                    self.result(self.tracker.timeout())
                    try:
                        item = self.commands.get_nowait()
                    except queue.Empty:
                        item = None
                    if item:
                        # A timed-out queued command must never execute later.
                        with self.tracker.lock:
                            valid = self.tracker.pending and self.tracker.pending["request_id"] == item["request_id"]
                        if valid and not self._stop.is_set():
                            self.result(dict(item, phase="sent"))
                            if self.simulate:
                                self._simulate_command(item["payload"])
                            else:
                                wire = (json.dumps(item["payload"], separators=(",", ":")) + "\n").encode()
                                if connection.write(wire) != len(wire):
                                    raise OSError("串口命令未完整发送，结果未知")
                    if self.simulate:
                        if self.sim_cal_until and time.monotonic() >= self.sim_cal_until:
                            self.sim_cal_until = 0
                            self.consume(b'{"type":"status","ok":true,"message":"calibration saved in NVS"}\n')
                        if time.monotonic() >= next_frame:
                            self.consume((json.dumps(self._mock_frame(seq)) + "\n").encode())
                            seq += 1; next_frame = time.monotonic() + 1
                        self._stop.wait(0.02)
                    else:
                        chunk = connection.read(max(1, min(connection.in_waiting, 4096)))
                        buffered.extend(chunk)
                        while b"\n" in buffered:
                            end = buffered.index(10) + 1
                            line = bytes(buffered[:end]); del buffered[:end]
                            self.consume(line)
                        if len(buffered) > 16384:
                            if self.logger:
                                self.logger.raw(bytes(buffered), now_iso(), time.monotonic())
                            buffered.clear()
                            raise ValueError("串口行超过16KB，已保留片段并断开")
                    now = time.monotonic()
                    if self.motor_lease_active and now-self.operator_alive < .75 and now-self.last_motor_lease >= .25:
                        heartbeat = dict(cmd="motor_heartbeat", session_id=self.motor_session)
                        if self.simulate:
                            self.sim_motor["lease"] = now
                        else:
                            wire = (json.dumps(heartbeat, separators=(",", ":"))+"\n").encode()
                            if connection.write(wire) != len(wire):
                                raise OSError("滚筒心跳未完整发送")
                        self.log("commands", dict(phase="lease", payload=heartbeat))
                        self.last_motor_lease = now
            except Exception as exc:
                self.log("device_health", dict(kind="CONNECTION_ERROR", error=str(exc)))
                self.error.emit(str(exc))
            finally:
                if buffered and self.logger:
                    self.logger.raw(bytes(buffered), now_iso(), time.monotonic())
                self.connected = False
                self.motor_lease_active = False
                self.result(self.tracker.abort("连接已断开，命令不重放"))
                self._clear_commands()
                if connection:
                    try:
                        connection.close()
                    except Exception:
                        pass
                self.log("device_health", dict(kind="DISCONNECTED"))
                self.status.emit("DISCONNECTED")
            if not self._stop.is_set():
                self._stop.wait(2)
