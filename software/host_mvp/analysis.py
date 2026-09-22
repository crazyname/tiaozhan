"""Reproducible offline engineering analysis, never writes into raw batches."""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import math
import statistics
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from replay import BatchSnapshot, SERIES, inside, number
from schema import HOST_VERSION

ANALYSIS_VERSION = "engineering-analysis-v1"
INPUT_FILES = ("meta.yaml", "sensor_1hz.csv", "events.csv", "image_index.csv", "master_labels.csv", "device_health.jsonl")


@dataclass(frozen=True)
class Parameters:
    baseline_start: float = 0
    baseline_end: float = 60
    stable_start: float = 60
    stable_end: float = 120
    field: str = "gas_1_v"
    allowed_flags: tuple = ("OK", "SERIAL_GAP")
    repeat_group: str = ""

    def validate(self):
        values = (self.baseline_start, self.baseline_end, self.stable_start, self.stable_end)
        if any(number(value) is None for value in values) or not (0 <= values[0] < values[1] <= values[2] < values[3]):
            raise ValueError("时间窗必须有限且满足 0 ≤ 基线起 < 基线止 ≤ 稳定起 < 稳定止（秒）")
        if self.field not in SERIES or self.field == "loss_pct":
            raise ValueError("请选择原始数值列作为漂移分析指标")
        if not self.allowed_flags or not all(isinstance(x, str) and x for x in self.allowed_flags):
            raise ValueError("允许的质量标记不能为空")


def manifest(root):
    result = {}
    for name in INPUT_FILES:
        path = inside(root, name)
        if path.is_file():
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024*1024), b""):
                    digest.update(chunk)
            result[name] = digest.hexdigest()
        else:
            result[name] = None
    return result


def sequence_metrics(batch):
    """Original file order; reset/reorder never enters the forward gap total."""
    counts = dict(inferred_missing=0, reset_evidence=0, backward_or_reordered=0, duplicates=0, invalid=0)
    changes = []
    previous = None
    segment = {}

    def finish_segment():
        ordered = sorted(segment)
        for left, right in zip(ordered, ordered[1:]):
            if right > left + 1:
                amount = int(right-left-1)
                counts["inferred_missing"] += amount
                changes.append(dict(record=segment[right], previous_record=segment[left], kind="inferred_missing", count=amount))
        segment.clear()
    # Read even rows omitted from the replay time axis.
    for line, row in batch._read("sensor_1hz.csv"):
        seq, mcu = number(row.get("seq")), number(row.get("mcu_t_ms"))
        if seq is None or seq < 0 or not seq.is_integer():
            counts["invalid"] += 1; finish_segment(); previous = None; continue
        if previous:
            old_line, old_seq, old_mcu = previous
            kind = None
            if mcu is not None and old_mcu is not None and mcu < old_mcu:
                kind = "reset_evidence"  # MCU clock rollback evidence, not external confirmation.
                finish_segment()
            elif seq < old_seq:
                kind = "backward_or_reordered"
            elif seq == old_seq:
                kind = "duplicates"
            if kind:
                counts[kind] += 1
                changes.append(dict(record=line, previous_record=old_line, kind=kind, count=1))
        segment.setdefault(seq, line)
        previous = (line, seq, mcu)
    finish_segment()
    return dict(**counts, changes=changes,
                definition="原文件顺序；MCU时间倒退分段，单列为重启/时钟回退证据；序号倒退原因未知；每段用已见序号集合计算内部缺号，后到帧可填补缺口；不估计首尾丢包，不证明链路丢包。")


def summarize(points):
    if not points:
        return dict(count=0, mean=None, sample_sd=None, slope_per_s=None, records=[])
    times = [p[0] for p in points]; values = [p[1] for p in points]
    mean_t, mean_v = statistics.mean(times), statistics.mean(values)
    denominator = sum((t-mean_t)**2 for t in times)
    slope = sum((t-mean_t)*(v-mean_v) for t, v in zip(times, values))/denominator if denominator else None
    return dict(count=len(points), mean=mean_v, sample_sd=statistics.stdev(values) if len(values) >= 2 else None,
                slope_per_s=slope, records=[p[2] for p in points])


