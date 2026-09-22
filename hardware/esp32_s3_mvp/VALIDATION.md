# 固件验证记录

## Wokwi 全系统功能仿真（2026-09-22，构建 PASS，运行待验收）

- 已新增独立 `esp32s3_wokwi` 构建环境，启用 `QY_WOKWI_SIM`、气路与电机功能；原 `esp32s3`、`esp32s3_air`、`esp32s3_motor` 不启用仿真宏，继续走真实硬件驱动。
- 已增加 `diagram.json` / `wokwi.toml`：目标板按 ESP32-S3 DevKitC-1、8MB Flash、8MB octal PSRAM、USB Serial/JTAG 配置；保留 Wokwi 原生 HX711，并加入气路/电机安全开关与泵阀/电机输出 LED。
- HX711 在仿真环境仍走真实 GPIO 位时序，不使用软件假值；ADS1115、双 SHT31、MLX90614、BME688 在 `QY_WOKWI_SIM` 下由确定性的做青过程功能模型提供输入，避免为缺少官方器件模型而污染真实驱动代码。
- 电机仍运行现有 `MotorPolicy` 控制与故障逻辑，但反馈来自一阶数学被控对象；该模型用于验证闭环软件、心跳、互锁和状态上报，不代表真实电机/减速器动力学。
- 仿真输出明确使用 `hardware=QY-WOKWI-SIM`、`source_mode=simulation`，避免把仿真数据混同为实物数据；仿真预热标志缩短为5秒，真实构建仍为20分钟工程标记。
- 已增加 `wokwi_full_demo.yaml`，覆盖 `info`、HX711去皮/1kg标定/0.5kg验证、3秒采样气路、15rpm/3秒电机命令和主机心跳；Automation Scenario 本身尚未在本机 Wokwi 中执行。
- `wokwi.toml` 开启 RFC2217 端口4000，为后续 PySide6 上位机直接连接虚拟 ESP32-S3 预留链路。
- 用户本机已在同步最新 `main` 后执行 `pio run -d hardware/esp32_s3_mvp -e esp32s3_wokwi`，**构建成功**：13.826 秒，静态 RAM 24028 / 327680 字节（7.3%），应用 Flash 320841 / 3342336 字节（9.6%）。编译、链接及 `firmware.bin` 生成均成功。
- PlatformIO 构建摘要仍将基础板描述为 `ESP32-S3-DevKitC-1-N8 (8 MB QD, No PSRAM)`；本项目另外通过 `qio_opi`、`BOARD_HAS_PSRAM` 与 Wokwi `diagram.json` 的 8MB octal PSRAM 属性表达目标 N8R8 配置。因此“构建 PASS”不能单独证明 Wokwi 运行时已经正确初始化 PSRAM，仍需检查启动日志中是否还出现 `PSRAM ID read error`。
- **当前状态：`esp32s3_wokwi` 已记构建 PASS；下一验收门槛是启动 Wokwi 并确认启动身份、传感帧、HX711、气路、电机、安全开关和自动场景。** 运行方法与仿真边界见 [WOKWI.md](WOKWI.md)。

## Wokwi 空板仿真记录（2026-09-22，历史基线）

- 本机 VS Code + PlatformIO 已能正常打开 `hardware/esp32_s3_mvp`，`esp32s3` 环境再次编译成功（7.485 秒）。
- 使用 Wokwi `board-esp32-s3-devkitc-1` 加载 `.pio/build/esp32s3/firmware.bin` 与 `.elf`，ESP32-S3 能正常启动并持续运行。
- `diagram.json` 使用 `serialInterface = USB_SERIAL_JTAG` 后，Wokwi Terminal 能收到固件的原生 USB CDC 输出；已看到启动状态 JSON 及约 1 Hz 的 `sensor` JSON 帧。
- 该轮最初只放置 ESP32-S3，ADS1115、SHT31、MLX90614、BME688、HX711 均未添加，因此 I²C `Error -1`、对应字段为 `null`、`SENSOR_ERROR` 与 `HX711_NO_FRESH_DATA` 属预期；随后加入 Wokwi 原生 HX711 后，已观察到 `hx_samples=10` 且 `HX711_NO_FRESH_DATA` 消失。
- 空板阶段 Wokwi 出现 `PSRAM ID read error`，原因是当时图中未配置目标 N8R8 的8MB octal PSRAM；新的全系统 `diagram.json` 已补上对应板属性，仍待本机复验启动日志。
- 首次仿真出现 NVS `record NOT_FOUND`，表示尚无称重标定记录，符合首次启动预期。
- Wokwi Terminal 输出刷新较快，未完成从 Terminal 手工输入 `{"cmd":"info"}` 的反向命令链路验证；新的 `wokwi.toml` 已增加 RFC2217，后续优先通过上位机或自动场景验证，不再依赖手工抢终端输入。
- 空板阶段启动字符串仍残留 `QY-FW-0.3.0`，而传感帧为 `QY-FW-0.4.0`；全系统代码已改为统一配置常量，并区分 hardware / simulation 身份，待重新编译确认。
- 本节只证明当时“PlatformIO 固件可在 Wokwi 启动、USB CDC 输出正常、原生 HX711可读取”；不证明全系统新仿真环境或任何实物硬件已经验收通过。

