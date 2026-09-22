from __future__ import annotations

import json
import math
from datetime import datetime, timezone

HOST_VERSION = "host_mvp-v0.9.0"
MOTOR_FIELDS = ["motor_compiled", "motor_interlock", "motor_running", "motor_fault", "shake_target_rpm", "shake_actual_rpm",
                "shake_direction", "shake_start_time", "shake_end_time", "shake_duration_s", "shake_time_kind", "motor_current_a", "motor_pwm_duty"]
SENSOR_FIELDS = ["batch_id", "seq", "datetime_iso", "host_monotonic_s", "mcu_t_ms"]
SENSOR_FIELDS += [f"gas_{i}_adc" for i in range(1, 5)] + [f"gas_{i}_v" for i in range(1, 5)]
SENSOR_FIELDS += ["bme688_gas_ohm", "chamber_temp_c", "chamber_rh_pct", "ambient_temp_c",
                  "ambient_rh_pct", "leaf_temp_c", "mass_g", "pump_state", "sample_valve_state",
                  "purge_valve_state", "quality_flag", "gas_enabled_mask", "device_frame_json"]
SENSOR_FIELDS += MOTOR_FIELDS
EVENT_FIELDS = ["event_id", "batch_id", "host_time_iso", "host_monotonic_s", "event_type", "event_value", "operator", "note"]
IMAGE_FIELDS = ["filename", "batch_id", "datetime_iso", "host_monotonic_s", "event_nearby", "exposure", "white_balance", "source_mode", "note"]

def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")

def default_batch_id():
    return datetime.now().astimezone().strftime("BATCH_%Y%m%d_%H%M%S_%f")[:-3]

def finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

def sensor_row(frame, batch_id, iso, mono, previous_seq=None):
    seq = frame.get("seq")
    if (not isinstance(seq, int) or isinstance(seq, bool) or seq < 0
            or not isinstance(frame.get("gas_adc"), list) or not isinstance(frame.get("gas_v"), list)):
        raise ValueError("无效传感器帧：seq 或气敏数组格式错误")
    flags = frame.get("quality_flag", "OK")
    if not isinstance(flags, str) or not flags:
        flags = "SENSOR_ERROR"
    if previous_seq is not None and seq != previous_seq + 1:
        flags = "SERIAL_GAP" if flags == "OK" else flags + ";SERIAL_GAP"
    row = dict(batch_id=batch_id, seq=seq, datetime_iso=iso, host_monotonic_s=f"{mono:.6f}",
               mcu_t_ms=frame.get("t_ms", ""), quality_flag=flags,
               gas_enabled_mask=frame.get("gas_enabled_mask", 15),
               device_frame_json=json.dumps(frame, ensure_ascii=False, separators=(",", ":")))
    for unit in ("adc", "v"):
        values = (frame[f"gas_{unit}"] + [None] * 4)[:4]
        row.update({f"gas_{i+1}_{unit}": value for i, value in enumerate(values)})
    for source, target in [("chamber_t_c", "chamber_temp_c"), ("ambient_t_c", "ambient_temp_c"),
                           ("leaf_t_c", "leaf_temp_c"), ("pump", "pump_state"),
                           ("valve_sample", "sample_valve_state"), ("valve_purge", "purge_valve_state")]:
        row[target] = frame.get(source)
    for name in ("bme688_gas_ohm", "chamber_rh_pct", "ambient_rh_pct", "mass_g"):
        row[name] = frame.get(name)
    for name in MOTOR_FIELDS:
        row[name] = frame.get(name)
    return row