def analyze_batch(batch, params):
    params.validate()
    baseline, stable, excluded, curve = [], [], [], []
    allowed = set(params.allowed_flags)
    seen_times = set()
    for row in batch.sensors:
        flags = set(filter(None, (row.values.get("quality_flag") or "").split(";")))
        reason = None
        if not flags or not flags <= allowed:
            reason = "quality_flag"
        elif row.time in seen_times:
            reason = "duplicate_time"
        seen_times.add(row.time)
        value = batch.value(row, params.field)
        if value is None:
            reason = reason or "missing_or_nonfinite"
        if params.field.startswith("gas_"):
            mask = number(row.values.get("gas_enabled_mask", "15"))
            channel = int(params.field.split("_")[1])-1
            if mask is None or not mask.is_integer() or not 0 <= mask <= 15 or not (int(mask) & (1 << channel)):
                reason = reason or "disabled_or_invalid_channel"
        if reason:
            excluded.append(dict(record=row.line, reason=reason))
        else:
            point = (row.time, value, row.line)
            if params.baseline_start <= row.time < params.baseline_end:
                baseline.append(point)
            if params.stable_start <= row.time < params.stable_end:
                stable.append(point)
        mass = number(row.values.get("mass_g"))
        mass_valid = bool(flags and flags <= allowed and mass is not None and mass >= 0)
        curve.append(dict(record=row.line, time_s=row.time, value=value if reason is None else None,
                          mass_g=mass if mass_valid else None,
                          loss_pct=batch.value(row, "loss_pct") if mass_valid else None,
                          exclusion=reason, quality_flag=row.values.get("quality_flag")))
    left, right = summarize(baseline), summarize(stable)
    delta = right["mean"]-left["mean"] if left["mean"] is not None and right["mean"] is not None else None
    return dict(batch=batch.root.name, root=str(batch.root), source=batch.source, clock=batch.clock,
                warnings=batch.warnings, sequence=sequence_metrics(batch), baseline=left, stable=right,
                stable_minus_baseline=delta, exclusions=excluded, curve=curve,
                invalid_time_records=[line for line, row in batch._read("sensor_1hz.csv") if batch._time(row) is None],
                initial_mass_g=batch.initial_mass, initial_mass_source=batch.meta.get("initial_mass_source", "legacy_fallback"))


def repeatability(batches, results, params):
    reason = None
    fields = ("cultivar", "origin", "protocol_id", "experiment_protocol_version", "calibration_version", "sensor_model_and_batch")
    if len(batches) < 2 or not params.repeat_group.strip():
        reason = "至少两批且必须人工确认同一重复实验组（repeat_group）"
    elif len({batch.root for batch in batches}) != len(batches):
        reason = "重复打开同一批次不构成重复实验"
    elif any(not batch.meta.get(field) for batch in batches for field in fields):
        reason = "品种/产地/方案/SOP/标定/传感器档案不完整，不能确认可比性"
    elif any(any(batch.meta[field] != batches[0].meta[field] for field in fields) for batch in batches):
        reason = "实验与设备档案不一致，不合并计算重复性"
    elif len({batch.source for batch in batches}) != 1:
        reason = "数据来源不一致，不混合模拟与实测"
    elif any("unknown" in batch.source or "unverified" in batch.source or " / " in batch.source for batch in batches):
        reason = "批次来源未知、未确认或混合，不能确认可比性"
    elif any(result["stable"]["count"] < 2 for result in results):
        reason = "每批稳定窗口至少需要两个有效样本"
    elif any(not batch.sensors or batch.sensors[-1].time < params.stable_end for batch in batches):
        reason = "部分批次未覆盖完整稳定窗口"
    if reason:
        return dict(eligible=False, reason=reason, sample_sd=None, cv_pct=None)
    values = [result["stable"]["mean"] for result in results]
    mean = statistics.mean(values); sd = statistics.stdev(values)
    return dict(eligible=True, group=params.repeat_group, batch_count=len(values), mean=mean,
                sample_sd=sd, cv_pct=sd/abs(mean)*100 if mean != 0 else None,
                reason="组内稳定窗口均值的样本标准差及CV；均值为0时CV未定义；模拟组只验证计算，不证明实验效果")


