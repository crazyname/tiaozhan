# Wokwi 全系统功能仿真

本目录提供青韵智控 ESP32-S3 固件的 **全系统功能/集成仿真环境**。目标是在未购买实物板卡和传感器前，验证采集链、JSONL 协议、称重标定、气路状态机、电机闭环策略、安全互锁和上位机通信接口。

> 重要边界：本仿真不是传感器电气精度、真实气敏响应、机械负载或安全认证的替代品。仿真结果不能写成实物测试结果。

## 1. 仿真覆盖范围

| 模块 | 仿真方式 | 覆盖内容 | 不覆盖内容 |
|---|---|---|---|
| ESP32-S3 | Wokwi 原生 ESP32-S3 | 固件启动、FreeRTOS、GPIO、USB CDC、NVS、定时与协议 | 实板供电、EMC、USB线材等 |
| 8MB Flash / 8MB OPI PSRAM | Wokwi 板属性 | 与 N8R8 配置匹配的启动环境 | 真实芯片批次和高速时序裕量 |
| HX711 + 5kg 称重 | Wokwi 原生 HX711 | 固件真实 GPIO 位时序、采样、去皮、砝码标定、NVS | 实际称重梁非线性、温漂、蠕变、机械振动 |
| ADS1115 三路气敏输入 | `QY_WOKWI_SIM` 功能模型 | 三通道独立动态值、ADC量纲、上位机数据链 | ADS1115真实I²C寄存器时序、TGS真实气敏曲线 |
| SHT31 ×2 | `QY_WOKWI_SIM` 功能模型 | 环境/腔体温湿度动态、字段和状态估计输入 | SHT31真实I²C命令与CRC、电气误差 |
| MLX90614 | `QY_WOKWI_SIM` 功能模型 | 叶温动态、数据链和状态估计输入 | SMBus PEC及真实红外测温误差 |
| BME688 | `QY_WOKWI_SIM` 功能模型 | 气体电阻动态、数据链和状态估计输入 | Bosch内部校准、加热器物理过程、真实气体选择性 |
| 泵 / 采样阀 / 吹扫阀 | 真实固件状态机 + LED | 互斥、切换延时、限时、使能和主循环心跳保护 | 流量、压力、阀响应时间和继电器/驱动电气特性 |
| 电机 / 编码器 | 真实控制策略 + 一阶数学被控对象 | PI控制、缓升、反馈、方向、超时、故障锁存和互锁 | 电机电气模型、齿轮间隙、惯量、负载扰动、电流 |
| 安全输入 | Wokwi 滑动开关 | Air Enable、Motor Enable、Guard、Driver Fault | 安全继电器认证和硬接线失效模式 |

`esp32s3_wokwi` 输出明确标记：

```text
hardware = QY-WOKWI-SIM
source_mode = simulation
```

因此仿真数据不会与真实硬件数据混淆。

## 2. 文件

```text
platformio.ini          新增 esp32s3_wokwi 环境
diagram.json            ESP32-S3、HX711、互锁开关和输出指示灯
wokwi.toml              Wokwi 固件路径和 RFC2217 串口转发
wokwi_full_demo.yaml    自动去皮/标定/气路/电机演示场景
include/sim_profile.h   仿真接口
src/sim_profile.cpp     做青过程输入与电机被控对象
```

真实固件环境 `esp32s3`、`esp32s3_air`、`esp32s3_motor` 不启用 `QY_WOKWI_SIM`，仍走真实驱动路径。

## 3. 第一次运行

在仓库根目录执行：

```powershell
Set-Location D:\tiaozhan
& "$env:USERPROFILE\.platformio\penv\Scripts\pio.exe" run -d hardware/esp32_s3_mvp -e esp32s3_wokwi
```

成功后，用 VS Code 打开：

```text
D:\tiaozhan\hardware\esp32_s3_mvp
```

然后：

```text
Ctrl+Shift+P
→ Wokwi: Start Simulation
```

`wokwi.toml` 已指向：

```text
.pio/build/esp32s3_wokwi/firmware.bin
.pio/build/esp32s3_wokwi/firmware.elf
```

## 4. 图中开关和指示灯

滑动开关默认位置已经设置为正常可运行状态：

