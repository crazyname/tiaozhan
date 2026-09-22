"""Read-only batch audit; runnable without the desktop dependencies."""
from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path


def audit_session(root: Path) -> str:
    root = Path(root)
    issues: Counter[str] = Counter()
    if (root / "INCOMPLETE").exists():
        issues["批次未完成或写盘失败（INCOMPLETE）"] += 1
    summary_path = root / "writer_summary.json"
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            if summary.get("error") or summary.get("unwritten") or summary.get("rejected"):
                issues["后台写盘存在错误、拒收或未保存记录"] += 1
        except (OSError, ValueError, AttributeError):
            issues["写盘汇总无法解析"] += 1
    samples = 0
    flags: Counter[str] = Counter()
    previous: dict[str, float] = {}
    if not (root / "meta.yaml").is_file() or not (root / "meta.yaml").stat().st_size:
        issues["meta.yaml 缺失或为空"] += 1
    required = ["seq", "host_monotonic_s", "mcu_t_ms", "mass_g",
                "chamber_rh_pct", "ambient_rh_pct", "chamber_temp_c",
                "ambient_temp_c", "leaf_temp_c", "bme688_gas_ohm"]
    required += [f"gas_{i}_{unit}" for i in range(1, 5) for unit in ("adc", "v")]
    required += ["pump_state", "sample_valve_state", "purge_valve_state"]
    try:
        with (root / "sensor_1hz.csv").open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for name in required + ["datetime_iso", "batch_id", "quality_flag"]:
                if name not in (reader.fieldnames or []):
                    issues[f"传感器列缺失: {name}"] += 1
            for row in reader:
                samples += 1
                # Legacy batches lack a mask and require all four channels.
                try:
                    mask = int(row.get("gas_enabled_mask", "15"))
                    if not 0 <= mask <= 15:
                        raise ValueError
                except (ValueError, TypeError):
                    issues["无效 gas_enabled_mask"] += 1
                    mask = 15
                flags[row.get("quality_flag") or "EMPTY"] += 1
                if row.get("quality_flag") != "OK":
                    issues["存在非 OK 或缺失的质量标记"] += 1
                if row.get("batch_id") != root.name:
                    issues["传感器批次编号不匹配"] += 1
                try:
                    datetime.fromisoformat(row.get("datetime_iso") or "")
                except ValueError:
                    issues["传感器日期无效"] += 1
                for name in required:
                    if name.startswith("gas_") and not (mask & (1 << (int(name.split("_")[1]) - 1))):
                        previous.pop(name, None)
                        continue
                    try:
                        value = float(row.get(name) or "")
                        if not math.isfinite(value):
                            raise ValueError
                    except (ValueError, TypeError):
                        issues[f"缺失或非有限数值: {name}"] += 1
                        previous.pop(name, None)
                        continue
                    if name in ("seq", "mcu_t_ms") and (value < 0 or not value.is_integer()):
                        issues[f"无效计数值: {name}"] += 1
                    if name == "seq" and name in previous and value != previous[name] + 1:
                        issues["seq 不连续（含重复、倒退或缺包）"] += 1
                    if name in ("mcu_t_ms", "host_monotonic_s") and name in previous:
                        delta = value - previous[name]
                        if delta <= 0:
                            issues[f"时间未递增: {name}"] += 1
                        if delta > (3000 if name == "mcu_t_ms" else 3):
                            issues[f"超过 3 秒的数据间隔: {name}"] += 1
                    if name.endswith("rh_pct") and not 0 <= value <= 100:
                        issues[f"湿度越界: {name}"] += 1
                    if name in ("mass_g", "bme688_gas_ohm") and value < 0:
                        issues[f"负数读数: {name}"] += 1
                    if name.endswith("_state") and value not in (0, 1):
                        issues[f"状态越界: {name}"] += 1
                    previous[name] = value
    except (OSError, UnicodeError, csv.Error) as exc:
        issues[f"传感器文件读取失败: {exc}"] += 1
    if samples == 0:
        issues["没有传感器数据"] += 1
    event_types = []
    event_ids = set()
    try:
        with (root / "events.csv").open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                event_types.append(row.get("event_type"))
                event_ids.add(row.get("event_id"))
                if row.get("batch_id") != root.name:
                    issues["事件批次编号不匹配"] += 1
        if (event_types.count("SESSION_START") != 1 or event_types.count("SESSION_END") != 1
                or event_types[0] != "SESSION_START" or event_types[-1] != "SESSION_END"):
            issues["SESSION_START / SESSION_END 未正确成对"] += 1
    except (OSError, UnicodeError, csv.Error) as exc:
        issues[f"事件文件读取失败: {exc}"] += 1
    images = sum(1 for p in (root / "images").glob("*.jpg") if p.is_file())
    index_path = root / "image_index.csv"
    if index_path.exists():
        indexed = set()
        try:
            with index_path.open(encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    name = row.get("filename", "")
                    path = (root / name).resolve()
                    if not path.is_relative_to((root / "images").resolve()) or not path.is_file():
                        issues["图片索引路径无效或文件缺失"] += 1
                    if name in indexed:
                        issues["图片索引重复"] += 1
                    indexed.add(name)
                    if row.get("batch_id") != root.name:
                        issues["图片批次编号不匹配"] += 1
                    if row.get("event_nearby") and row["event_nearby"] not in event_ids:
                        issues["图片关联事件不存在"] += 1
            actual = {p.relative_to(root).as_posix() for p in (root / "images").glob("*.jpg")}
            if actual != indexed:
                issues["图片文件与索引不一致"] += 1
        except (OSError, UnicodeError, csv.Error):
            issues["图片索引读取失败"] += 1
    labels_path = root / "master_labels.csv"
    if labels_path.exists():
        seen = {}
        try:
            with labels_path.open(encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    if row.get("batch_id") != root.name or row.get("event_id") not in event_ids:
                        issues["标签批次或事件关联无效"] += 1
                    old = seen.get(row.get("supersedes"))
                    revision = int(row.get("revision", "0"))
                    if (row.get("supersedes") and (not old or revision != int(old["revision"])+1 or row.get("event_id") != old.get("event_id"))) or (not row.get("supersedes") and revision != 1):
                        issues["标签修订链无效"] += 1
                    if not row.get("label_id") or row["label_id"] in seen:
                        issues["标签编号无效或重复"] += 1
                    seen[row.get("label_id")] = row
        except (OSError, UnicodeError, csv.Error, ValueError, TypeError):
            issues["标签文件读取失败"] += 1
    try:
        free_mb = shutil.disk_usage(root).free / 1024**2
        disk = f"{free_mb:.1f} MB（检查时）"
        if free_mb < 1024:
            issues["剩余磁盘空间不足 1 GB"] += 1
    except OSError as exc:
        disk = f"无法读取: {exc}"
        issues["无法检查磁盘空间"] += 1
    lines = ["青韵智控批次完整性检查", f"批次: {root.name}",
             f"检查时间: {datetime.now().astimezone().isoformat()}",
             f"结果: {'发现异常' if issues else '基础检查通过'}",
             f"传感器行数: {samples}", f"事件数量: {len(event_types)}",
             f"图片数量: {images}", f"剩余磁盘空间: {disk}",
             f"质量标记统计: {dict(flags)}", "", "异常计数:"]
    lines += [f"- {key}: {count}" for key, count in sorted(issues.items())] or ["- 无"]
    lines += ["", "边界：基础检查通过不代表数据具有科研有效性。",
              "温度、电压、ADC 的器件专用量程尚未配置；图片检查文件与索引，未验证内容。",
              "本报告不修改原始数据；模拟数据不得用于实验结论。"]
    return "\n".join(lines) + "\n"


def write_session_check(root: Path) -> Path:
    report = audit_session(root)
    path = Path(root) / "session_check.txt"
    path.write_text(report, encoding="utf-8")
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="检查已有批次并生成 session_check.txt")
    parser.add_argument("batch_directory", type=Path)
    args = parser.parse_args()
    if not args.batch_directory.is_dir():
        parser.error("批次目录不存在")
    print(write_session_check(args.batch_directory))
