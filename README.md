# 青韵智控 | QingYun Tea Control

**凤凰单丛做青多模态感知与自适应控制系统**  
*Multimodal Sensing and Adaptive Control Research Platform for Phoenix Dancong Tea Processing*

[English](README.en.md) · [快速开始](docs/GETTING_STARTED.md) · [系统架构](docs/ARCHITECTURE.md) · [验证状态](docs/PROJECT_STATUS.md) · [参与贡献](CONTRIBUTING.md)

青韵智控是一个面向凤凰单丛乌龙茶**做青阶段（摇青与静置）**的开源工程研究项目，探索将多传感器采集、过程数据留痕、人工操作事件、工艺状态估计与执行控制组织为可复现的研发平台。**本项目不覆盖杀青、揉捻、干燥等工序。**

> **当前是研究原型，不是已完成的自主制茶系统。** 仓库中的固件、上位机、测试、仿真和实验记录具有不同的验证等级。跨批次做青状态识别、基于真实鲜叶状态的闭环执行和成茶品质改善，尚未完成相应实证。请勿将模拟数据、代码编译或处理结果表误写为实物系统的工艺效果。

## 已提供的工程模块

| 模块 | 位置 | 作用 |
| --- | --- | --- |
| ESP32-S3 采集与执行固件 | [hardware/esp32_s3_mvp](hardware/esp32_s3_mvp) | 三路 TGS 气敏 + BME688、SHT31、MLX90614、HX711 接口；泵阀及可选滚筒控制逻辑 |
| 硬件配置与接线 | [hardware](hardware) | 引脚、电气规范、器件接线及滚筒基准 |
| Python / PySide6 上位机 | [software/host_mvp](software/host_mvp) | 串口采集、批次档案、事件及标签、相机、数据回放、工程分析 |
| 软件与固件测试 | [software/host_mvp 单元测试](software/host_mvp) · [hardware/esp32_s3_mvp/test](hardware/esp32_s3_mvp/test) | 合成场景、业务逻辑和协议行为回归 |
| 测试结果与证据约束 | [data/hardware_tests](data/hardware_tests) | 已提供的**处理结果工作簿**及其局限性、复核口径 |
| 竞赛和研究资料 | [docs](docs) · [submission](submission) | 实验 SOP、研究计划、证据台账、挑战杯申报及答辩归档 |

设计中的总体流程为：

**传感器采集 → 质量标记与批次记录 → 状态估计 → 决策建议 → 人工确认/执行 → 验证反馈**

这里的状态估计与自动闭环属于目标架构，**不表示整条链路目前已完成或已通过真实做青验证**。

## 快速体验（不需要硬件）

建议先运行**模拟模式**熟悉数据流程；模拟记录不能作为真实茶叶实验结果。

1. 安装 Python 3.11/3.12，克隆本仓库。
2. 在仓库根目录创建虚拟环境并安装上位机依赖。
3. 进入上位机目录，运行模拟模式。

~~~powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r software/host_mvp/requirements.txt
cd software/host_mvp
..\..\.venv\Scripts\python.exe app.py --simulate
~~~

Linux/macOS 请使用 `./.venv/bin/python` 替换 Windows 的解释器路径。真实设备启动、串口参数、PlatformIO 构建和风险要求见[快速开始](docs/GETTING_STARTED.md)及各模块 README。

## 验证状态与事实边界

- **代码已实现 ≠ 已上板。** 当前 ESP32-S3 固件文档记录了构建、纯逻辑和接口兼容性验证，但该固件版本的烧录、接线、测量精度、完整安全链及长期运行仍须独立验收。
- **上位机可模拟 ≠ 已完成真实做青。** 批次记录、回放、分析及异常处理已有软件实现，实体相机、设备和长时工作需以实测记录为准。
- **测试结果表 ≠ 原始证据全量公开。** `data/hardware_tests/数据结果.xlsx` 是处理结果文件，详情及“是否可写”等限制见[数据说明](data/hardware_tests/README.md)。
- **人工滚筒控制 ≠ 自适应闭环。** 自动状态驱动执行、真实工艺对照 A/B/C 和成茶品质增益尚未验证。
- **离线建模预研**目前存在于独立[PR #9](https://github.com/crazyname/tiaozhan/pull/9)，未进入默认分支，也不能作为生产控制能力。

详见[PROJECT_STATUS](docs/PROJECT_STATUS.md)。任何统计数字应回溯到其文件、试验条件和核验级别。

## 仓库导航

~~~text
hardware/              ESP32-S3固件、接线与电机控制基准
software/host_mvp/     PySide6采集工作台、回放、测试
scripts/               数据与答辩指标一致性复核
data/hardware_tests/   经整理的测试结果与说明（非全量原始数据）
docs/                  架构、快速开始、实验SOP和研发文档
submission/            挑战杯申报书与答辩资料（保留历史）
.github/               自动检查和社区协作模板
~~~

参与开发前请阅读 [CONTRIBUTING](CONTRIBUTING.md)；安全或隐私相关问题请阅读 [SECURITY](SECURITY.md)。研发计划请参考[硬件测试后完整研发路线与验收标准](docs/13_硬件测试后完整研发路线与验收标准.md)。

## 许可证、来源与使用风险

项目贡献者原创的**软件、固件、脚本**采用 [MIT License](LICENSE)；许可证的具体适用范围和素材/数据例外见 [NOTICE](NOTICE.md)。竞赛文档、演示图像、处理数据和其他第三方素材，**不应因仓库公开而自动视为可自由再分发或商用**。

涉及电机、泵阀与电气设备的演示和控制逻辑仅供研发，**不构成安全认证**。独立硬件急停、供电隔离、故障停机与实物验收须自行完成；请勿将软件 STOP 或串口断连当作物理急停。

欢迎通过 Issues 交流可复现的缺陷、文档问题及研究方法，贡献时请区分模拟、台架和实际茶叶实验。
