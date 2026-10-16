# 快速开始 / Getting Started

本指南以**模拟上位机**为最安全的初次入口；涉及真实硬件的操作只列出研发准备步骤，不能替代电气检查、急停及专业人员监督。

## 1. 环境

- Python 3.11/3.12；Windows 10/11 为已记录的主要开发环境。Linux/macOS 需自行核验 GUI、相机和串口兼容性。
- 上位机依赖定义：[requirements.txt](../software/host_mvp/requirements.txt)。
- ESP32-S3 开发使用 [PlatformIO](https://platformio.org/)；板型及依赖版本见 [platformio.ini](../hardware/esp32_s3_mvp/platformio.ini)。

## 2. 不接设备启动模拟上位机

在仓库根目录：

~~~powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r software/host_mvp/requirements.txt
cd software/host_mvp
..\..\.venv\Scripts\python.exe app.py --simulate
~~~

Linux/macOS 先使用 `python3 -m venv .venv`，再以 `./.venv/bin/python` 执行安装和启动，进入目录后相应调整相对路径。模拟数据、模拟相机及模拟标定仅用于功能演示，禁止用于实际做青效果分析。

首次使用依次体验设备信息、批次元数据、人工事件、结束批次、历史回放。真实输出默认保存在仓库根目录附近的运行数据位置，参考[上位机数据保存规范](../software/host_mvp/README.md)；**原始记录不得为了清理工作目录而直接删除**。

## 3. 运行 Python 单元测试

在仓库根目录、已经安装依赖后：

~~~powershell
$env:QT_QPA_PLATFORM="offscreen"
.\.venv\Scripts\python.exe -m unittest discover -s software/host_mvp -p "test_*.py" -v
~~~

这是软件测试，不能证明气敏测量准确、实际电机安全或制茶品质改善。也可在上位机目录按照其 README 执行批次数据检查。

## 4. 编译 ESP32-S3 固件

在仓库根目录安装好 PlatformIO CLI 后：

~~~powershell
pio run -d hardware/esp32_s3_mvp -e esp32s3
pio run -d hardware/esp32_s3_mvp -e esp32s3_air
pio run -d hardware/esp32_s3_mvp -e esp32s3_motor
~~~

- `esp32s3`：默认采集构建，气路及电机输出关闭，建议作为首次联调起点。
- `esp32s3_air`：允许带硬件使能保护的气路操作；须先核对泵阀电路。
- `esp32s3_motor`：含滚筒人工控制代码；独立急停与速度反馈等安全链尚需实物验收，不应直接驱动未经验证的机械装置。

编译成功**不代表已经烧录、更不代表实物验证**。接线检查先看 [PINOUT](../hardware/PINOUT.md) 和 [MOTOR_BASELINE](../hardware/MOTOR_BASELINE.md)；固件详细上传步骤见[固件 README](../hardware/esp32_s3_mvp/README.md)。

## 5. 真实串口连接前

需要确认：真实 COM 口（不是蓝牙虚拟端口）、电源与电气隔离、模块 GPIO、电机独立急停、传感器正确标定、操作者现场监护。上位机串口连接示例（端口号仅为示例）：

~~~powershell
cd software/host_mvp
python app.py --port COM12
~~~

不允许仅以软件停止按钮或 USB 断开作为安全保护。遇到执行器状态不明、无实测反馈、数据异常时，先转入独立硬件安全停机与现场检查。

## 6. 数据、实验与复现

- 项目提供的处理工作簿：[data/hardware_tests/](../data/hardware_tests/)；**不包含完整原始逐秒传感日志**。
- 采样实验 SOP：[docs/10_第一批做青采样SOP.md](10_第一批做青采样SOP.md)。
- 处理结果指标索引：[data/hardware_tests/metrics_20261009_v2.json](../data/hardware_tests/metrics_20261009_v2.json)，与处理工作簿对应。
- 指标与工作簿的一致性校验，从根目录执行 `python scripts/verify_hardware_results.py`。校验仅复算已提供材料，不认证实验事实本身。

报告必须注明来源（模拟/实物/人工输入）、硬件与软件版本、批次、标定、环境条件及原始记录是否可审计。
