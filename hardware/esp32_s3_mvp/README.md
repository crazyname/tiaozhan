# 青韵智控 ESP32-S3 实测采集固件 v0.2.0

硬件：**ESP32-S3-DevKitC-1 N8R8**。接线必须遵守 [HW-v0.2 引脚与电气规范](../PINOUT.md)。本目录是实测采集程序，不生成模拟读数；传感器未接、通信失败、称重未标定时对应字段为 `null` 并带诊断信息。

已完成两个构建环境的交叉编译、纯逻辑测试及上位机兼容性测试；**未烧录到实物板，未验证接线、测量精度和长期稳定性**。本机目前枚举到的 COM3/4/5/7 是蓝牙串口，不是已确认的 ESP32-S3。

## 1. 已实现能力与结构

| 功能 | 当前实现 |
|---|---|
| 三路 TGS | ADS1115 单端采样，第四路预留，输出 ADC 原始码和 ADC 引脚电压 |
| 双 SHT31 | 环境/采样腔独立地址，校验两个 CRC 字节 |
| MLX90614 | 对象温度，SMBus PEC 校验及错误位检查 |
| BME688 | Bosch BME68x 库，强制模式、320 ℃/150 ms 加热配置，仅返回新鲜且 gas-valid/heater-stable 的气体电阻 |
| HX711 | A通道128倍增益，ready轮询、24位符号扩展、饱和值与DOUT异常检查 |
| 称重标定 | 空台去皮、已知砝码标定、20个新样本、10秒超时、NVS保存；不自动开机去皮 |
| 泵阀 | 独立任务、硬件使能、采样/清洗互斥、切换延时、限时、主循环停顿保护 |
| 通信 | 原生USB CDC，约1Hz JSON Lines；配置查询、I²C扫描与标定命令 |

```text
include/config.h     GPIO、地址、通道开关、ADC比例和采样配置
include/core.h       CRC、24位转换、标定计算、泵阀状态逻辑
include/sensors.h    驱动接口
src/sensors.cpp      I²C/HX711驱动和Bosch库严格读写适配器
src/main.cpp         调度、USB命令、JSON输出、NVS、泵阀任务
platformio.ini       N8R8构建与依赖版本
test/core_test.cpp   不接硬件的C++核心逻辑回归测试
test/run_core_tests.ps1  Windows测试入口
VALIDATION.md        当前软件验证结果和待执行板上验收
```

主循环负责命令处理、称重轮询和传感器读取；约每秒启动一帧采集。I²C传感器顺序读取，**不是严格同时采样**，也没有实现原设计中的每路5–10Hz统计。每路ADS取一次转换，HX711使用自上一帧以来读到的样本均值，并输出样本数；BME转换等操作会降低实际HX轮询频率。`acquisition_ms` 表示本帧采集耗时。

泵阀独立任务运行在core0，每10ms检查一次；主循环在Arduino默认core1。没有启用Wi-Fi或蓝牙。I²C单次事务超时25ms，ADS转换有30ms轮询期限；设备错误不会用上次成功值顶替。本固件不是经过安全认证的控制器。

## 2. 构建与烧录

使用 PlatformIO。固定依赖为 Espressif32 6.9.0（Arduino-ESP32 2.0.17）、ArduinoJson 6.21.5、Bosch BME68x Sensor library 1.2.40408。配置基于通用 `esp32-s3-devkitc-1`，显式覆盖 `qio_opi` 并定义 `BOARD_HAS_PSRAM`，对应N8R8；构建日志可能仍显示基础板定义的“N8 / No PSRAM”名称，以项目覆盖配置为准，PSRAM实测仍待上板。

本机PowerShell可直接执行：

```powershell
Set-Location D:\tiaozhan
& "$env:USERPROFILE\.platformio\penv\Scripts\pio.exe" run -d hardware/esp32_s3_mvp -e esp32s3
& "$env:USERPROFILE\.platformio\penv\Scripts\pio.exe" run -d hardware/esp32_s3_mvp -e esp32s3_air
```

其他电脑安装PlatformIO后使用 `pio` 替代上述完整路径即可。两种环境：

- `esp32s3`：默认采集版，泵阀输出关闭，气路启动命令被拒绝。
- `esp32s3_air`：允许限时气路命令，但还要求GPIO6使能开关闭合。首次联调先用普通版。

USB线连接板上的 **ESP32-S3原生USB口**（直接连GPIO19/20），不是USB-UART桥口。程序的 `Serial` 输出在原生USB CDC；首次可用BOOT+RESET进入下载模式。先枚举实际端口，以下COM12只是示例：

