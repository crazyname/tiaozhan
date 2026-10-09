# 青韵智控：数据处理、分批次建模和影子建议（软件预研）

更新：2026-10-09。权威边界：[硬件测试后完整研发路线](../docs/13_硬件测试后完整研发路线与验收标准.md)。

**状态：已编写离线程序并通过合成数据单元测试；未建立真实做青状态模型，未完成新一轮实物/安全验收。所有新增代码默认不执行任何电机动作。**

## 运行环境

在仓库根目录：

~~~powershell
python -m pip install -r software/host_mvp/requirements.txt
python -m pip install -r software/modeling/requirements.txt
python -m unittest discover -s software/tests -v
~~~

保留原有 ESP32-S3 固件、PySide6 主机、串口采集、批次/图像/标签/回放功能，不修改任何原始 CSV 或固件协议。

## 1. 只读特征流水线（G3 基础）

~~~powershell
python -m software.analysis.pipeline data/raw/BATCH_XXX --output data/processed --window 60 --step 30
~~~

从既有批次的 meta.yaml、sensor_1hz.csv、events.csv 和 master_labels.csv 读取数据，在新的唯一目录生成 features.csv 与 manifest.json。后者记录输入/输出 SHA256、处理版本、主机时间基准、源批次及计算参数。任何情况下不得改动 data/raw。

处理逻辑：
- 使用主机单调时间作为优先时间轴；仅利用真实观测的半开窗口，不补点，不跨时间轴补造精度。
- 质量标记仅为 OK 的原始记录进入统计；缺失值以空值/available 标志保留。
- 四路 MOS 电压必须通道掩码有效且泵开、采样阀开、吹扫阀关才计算窗口中位数与斜率。
- 质量仅在事件表示为静置，且 motor_running 明确为 false 时才统计。m0 必须由批次 meta.initial_mass_g 显式给出，缺失不推断。
- 跨摇青/静置事件转换窗口不生成静置质量特征；尚未处理的称重振动、气路残留、湿度漂移仍须在真实数据审查中单列排除。
- 保留 host 的来源标识，包括 simulate、serial_unverified 和 hardware。固件自称 hardware **不等于**人工确认的物理测量可信；此类来源保留来源待核验警告。

当前未实现湿度补偿、视觉量化、叶温校准与机器学习意义上的状态估计。批次数据按完整文件载入内存；大数据前需压测。

## 2. 分批次基线建模（G3 离线）

在可信数据获得后，由人工把处理结果与经专家审核的标签按采样时刻和事件整理成 curated.csv。**不允许将事件阶段 event_phase 自动当成专家真值。**

最低字段：

~~~text
batch_id,source_mode,stage_label,gas_1_v_median,gas_2_v_median,gas_3_v_median,gas_4_v_median,ambient_temp_c_median,ambient_rh_pct_median,relative_mass_loss_pct,last_shake_elapsed_s
~~~

其中 source_mode=physical 只能由核对实际设备/原始证据的人员在人工审核表内确定，不能改写原始 meta。stage_label 仅能填经审核的 initial / shaking / resting / ready_for_fixation。不足三个独立真实批次程序拒绝评估；各批仍需进一步防范同日同原料相关性。

~~~powershell
python -m software.modeling.evaluate curated.csv --output data/reports/grouped_01.json
~~~

比较多数类、仅摇青历史及全部当前输入的逻辑回归基线；按 batch_id 留一批次验证，在每个训练折内独立拟合缺失值填补，导出 macro-F1、混淆矩阵、逐批预测与失败折。输出**不是**可部署模型，也不包含概率校准或品质提升结论。

只有测试合成样例时才可以添加 --allow-simulation 参数；默认拒绝未经核实的来源。

## 3. 影子工艺建议（G4 非执行）

~~~powershell
python -m software.decision.shadow observation.json --audit-dir data/reports/shadow
~~~

observaton.json 是外部状态估计/审核系统提供的单份 JSON，必要字段包括 observed_at_iso、source_mode、quality_flag、stage、confidence、model_validated、calibration_verified、tea_type、motor_running、motor_fault、hardware_interlock_verified、last_shake_elapsed_s、batch_id。

默认未提供专家批准的规则文件，输出 REVIEW_REQUIRED。即使未来传入 --expert-limits 和人工验收全部通过，结果也仅为 CONTINUE_REST 或 REVIEW_SHAKE_CANDIDATE 的影子预览。拒绝条件包括数据超时（5 秒）、来源未核验、低置信度（小于0.8）、标定/互锁状态未知、专家规则未批准、休止间隔不足和电机状态不安全。推荐范围不是机器命令。每次建议存独立 JSON，人工复核通过 append_review 追加审计日志。

模块没有设备导入或串口/电机调用，所有建议始终携带 actuator_command=null、dispatched=false。人的 accept_for_review 也仅表示接受审查，**不触发滚筒**。

## 4. 与 G0—G7 验收门槛的关系

- G0/G1：真实接线、参考仪表校准、气敏稳定性、气路清洗/残留、长时采集与证据关联未由本软件验证。
- G2：完整真实做青、专业标签与跨批次数据尚待收集；没有这些，离线模型不得声称有效。
- G3：本代码完成可重算特征和保守的分组评估入口，尚不具备已验证的有效状态预测、置信度校准或消融成果。
- G4：本代码仅提供 CLI 影子逻辑和人工复核存储，尚未接入 PySide6 生产界面。
- G5/G6：独立急停/门盖/继电器、电机反馈与真实品质对照属于将来的硬件和实验验收，**不属于本次程序交付**。

所有真实原始数据及个人/企业信息保持在受控存储，公开仓库仅提交代码、文档和合成数据测试。新增代码不会把已有作品材料更改为未证实的“自动做青成功”。
