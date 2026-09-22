"""Bounded background writer and thread-safe session lifecycle."""
from __future__ import annotations

import base64
import copy
import csv
import json
import queue
import re
import subprocess
import threading
import time
import uuid
from collections import Counter
from pathlib import Path

import yaml
from labels import LABEL_FIELDS, SCALE_VERSION, validate_label
from schema import HOST_VERSION, SENSOR_FIELDS, EVENT_FIELDS, IMAGE_FIELDS, now_iso, default_batch_id, sensor_row, finite_number
from session_check import write_session_check
from metadata import METADATA_VERSION, normalize_metadata, metadata_digest
from camera_process import camera_settings as normalize_camera_settings

TABLES = {"sensor_1hz": SENSOR_FIELDS, "events": EVENT_FIELDS, "image_index": IMAGE_FIELDS, "master_labels": LABEL_FIELDS}

class DiagnosticJournal:
    """Connection-wide journal, including commands issued before a batch."""
    def __init__(self, root, segment_bytes=16*1024*1024, max_segments=64):
        if segment_bytes < 1 or max_segments < 1:
            raise ValueError("日志段大小和段数必须为正数")
        self.root = Path(root)
        self.segment_bytes, self.max_segments = segment_bytes, max_segments
        self.queue = queue.Queue(4096)
        self.closing = threading.Event()
        self.error = ""
        self.thread = None
        self.lock = threading.Lock()

    def submit(self, kind, payload):
        with self.lock:
            if self.error or self.closing.is_set():
                return
            if self.thread is None:
                self.thread = threading.Thread(target=self._run, name="device-journal", daemon=True)
                self.thread.start()
            try:
                self.queue.put_nowait(dict(category=kind, **payload))
            except queue.Full:
                self.error = "设备日志队列已满，连接日志不完整"

    def _run(self):
        handle = None
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            prefix = default_batch_id() + "_" + uuid.uuid4().hex[:8]
            segment, size = 0, 0
            while not (self.closing.is_set() and self.queue.empty()):
                try:
                    item = self.queue.get(timeout=0.05)
                except queue.Empty:
                    continue
                encoded = (json.dumps(item, ensure_ascii=False) + "\n").encode("utf-8")
                if len(encoded) > self.segment_bytes:
                    raise OSError("单条连接日志超过段上限，日志不完整")
                if handle is None or size + len(encoded) > self.segment_bytes:
                    if handle:
                        handle.close(); handle = None
                    if segment >= self.max_segments:
                        raise OSError("本次连接日志容量已达上限；历史日志保留，请归档后重启")
                    handle = (self.root / f"{prefix}_{segment:04d}.jsonl").open("xb")
                    segment += 1; size = 0
                handle.write(encoded); handle.flush(); size += len(encoded)
        except Exception as exc:
            self.error = f"设备日志写入失败: {exc}"
        finally:
            if handle:
                handle.close()

    def close(self):
        self.closing.set()

    @property
    def finished(self):
        return self.thread is None or not self.thread.is_alive()