```powershell
& "$env:USERPROFILE\.platformio\penv\Scripts\pio.exe" device list
& "$env:USERPROFILE\.platformio\penv\Scripts\pio.exe" run -d hardware/esp32_s3_mvp -e esp32s3 -t upload --upload-port COM12
& "$env:USERPROFILE\.platformio\penv\Scripts\pio.exe" device monitor --port COM12 --baud 115200 --echo
```

烧录后原生USB可能重新枚举端口。串口监视器与上位机只能有一个占用串口。二进制位于 `.pio/build/<环境>/firmware.bin`；该文件仅为应用镜像，直接用esptool时还需要正确的bootloader、分区表和偏移，因此优先使用PlatformIO上传目标，不手工猜地址。没有在本次开发中对任何COM口执行烧录。

## 3. USB 命令

在串口监视器输入单行JSON并以换行结束（LF或CRLF）；每行最多255个字符。响应为 `type="status"`，通过 `ok` 和 `message` 判断结果；采集帧仍持续发送。

| 命令 | 示例 | 行为 |
|---|---|---|
| 配置查询 | `{"cmd":"info"}` | 固件/硬件版本、使能状态、称重零点和系数 |
| 扫描I²C | `{"cmd":"scan"}` | 先停止气路，再返回7位地址的十进制数组；如68=0x44 |
| 去皮 | `{"cmd":"tare"}` | 停止气路，采20个新称重样本后保存零点 |
| 砝码标定 | `{"cmd":"calibrate","grams":1000}` | 根据已知零点和实际1000g砝码求counts/g并保存 |
| 限时采样 | `{"cmd":"air","mode":"sample","duration_ms":30000}` | air构建且硬件使能有效时执行 |
| 限时清洗 | `{"cmd":"air","mode":"purge","duration_ms":30000}` | 同上，与采样互斥 |
| 停止气路 | `{"cmd":"stop"}` | 请求全关；不取消正在进行的称重标定 |

标定期间拒绝气路命令。非法气路模式或超出1–120秒的时长会停止当前气路动作并报错；其他未知命令不改变当前动作。STOP由主循环解析，传感器读取会带来响应延迟；需要立即断电使用硬件急停/使能开关。

独立泵阀任务在接收到新命令后先关泵和两阀200ms，再开目标阀，400ms时才开泵，命令时长包含前400ms。使能断开后即丢弃当前动作，重新闭合不会恢复，须重新下发命令。USB拔出不会立即终止动作，仍按剩余时长结束；没有主机持续心跳控制。没有自动采样/清洗循环或摇青电机控制。

当前桌面上位机 v0.3 已提供设备信息查询、去皮、砝码标定、限时采样/吹扫和停止气路按钮，命令与采集共用同一串口连接。去皮和标定须在批次外完成；I²C扫描仍需使用串口监视器。使用监视器前先断开上位机，使用上位机前先关闭监视器，不能同时打开两个串口客户端。默认采集版固件拒绝气路启动命令；气路命令被接受不代表阀位或流量已经得到实测确认。操作和命令超时处理见[上位机说明](../../software/host_mvp/README.md)。

## 4. 称重标定步骤

1. 安装并预热称重系统，保持平台静止；清空平台，发送 `tare`。
2. 等待 `calibration saved in NVS`。程序收集20个新样本；10秒内不足或样本最大最小差超过5000计数则失败，保留旧标定。
3. 放上已知砝码，例如1000g，待机械稳定后发送 `calibrate`，grams必须与实际砝码一致。
4. 等待成功响应，查看 `info` 的 `hx_offset` 与 `hx_counts_per_g`。系数允许负数；零差、过小变化、非数值、超出0–20000g的参考质量拒绝。
5. 取下砝码检查回零，再用多个质量点检查误差和重复性，记录而不是自动修正结果。
6. 重启验证标定保存。更换称重传感器、激励电压、结构或HX711增益后必须重新标定。

NVS namespace为 `qy-scale`，使用单个带标识和校验的 `record` blob 保存offset与scale。写入失败保留旧内存值。首次去皮但未标定时scale仍为0，`mass_g=null`；已标定后的去皮保留原系数，仅更新零点。`mass_g=(hx_raw-offset)/scale`，负质量不裁剪，会标记异常。

5000计数的稳定性门槛只是原型阈值，不代表克级精度。标定期间暂停有效质量输出；没有算法补偿温漂、蠕变或振动。