def export_report(roots, output_parent, params=Parameters()):
    params.validate()
    roots = [Path(root).resolve() for root in roots]
    if not roots or len(set(roots)) != len(roots):
        raise ValueError("请选择至少一个不重复的批次")
    parent = Path(output_parent).resolve()
    if any(parent.is_relative_to(root) for root in roots):
        raise ValueError("分析输出必须位于所有原始批次目录之外")
    if any((root / "INCOMPLETE").exists() for root in roots):
        raise ValueError("未完成批次不能导出可复算报告；请等待采集完成")
    before = [manifest(root) for root in roots]
    if any(item["sensor_1hz.csv"] is None for item in before):
        raise ValueError("传感器文件缺失，不能分析")
    batches = [BatchSnapshot(root) for root in roots]
    results = [analyze_batch(batch, params) for batch in batches]
    if before != [manifest(root) for root in roots]:
        raise ValueError("读取期间原始文件发生变化，请重新分析")
    output = parent / (ANALYSIS_VERSION + "_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8])
    output.mkdir(parents=True, exist_ok=False)
    marker = output / "INCOMPLETE"
    marker.write_text("报告尚未完成", encoding="utf-8")
    report = dict(analysis_version=ANALYSIS_VERSION, host_version=HOST_VERSION,
                  created_at=datetime.now(timezone.utc).isoformat(), parameters=asdict(params),
                  definitions={"window": "半开区间 [start,end)，相对 SESSION_START；无起始事件时用首个可用时间",
                               "drift": "稳定窗口均值减基线窗口均值；窗口斜率为带截距最小二乘，单位为原指标/秒",
                               "null": "无有效数据、样本不足或指标未定义时为null，不填0",
                               "record": "原 CSV 记录序号，表头为1；不等同含多行字段时的物理行号",
                               "exclusion": "非允许质量标记、非有限值、重复时间、禁用通道；无效时间记录另列",
                               "scope": "工程分析，不是做青状态识别或品质结论"},
                  inputs=[dict(root=str(root), sha256=digest) for root, digest in zip(roots, before)],
                  batches=results, repeatability=repeatability(batches, results, params))
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    with (output / "curves.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=["batch", "record", "time_s", "value", "mass_g", "loss_pct", "exclusion", "quality_flag"])
        writer.writeheader()
        for result in results:
            writer.writerows(dict(batch=result["batch"], **point) for point in result["curve"])
    draw_charts(results, params, output)
    write_html(report, output)
    if before != [manifest(root) for root in roots] or any((root / "INCOMPLETE").exists() for root in roots):
        raise ValueError("导出期间原文件发生变化；保留未完成报告标记，请重新分析")
    marker.unlink()
    return output


def draw_charts(results, params, output):
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    figure = Figure(figsize=(10, 7), layout="constrained")
    FigureCanvasAgg(figure)
    top, bottom = figure.subplots(2, 1)
    for i, result in enumerate(results, 1):
        points = result["curve"]
        x = [p["time_s"] for p in points]
        top.plot(x, [p["value"] if p["value"] is not None else math.nan for p in points], label=f"Batch {i} [{result['source']}]")
        bottom.plot(x, [p["loss_pct"] if p["loss_pct"] is not None else math.nan for p in points], label=f"Batch {i}")
    top.axvspan(params.baseline_start, params.baseline_end, alpha=.10, color="blue", label="baseline")
    top.axvspan(params.stable_start, params.stable_end, alpha=.10, color="green", label="stable")
    top.set_ylabel(params.field); bottom.set_ylabel("Mass loss (%)"); bottom.set_xlabel("Batch time (s)")
    for axis in (top, bottom):
        axis.legend(); axis.grid(alpha=.2)
    figure.savefig(output / "curves.png", dpi=150)


def write_html(report, output):
    esc = lambda value: html.escape(str(value))
    body = ["<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>青韵智控工程分析</title>",
            "<style>body{max-width:1100px;margin:40px auto;font:16px/1.6 sans-serif;color:#24392b}table{border-collapse:collapse;width:100%}td,th{padding:8px;border:1px solid #ccd8cf}pre{white-space:pre-wrap;background:#f1f5f2;padding:16px}img{max-width:100%}</style>",
            "<h1>批次工程分析报告</h1><p>模拟数据仅用于软件验证。本报告不判断做青阶段或品质；空值表示无法计算。</p>",
            "<p><a href='report.json'>完整参数、输入哈希和记录索引</a> · <a href='curves.csv'>曲线数据</a></p>",
            "<h2>计算规则</h2><pre>" + esc(json.dumps(report["parameters"], ensure_ascii=False, indent=2)) + "</pre>",
            "<pre>" + esc(json.dumps(report["definitions"], ensure_ascii=False, indent=2)) + "</pre>",
            "<table><tr><th>图例</th><th>批次 / 来源</th><th>基线均值</th><th>稳定均值</th><th>均值差</th><th>推测缺包</th><th>重启证据</th></tr>"]
    for i, result in enumerate(report["batches"], 1):
        values = [i, result["batch"] + " / " + result["source"], result["baseline"]["mean"],
                  result["stable"]["mean"], result["stable_minus_baseline"], result["sequence"]["inferred_missing"], result["sequence"]["reset_evidence"]]
        body.append("<tr>" + "".join("<td>" + esc(value if value is not None else "未定义") + "</td>" for value in values) + "</tr>")
    body.extend(["</table><img src='curves.png' alt='指标和失水曲线'><h2>重复性</h2><pre>",
                 esc(json.dumps(report["repeatability"], ensure_ascii=False, indent=2)), "</pre><h2>批次警告</h2>"])
    for result in report["batches"]:
        body.append("<h3>"+esc(result["batch"])+"</h3><pre>"+esc("\n".join(result["warnings"]) or "无读取警告")+"</pre>")
    body.append("</html>")
    (output / "report.html").write_text("\n".join(body), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="只读工程分析，结果写到独立版本目录")
    parser.add_argument("batches", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--field", default="gas_1_v", choices=[x for x in SERIES if x != "loss_pct"])
    parser.add_argument("--baseline", nargs=2, type=float, default=(0, 60))
    parser.add_argument("--stable", nargs=2, type=float, default=(60, 120))
    parser.add_argument("--repeat-group", default="")
    args = parser.parse_args()
    try:
        result = export_report(args.batches, args.output, Parameters(*args.baseline, *args.stable, args.field, repeat_group=args.repeat_group))
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(result)


if __name__ == "__main__":
    main()