class BatchWriter:
    def __init__(self, root, meta, capacity=2048):
        self.root, self.meta = Path(root), meta
        self.queue = queue.Queue(capacity)
        self.lock = threading.RLock()
        self.ready, self.done, self.closing = threading.Event(), threading.Event(), threading.Event()
        self.error = ""
        self.accepted, self.written, self.rejected = Counter(), Counter(), Counter()
        self.report = None
        self.thread = threading.Thread(target=self._run, name="batch-writer", daemon=True)
        self.thread.start()
        if not self.ready.wait(5):
            self.fail("文件初始化超时")
            self.close()
            raise OSError(self.error)
        if self.error:
            raise OSError(self.error)

    def submit(self, kind, payload):
        with self.lock:
            if self.error or self.closing.is_set():
                self.rejected[kind] += 1
                return False
            try:
                self.queue.put_nowait((kind, copy.deepcopy(payload)))
                self.accepted[kind] += 1
                return True
            except queue.Full:
                self.rejected[kind] += 1
                self.error = "写入队列已满，批次停止接收；已入队数据继续排空"
                self.closing.set()
                return False

    def fail(self, message):
        with self.lock:
            self.error = self.error or str(message)

    def close(self):
        self.closing.set()

    def _write_item(self, kind, payload, files, writers):
        if kind in writers:
            writers[kind].writerow(payload)
            files[kind].flush()
        elif kind == "meta":
            self.meta.update(payload)
            temp = self.root / "meta.yaml.tmp"
            temp.write_text(yaml.safe_dump(self.meta, allow_unicode=True, sort_keys=False), encoding="utf-8")
            temp.replace(self.root / "meta.yaml")
        else:
            files[kind].write(json.dumps(payload, ensure_ascii=False) + "\n")
            files[kind].flush()

    def _run(self):
        files, writers = {}, {}
        marker = self.root / "INCOMPLETE"
        try:
            marker.write_text("批次尚未完成，禁止当作完整实验。\n", encoding="utf-8")
            (self.root / "meta.yaml").write_text(yaml.safe_dump(self.meta, allow_unicode=True, sort_keys=False), encoding="utf-8")
            for name, fields in TABLES.items():
                files[name] = (self.root / f"{name}.csv").open("w", newline="", encoding="utf-8-sig")
                writers[name] = csv.DictWriter(files[name], fields)
                writers[name].writeheader(); files[name].flush()
            for name in ("raw_serial", "device_health", "commands"):
                files[name] = (self.root / f"{name}.jsonl").open("w", encoding="utf-8")
            self.ready.set()
            while not (self.closing.is_set() and self.queue.empty()):
                try:
                    kind, payload = self.queue.get(timeout=0.05)
                except queue.Empty:
                    continue
                self._write_item(kind, payload, files, writers)
                with self.lock:
                    self.written[kind] += 1
        except Exception as exc:
            self.fail(f"文件写入失败: {exc}")
        finally:
            self.ready.set()
            for handle in files.values():
                try:
                    handle.close()
                except Exception as exc:
                    self.fail(exc)
            try:
                with self.lock:
                    summary = dict(accepted=dict(self.accepted), written=dict(self.written), rejected=dict(self.rejected),
                                   unwritten=dict(self.accepted - self.written), error=self.error, ended_at=now_iso())
                (self.root / "writer_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
                if not self.error:
                    marker.unlink(missing_ok=True)
                self.report = write_session_check(self.root)
            except Exception as exc:
                self.fail(f"收尾失败: {exc}")
                try:
                    marker.write_text(self.error, encoding="utf-8")
                except OSError:
                    pass
            self.done.set()

class SessionLogger:
    def __init__(self, data_root, queue_capacity=2048):
        self.data_root, self.queue_capacity = Path(data_root), queue_capacity
        self.lock = threading.RLock()
        self.active = False
        self.root = None
        self.writer = None
        self.batch_id = self.operator = self.token = ""
        self.event_id = 0
        self.pending_images = 0
        self.stopping = False
        self.last_seq = None
        self.m0 = None
        self.last_device = None
        self.latest_label = None
        self.event_ids = set()
        self.journal = DiagnosticJournal(self.data_root.parent / "device_logs")

    @property
    def error(self):
        return self.writer.error if self.writer else ""

    @property
    def finished(self):
        return self.writer is None or self.writer.done.is_set()

    def _submit(self, kind, row):
        if not self.writer or not self.writer.submit(kind, row):
            self.active = False
            self.stopping = True
            return False
        return True

    def start(self, batch_id, operator, mode, metadata=None, camera_settings=None):
        metadata = normalize_metadata(metadata)
        camera_settings = normalize_camera_settings(camera_settings)
        with self.lock:
            if self.active or not self.finished or self.pending_images:
                raise RuntimeError("上一批次尚未结束")
            name = batch_id.strip() or default_batch_id()
            reserved = {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}
            if not re.fullmatch(r"[\w-]{1,80}", name) or name.upper() in reserved:
                raise ValueError("批次编号仅允许1–80个字母、数字、下划线或短横线，不得使用系统保留名")
            root = self.data_root / name
            root.mkdir(parents=True, exist_ok=False)
            (root / "images").mkdir()
            self.root, self.batch_id, self.operator = root, name, operator.strip() or "unknown"
            self.token = uuid.uuid4().hex
            self.event_id = self.pending_images = 0
            self.last_seq = self.m0 = self.last_device = self.latest_label = None
            self.event_ids = set()
            self.stopping = False
            provenance = {"host_git_commit": None, "host_worktree_dirty": None}
            try:
                repo = Path(__file__).resolve().parents[2]
                options = dict(cwd=repo, capture_output=True, text=True, check=True, timeout=2,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                provenance["host_git_commit"] = subprocess.run(["git", "rev-parse", "HEAD"], **options).stdout.strip()
                provenance["host_worktree_dirty"] = bool(subprocess.run(["git", "status", "--porcelain"], **options).stdout)
            except (OSError, subprocess.SubprocessError):
                pass
            meta = dict(batch_id=name, project="qingyun-zhikong", operator=self.operator, created_at=now_iso(),
                        host_software_version=HOST_VERSION, **provenance,
                        source_mode="simulate" if mode == "simulate" else "serial_unverified", transport=mode,
                        firmware_version=None, hardware_version=None, device_id=None, calibration_version=None,
                        label_scale_version=SCALE_VERSION)
            meta.update(metadata)
            meta["camera_settings"] = camera_settings
            meta.update(metadata_schema=METADATA_VERSION, operator_metadata=copy.deepcopy(metadata),
                        operator_metadata_sha256=metadata_digest(metadata),
                        initial_mass_source="operator" if metadata["initial_mass_g"] is not None else None)
            self.m0 = metadata["initial_mass_g"]
            self.writer = BatchWriter(root, meta, self.queue_capacity)
            self.active = True
            self.event("SESSION_START", note="session started")
            if self.m0 is not None:
                self.event("T0_INITIAL", str(self.m0), "operator supplied initial loaded mass")
            return root

    def event(self, event_type, event_value="", note=""):
        with self.lock:
            if not self.active:
                return None
            self.event_id += 1
            self.event_ids.add(self.event_id)
            row = dict(event_id=self.event_id, batch_id=self.batch_id, host_time_iso=now_iso(), host_monotonic_s=time.monotonic(),
                       event_type=event_type, event_value=event_value, operator=self.operator, note=note)
            return self.event_id if self._submit("events", row) else None

    def log(self, kind, payload):
        with self.lock:
            self.journal.submit(kind, dict(host_time_iso=now_iso(), host_monotonic_s=time.monotonic(), **payload))
            if self.active:
                self._submit(kind, dict(batch_id=self.batch_id, host_time_iso=now_iso(), host_monotonic_s=time.monotonic(), **payload))

    def raw(self, raw, iso, mono):
        # Base64 is lossless even for invalid UTF-8 or unterminated chunks.
        with self.lock:
            self.journal.submit("raw_serial", dict(host_time_iso=iso, host_monotonic_s=mono,
                                                  data_b64=base64.b64encode(raw).decode("ascii")))
            if self.active:
                self._submit("raw_serial", dict(batch_id=self.batch_id, host_time_iso=iso, host_monotonic_s=mono,
                                                data_b64=base64.b64encode(raw).decode("ascii"), text=raw.decode("utf-8", errors="replace")))

    def ingest(self, frame, iso, mono):
        with self.lock:
            row = sensor_row(frame, self.batch_id if self.active else "", iso, mono, self.last_seq)
            if not self.active:
                return row
            if self.last_seq is not None and frame["seq"] <= self.last_seq:
                self.log("device_health", dict(kind="DEVICE_RESET_OR_REORDER", previous=self.last_seq, seq=frame["seq"]))
            self.last_seq = frame["seq"]
            mass = frame.get("mass_g")
            if self.m0 is None and finite_number(mass) and mass > 0:
                self.m0 = float(mass)
                self.event("T0_INITIAL", str(self.m0), "first positive finite mass")
                self._submit("meta", dict(initial_mass_g=self.m0, initial_mass_source="first_positive_measurement"))
            device = {key: frame.get(key) for key in ("firmware", "hardware", "device_id", "source_mode", "hx_offset", "hx_counts_per_g")}
            if device != self.last_device:
                self.log("device_health", dict(kind="DEVICE_CONFIGURATION", values=device))
                if self.last_device is None:
                    self._submit("meta", dict(initial_device_configuration=device))
                self._submit("meta", dict(firmware_version=device["firmware"], hardware_version=device["hardware"],
                                           device_id=device["device_id"], device_configuration=device,
                                           source_mode=device["source_mode"] or ("simulate" if self.writer.meta.get("transport") == "simulate" else "serial_unverified")))
                self.last_device = device
            self._submit("sensor_1hz", row)
            return row

    def write_sensor(self, row):
        with self.lock:
            if self.active:
                self._submit("sensor_1hz", row)

    def label(self, values, event_id, revise=False):
        validate_label(values)
        with self.lock:
            if not self.active or event_id not in self.event_ids:
                raise ValueError("标签必须关联当前活动批次中的事件")
            old = self.latest_label if revise else None
            if revise and old is None:
                raise ValueError("没有可修订的标签")
            if old and event_id != old["event_id"]:
                raise ValueError("修订必须保留原标签事件关联")
            row = dict(label_id=uuid.uuid4().hex, revision=old["revision"] + 1 if old else 1,
                       supersedes=old["label_id"] if old else "", event_id=event_id, batch_id=self.batch_id,
                       host_time_iso=now_iso(), host_monotonic_s=time.monotonic(), operator=self.operator,
                       scale_version=SCALE_VERSION, **values)
            if not self._submit("master_labels", row):
                raise OSError(self.error)
            self.latest_label = row
            return row

    def image_request(self, event_id="", source_mode="camera"):
        with self.lock:
            if not self.active:
                return None
            self.pending_images += 1
            return dict(token=self.token, root=self.root, filename=f"images/IMG_{uuid.uuid4().hex}.jpg",
                        batch_id=self.batch_id, event_nearby=event_id or "", source_mode=source_mode,
                        camera_settings=copy.deepcopy(self.writer.meta.get("camera_settings")))

    def image_result(self, request, result):
        with self.lock:
            if request["token"] != self.token:
                return
            if result.get("ok"):
                row = {key: result.get(key, request.get(key, "")) for key in IMAGE_FIELDS}
                self._submit("image_index", row)
            else:
                self._submit("device_health", dict(batch_id=self.batch_id, host_time_iso=now_iso(), host_monotonic_s=time.monotonic(),
                                                   kind="CAMERA_ERROR", error=result.get("error"), event_id=request["event_nearby"]))
            self.pending_images -= 1
            self._finish_if_ready()

    def _finish_if_ready(self):
        if self.stopping and self.pending_images == 0 and self.writer and not self.writer.closing.is_set():
            self.event_id += 1
            self._submit("events", dict(event_id=self.event_id, batch_id=self.batch_id, host_time_iso=now_iso(),
                                         host_monotonic_s=time.monotonic(), event_type="SESSION_END", event_value="",
                                         operator=self.operator, note="session ended"))
            self.writer.close()

    def stop(self, wait=True, timeout=10):
        with self.lock:
            if self.writer is None:
                return None
            self.active = False
            self.stopping = True
            self._finish_if_ready()
            writer = self.writer
        if wait and not writer.done.wait(timeout):
            raise TimeoutError("批次仍在保存或等待相机；不可开始下一批")
        return writer.report