## 5. JSONL-v0.2 数据契约

保留上位机v0.1的全部字段：`type,seq,t_ms,gas_adc,gas_v,bme688_gas_ohm,chamber_t_c,chamber_rh_pct,ambient_t_c,ambient_rh_pct,leaf_t_c,mass_g,pump,valve_sample,valve_purge`。

| 新增字段 | 语义 |
|---|---|
| `protocol` | `JSONL-v0.2` |
| `firmware` / `hardware` | `QY-FW-0.2.0` / `QY-HW-0.2-N8R8` |
| `device_id` | 来自芯片eFuse MAC的标识字符串 |
| `source_mode` | `hardware`，只说明读取实际接口，不代表传感器已通过验证 |
| `gas_enabled_mask` | 默认7；bit0..3对应四个模拟气敏通道 |
| `gas_divider_ratio` | 默认2；`gas_v`为ADC引脚电压，乘倍率才是分压前电压 |
| `hx_raw` / `hx_samples` | 本周期HX均值与样本数，缺样为空/0 |
| `hx_offset` / `hx_counts_per_g` | 当前标定参数，便于审计 |
| `errors` | 各传感器故障标识数组，缺设备、校验失败等可定位 |
| `quality_flag` | `OK`或多个用分号连接的标记 |
| `actuator_state_kind` | `commanded_not_feedback`，泵阀字段不是实测反馈 |
| `acquisition_ms` | 从本帧采集开始到汇总完成的耗时 |
| `aux_adc` | 仅启用第二片ADS时输出四项，不映射为气敏通道 |

`seq`随帧递增，重启回零；`t_ms`使用64位ESP计时器的启动后毫秒值，避免直接用32位millis在约49天回绕，重启仍会归零。硬件计时与电脑ISO时间不同，不能直接视为UTC。不同传感器在帧内顺序读取，`t_ms`是采集开始时间，HX数据来自最近统计窗口。

`quality_flag`包含：传感器错误时 `SENSOR_ERROR`，启动后前20分钟 `WARMUP`，无有效称重系数 `UNCALIBRATED`，标定过程中 `CALIBRATING`。预热时间只是工程提示，清除WARMUP不证明TGS稳定。禁用的模拟通道输出null，不产生传感器错误；**启用但读取失败**则输出null并标记错误。BME688加热未稳定也不输出伪造的电阻值。

升级后的上位机把固件质量标记和SERIAL_GAP合并保存，新增 `gas_enabled_mask` 和 `device_frame_json` CSV列。后者保存完整已解析设备帧（包含错误和标定参数），不是串口原始字节。检查模块按mask检查启用通道；旧批次没有mask则继续要求四路。设备固件版本在逐帧JSON中保留，批次meta中的旧固定设备版本占位仍需后续改进。

## 6. 配置与扩展

优先在 `include/config.h` 调整地址、通道开关、分压倍率、预热时间与GPIO，并同步修改 [接线规范](../PINOUT.md) 和 [CSV表](../pinout.csv)。气路状态机的时限/切换逻辑在 `include/core.h`，不能只改说明文字。任何影响原始数据含义的改动都应升级固件/协议版本并记录硬件变化。

新增真实传感器先检查供电与地址，验证读取失败输出null；不能用假数维持“看起来正常”。BME688仍使用原始气体电阻，不引入未验证VOC浓度或茶叶状态分类。新驱动如果可能长时间阻塞，需要保持气路独立任务可运行，并验证主循环停顿保护。

当前不足：不具备SD离线缓存、传感器完整自动重连状态机、I²C总线时钟恢复、实时流量/阀位反馈、温漂补偿、自动试验循环或电机控制。USB拥塞可能丢弃帧，主机通过seq识别；I²C故障下实际采样周期可能延长。设备校准与可靠性结论必须来自板上测试。

## 7. 回归测试

Windows已安装Visual Studio C++ Build Tools时：

```powershell
Set-Location D:\tiaozhan
& .\hardware\esp32_s3_mvp\test\run_core_tests.ps1
.\software\host_mvp\.venv\Scripts\python.exe -m unittest discover -s software/host_mvp -p 'test_*.py' -v
```

核心测试覆盖CRC校验向量、24位正负转换、正负标定系数、非法参考质量、使能禁止、互斥切换、超时、定时器回绕、STOP与使能恢复后不重启。上位机12项测试包含固件错误与原始帧保留、禁用通道和旧格式兼容。测试不能证明实物电气接线或测量精度。
