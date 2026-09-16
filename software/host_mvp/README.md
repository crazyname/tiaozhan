# 青韵智控 Host MVP v0.1

这是第一阶段的数据采集上位机。目标是先把**串口协议、实时显示、原始数据落盘、事件标记、图像快照**跑通，而不是做最终比赛界面。

## 1. 环境

推荐 Windows + Python 3.12。

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
- 自动 `session_check.txt`；
- 串口自动重连；
- 真实 ADS1115 / TGS / SHT31 / MLX90614 / HX711 驱动；
- 泵阀控制命令；
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

优先顺序：

1. 将 ESP32 mock 字段逐个替换为真实传感器读数；
2. 先接 ADS1115 + 1 路模拟输入验证；
3. 再接 TGS2600/2602/2603；
4. 接 SHT31；
5. 接 HX711；
6. 完成 `session_check.txt` 与自动快照；
7. 最后再进入第一批真实做青被动监测。