## FW-v0.4.0 启动自检支持（2026-09-22）

- `info`新增完整身份、启用掩码及指定I²C地址应答，容量增至1536字节并检查溢出；不调用原scan的气路停止逻辑。
- PlatformIO三种配置全部构建成功：esp32s3 9.374秒、esp32s3_air 9.158秒、esp32s3_motor 9.984秒。motor构建RAM 24856字节、Flash 333965字节。
- 原有CRC/ADC/标定/气路状态机与motor策略原生C++测试通过；主机60项测试通过。
- 未烧录或读取实体I²C设备，未验证真实总线时延及上电输出；编译和fixture不证明接线或安全链通过。恢复步骤及未完成项见[PR #7交接](../../software/host_mvp/HANDOFF.md)。

## FW-v0.3.0 / DRUM-v0.1 软件验证（2026-09-22）

- PlatformIO 6.2.0，固定Espressif32 6.9.0 / Arduino-ESP32 2.0.17，`pio run -d hardware/esp32_s3_mvp -e esp32s3 -e esp32s3_air -e esp32s3_motor`三种配置全部成功，总计52.583秒；motor版静态RAM24856字节、应用332825字节（编译器统计）。
- WinLibs GCC16.1.0，`test/run_core_tests.ps1`运行原core_test与新增motor_test均通过。新增验证参数拒绝、正/反转计数、缓升/占空比限幅、定时结束、必须停稳才能重启、互锁/驱动/MCU/主机/超速/反向/无运动/无效反馈停止、故障锁存与人工复位。
- 上位机51项测试通过，包含motor请求ID、心跳故障模拟和批次收尾联动。
- 未烧录、未接电机/编码器/PNOZ，未测试电气安全链、实际控制周期、停止延迟、PI稳定性、负载、电流或长期运行。按[DRUM-v0.1](../MOTOR_BASELINE.md)执行实物验收后另填记录；以下v0.2记录保留为历史。

日期：2026-09-22。环境：Windows、本地PlatformIO 6.x、Espressif32平台6.9.0、Arduino-ESP32框架2.0.17；上位机使用项目已有Python虚拟环境。

## 已执行

| 项目 | 结果 | 能说明什么 |
|---|---|---|
| `pio run -d hardware/esp32_s3_mvp -e esp32s3` | PASS | 普通采集版编译链接成功 |
| `pio run -d hardware/esp32_s3_mvp -e esp32s3_air` | PASS | 气路版编译链接成功 |
| `test/run_core_tests.ps1` | PASS | 实际core.h逻辑的CRC、符号转换、标定计算和气路策略测试通过 |
| 上位机 `unittest discover` | 12项PASS | 新协议数据保存、错误标记、通道掩码与原有批次流程回归通过 |
| 串口枚举 | 仅发现蓝牙COM口 | 未识别到可确认的ESP32-S3；未执行烧录 |

当前普通版应用镜像约319KB，静态RAM约24KB；它们是编译器估计，不是实测峰值内存或性能。依赖下载缓存和编译结果位于忽略的 `.pio/`，不纳入源码交付。

## 未执行，接板后填写

| 验收项 | 操作及预期 | 状态 |
|---|---|---|
| N8R8启动 | 烧录后持续输出JSON，无重启；检查PSRAM配置与端口 | 待测 |
| 空板行为 | 缺传感器仍发送帧，未接项null、errors非空、所有输出LOW | 待测 |
| I²C地址 | 扫描0x44/45/48/5A/76；扩展板可选0x49 | 待测 |
| ADC电压 | 已知安全电压与万用表比较；验证分压倍率，检查三通道顺序 | 待测 |
| SHT/MLX | 与参考温湿度/温度比较；断开设备输出null | 待测 |
| BME688 | gas-valid及heater-stable后有数据，断开后不保留旧值 | 待测 |
| HX711 | 无模块不阻塞；去皮、标定、多点砝码、持久化、饱和/振动失败 | 待测 |
| 泵阀普通版 | GPIO6闭合也不能通过命令启泵 | 待测 |
| 泵阀air版 | 先假负载；切换先全关→单阀→泵；到时/使能断开/STOP全关 | 待测 |
| 主循环卡顿 | 人为制造可控采集阻塞，独立任务在心跳超时后全关 | 待测 |
| 主机断开 | 已执行命令不超过其时限；重连、seq、JSON完整性 | 待测 |
| 联动采集 | 新上位机保留固件版本、mask、故障、标定参数、完整性报告 | 待测 |
| 长期空跑 | 4–8小时；记录缺包、复位、漂移、气路干扰和磁盘状态 | 待测 |

每项补充实际日期、操作者、硬件版本、仪表/砝码、观测值、原始文件路径及失败处理，未测项目不能改写为通过。编译成功不等于完成硬件验收。
