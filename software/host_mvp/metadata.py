"""Operator-supplied records, separate from automatically measured provenance."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

METADATA_VERSION = "batch-metadata-v1"
TEXT_FIELDS = {
    "cultivar": "品种", "origin": "产地", "protocol_id": "实验方案编号",
    "picking_conditions": "采摘时间、天气与鲜叶条件",
    "sensor_model_and_batch": "传感器型号、编号与批次",
    "calibration_version": "标定记录版本",
    "calibration_date": "标定日期 / 时间",
    "calibration_reference": "参考仪表 / 砝码编号",
    "calibration_notes": "标定方法、结果与记录位置",
    "experiment_protocol_version": "SOP 版本",
    "experiment_protocol_reference": "SOP 内容或文档位置",
}


def normalize_metadata(values=None):
    values = values or {}
    if not isinstance(values, dict):
        raise ValueError("批次档案必须是对象")
    extra = values.keys() - (TEXT_FIELDS.keys() | {"initial_mass_g"})
    if extra:
        raise ValueError("档案不能覆盖设备或软件追溯字段: " + ", ".join(sorted(extra)))
    result = {}
    for key in TEXT_FIELDS:
        value = values.get(key)
        if value is not None and not isinstance(value, str):
            raise ValueError(f"{TEXT_FIELDS[key]}必须是文本")
        result[key] = value.strip() or None if value is not None else None
    mass = values.get("initial_mass_g")
    if mass in (None, ""):
        result["initial_mass_g"] = None
    else:
        try:
            number = float(mass)
        except (TypeError, ValueError):
            raise ValueError("初始装料质量必须是正数（g），未知请留空") from None
        if isinstance(mass, bool) or not math.isfinite(number) or number <= 0:
            raise ValueError("初始装料质量必须是有限正数（g）")
        result["initial_mass_g"] = number
    return result


def metadata_digest(values):
    return hashlib.sha256(json.dumps(normalize_metadata(values), ensure_ascii=False,
                                     sort_keys=True, allow_nan=False).encode("utf-8")).hexdigest()


def save_profile(path, values):
    """New version only: never silently overwrite an existing profile."""
    values = normalize_metadata(values)
    payload = dict(schema=METADATA_VERSION, values=values, sha256=metadata_digest(values))
    with Path(path).open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)


def load_profile(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict) or payload.get("schema") != METADATA_VERSION:
        raise ValueError("不支持的档案格式 / 版本")
    values = normalize_metadata(payload.get("values"))
    if payload.get("sha256") != metadata_digest(values):
        raise ValueError("档案校验不一致，请核对原始版本")
    return values