- `swAir`：左侧 = GPIO6 LOW = 气路允许；右侧 = 禁止。
- `swMotorEnable`：左侧 = GPIO7 LOW = 电机允许；右侧 = 禁止。
- `swMotorGuard`：左侧 = GPIO21 LOW = 防护条件满足；右侧 = 互锁断开。
- `swMotorFault`：左侧 = GPIO15 HIGH = 驱动器健康；右侧 = LOW = 注入驱动器故障。

输出 LED：

- 红：泵 GPIO16
- 绿：采样阀 GPIO17
- 蓝：吹扫阀 GPIO18
- 橙：电机 PWM GPIO10，亮度随占空比变化
- 青：电机方向 GPIO11
- 黄：电机 SLEEP GPIO12

## 5. 仿真过程数据

仿真过程使用 120 秒循环，仅用于让上位机和控制链看到连续变化，不代表凤凰单丛真实工艺曲线：

- 三路气敏 ADC：分别从不同基线缓慢变化并叠加小幅周期扰动；
- 环境温湿度：小幅波动；
- 腔体温度：总体上升；
- 腔体湿度：总体下降；
- 叶温：缓慢上升；
- BME688 气体电阻：总体上升。

Wokwi 构建的预热标志仅保留 5 秒，便于功能演示；真实固件仍保留 20 分钟工程标记。

## 6. HX711 实际 Wokwi 模型

HX711 没有走软件假数据。`diagram.json` 使用 Wokwi 原生 `wokwi-hx711`，固件仍执行真实的 24 位 GPIO 读取和第 25 个增益脉冲。

运行时点击 HX711 可以改变负载。自动演示场景会按以下顺序：

```text
0 kg → tare
1.0 kg → calibrate 1000 g
0.5 kg → 验证约 500 g
```

这验证的是协议与标定逻辑，不代表真实称重精度。

## 7. 自动演示场景

`wokwi_full_demo.yaml` 自动执行：

```text
info
→ HX711 0kg 去皮
→ 1kg 砝码标定
→ 0.5kg 验证
→ 3s 采样气路
→ 15rpm / 3s 电机控制 + 主机心跳
```

Automation Scenarios 需要 Wokwi CLI 和 CI token。交互式 VS Code 仿真不要求用这个文件。

安装 Wokwi CLI 后可执行：

```powershell
wokwi-cli hardware/esp32_s3_mvp --scenario wokwi_full_demo.yaml --timeout 25000
```

建议先执行：

```powershell
Set-Location D:\tiaozhan\hardware\esp32_s3_mvp
wokwi-cli lint
```

检查 `diagram.json` 的器件和引脚名称。

## 8. 与上位机直连

`wokwi.toml` 已启用：

```text
rfc2217ServerPort = 4000
```

Wokwi 运行时，Python 可以直接连接虚拟 ESP32-S3：

```python
import serial
ser = serial.serial_for_url("rfc2217://localhost:4000", baudrate=115200)
```

这样可以在没有开发板时继续验证：

```text
PySide6 上位机 ↔ RFC2217 ↔ Wokwi ESP32-S3 ↔ 固件
```

注意模拟器标签页必须保持可见，否则 Wokwi 可能暂停。

## 9. 为什么不转 Proteus

当前固件已经是 PlatformIO + Arduino-ESP32 的 ESP32-S3 二进制，并已能在 Wokwi 直接运行。Wokwi 还原生支持 ESP32-S3、USB CDC、PSRAM、GPIO 和 HX711，并允许用独立仿真环境补齐系统输入。

因此当前阶段继续 Wokwi 的迁移成本更低，也更容易保持“真实固件环境”和“仿真环境”严格隔离。若后续需求变成模拟电源、MOSFET驱动、模拟前端等电路级行为，再单独使用 Proteus / SPICE 类工具，而不是为了传感器流程仿真整体迁移固件。

## 10. 尚需实物验证

以下内容无论 Wokwi 是否通过，都必须等实物硬件到手后重新验收：

- ADS1115 / SHT31 / MLX90614 / BME688 的真实总线时序和测量精度；
- TGS 气敏传感器预热、漂移、交叉敏感性和气路响应；
- HX711 与称重梁的多点标定、重复性和温漂；
- 泵阀流量、响应时间和气密性；
- 电机、编码器、减速机构、驱动器、电流和机械负载；
- 急停、使能、安全继电器等硬件安全链；
- 4–8 小时长期采集稳定性。
