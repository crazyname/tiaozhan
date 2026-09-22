# 青韵智控 Host MVP v0.1

这是第一阶段的数据采集上位机。目标是先把**串口协议、实时显示、原始数据落盘、事件标记、图像快照**跑通，而不是做最终比赛界面。

开发与维护请参阅 [开发文档](DEVELOPMENT.md)，包括模块结构、线程与数据流、协议字段、测试、已知限制和后续开发入口。

实际硬件请使用 [ESP32-S3 N8R8 固件](../../hardware/esp32_s3_mvp/README.md) 并遵守 [接线规范](../../hardware/PINOUT.md)。当前上位机已兼容固件JSONL-v0.2：保留设备质量标记、启用通道掩码及完整已解析设备帧。

## 1. 环境

推荐 Windows + Python 3.12。
界面仅依赖 PySide6-Essentials（提供所需的 Qt 桌面组件），无需安装完整 Addons。

```powershell
cd D:\tiaozhan\software\host_mvp
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -r requirements.txt
```

## 2. 不接硬件先跑通

```powershell
python app.py --simulate
```

然后：

1. 点击“连接”；
2. 点击“开始批次”；
3. 确认 4 路气敏曲线持续更新；
4. 点击“摇青开始 / 摇青结束 / 师傅检查 / 取样”；
5. 点击“结束批次”。

数据会写入：

```text
data/raw/BATCH_YYYYMMDD_HHMMSS/
├─ meta.yaml
├─ sensor_1hz.csv
├─ events.csv
├─ session_check.txt  # 结束批次后生成
└─ images/
```

模拟数据仅用于联调，严禁进入论文、算法性能或比赛结果。

## 3. 接 ESP32-S3 mock firmware

先烧录：

`hardware/esp32_s3_mock/esp32_s3_mock.ino`

确认设备串口号，例如 `COM7`，再运行：

```powershell
python app.py --port COM7
```

GUI 中不要勾选“模拟数据”，点击连接。

## 4. 第一版已经具备

- USB / Serial + JSON Lines；
- `seq` 连续性检查；
- MCU `t_ms` + PC ISO 时间 + monotonic 时间；
- 4 路气敏实时曲线；
- 环境 / 采样腔温湿度；
- 叶温；
- 质量与相对失水率；
- `SESSION_START / END`；
- `SHAKE_START / END`；
- `MASTER_CHECK`；
- `SAMPLE_TAKEN`；
- 摄像头手动快照；
- `quality_flag=SERIAL_GAP`；
- CSV/YAML 持续落盘。

## 5. 当前限制

v0.1 还没有：

- 自动 30 s 图像采集；
- `master_labels.csv` 专用录入窗口；
- `image_index.csv`；
- 串口自动重连；
- 上位机泵阀命令与称重标定按钮（固件命令已实现，当前通过串口监视器使用）；
- SQLite 批次索引；
- 自动控制算法。

这些按优先级逐步加入，不影响现在开始联调。

## 6. v0.1 验收

### 软件模拟模式

连续运行至少 2 h：

- UI 不崩溃；
- `sensor_1hz.csv` 持续增长；
- `seq` 无异常跳变；
- 每个事件都进入 `events.csv`；
- 结束批次后文件可正常打开。

### ESP32 mock 模式

连续运行至少 4 h：

- 串口无持续解析错误；
- `seq` / `t_ms` 单调；
- PC 休眠必须关闭；
- USB 接触不良、拔线等故障必须能在日志中被发现。

## 7. 下一步

软件开发优先级、实现范围和验收标准见 [开发文档第10节](DEVELOPMENT.md#10-软件开发路线与验收标准)。下一轮建议先做设备操作面板、后台写盘、自动拍照和师傅标签录入；这些均为待开发项。

硬件联调顺序：

1. 烧录已实现的实测固件，逐个验证真实传感器读数（驱动代码已完成，硬件未验收）；
2. 先接 ADS1115 + 1 路模拟输入验证；
3. 再接 TGS2600/2602/2603；
4. 接 SHT31；
5. 接 HX711；
6. 在已有 `session_check.txt` 基础上扩展检查，并补充自动快照；
7. 最后再进入第一批真实做青被动监测。

## 数据完整性检查更新

结束批次或关闭窗口时自动生成 `session_check.txt`，包含数据行数、图片数量、
质量标记统计、序号连续性、设备与主机时间递增性、超过 3 秒的数据间隔、
缺失/非有限数值、湿度与开关状态越界、批次编号一致性、起止事件配对和剩余磁盘空间。
报告仅检查基础数据完整性，不替代器件校准或真实实验验证。
温度、电压和 ADC 的器件专用量程尚未配置；图片仅统计数量。

已有批次也可以重新检查（不改动原始 CSV）：

```powershell
python session_check.py ../../data/raw/BATCH_20260921_001
```

开始批次前须先连接设备或启动模拟源；采集中锁定连接方式与批次信息，
避免模拟来源标记与实际数据源不一致。批次编号限定为字母、数字、下划线和短横线，
不允许覆盖已有目录。报告写入失败会在界面日志中显示。

运行回归检查：

```powershell
python -m unittest discover -p "test_*.py" -v
```

仍需在实际设备上完成串口、相机及至少 8 小时连续运行验收。

## JSONL-v0.2 固件配套更新

CSV新增 `gas_enabled_mask` 与 `device_frame_json`，旧列顺序保留。掩码默认15（四路）；新硬件默认7（三路），检查时不把未启用第四路的空值当作故障。启用通道缺值仍报错。

固件 `quality_flag`（如WARMUP、UNCALIBRATED、SENSOR_ERROR）不会被上位机覆盖为OK；发生跳号时追加SERIAL_GAP。完整已解析设备帧保存为JSON列，其中含固件/硬件版本、传感器错误和称重标定参数。它不等于原始串口字节存档。
