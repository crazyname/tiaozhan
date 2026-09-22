# 青韵智控采集工作台 v0.3

用于做青实验的串口采集、人工事件、自动拍照和师傅标签记录，支持人工去皮、砝码标定、限时采样/吹扫及停止命令。

## 启动

Windows环境，首次安装：

```powershell
cd D:\tiaozhan\software\host_mvp
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

无需硬件体验（传感器和图像都是模拟数据）：

```powershell
.\.venv\Scripts\python.exe app.py --simulate
```

连接真实设备：

```powershell
.\.venv\Scripts\python.exe app.py --port COM7
```

硬件采用[ESP32-S3-DevKitC-1 N8R8配套固件](../../hardware/esp32_s3_mvp/README.md)，接线按[引脚规范](../../hardware/PINOUT.md)。默认采集固件不启用气路，设备拒绝命令时界面会显示拒绝。

## 操作

1. 点击连接，等待设备信息查询完成。旧mock不支持命令时等待查询超时，再开始被动采集。
2. 真实称重在批次外进行空载去皮，再放置已知砝码并输入质量标定；等待设备报告完成。模拟标定只演示状态流程。
3. 填写批次、操作员、品种、产地和方案编号；未知信息可空。点击开始批次。
4. 默认开始时、每30秒和关键事件拍照；模拟模式保存水印图片，真实模式使用相机0。
5. 点击摇青开始/结束、取样等记录事件。“师傅检查”打开观察表单，未判断保持空值；修订最近标签保留历史。
6. 如启动过气路，先点击停止气路并核对设备，再结束批次。等待保存队列及照片完成，查看检查报告。

**结束批次、断开或退出不等同于物理急停。** 动作命令不会在重连后自动恢复；超时表示结果未知，确认设备状态后断开并重新连接。停止气路命令不取消正在进行的称重标定。

## 数据与检查

数据写入`data/raw/<批次>/`：传感器、事件、图片索引、师傅标签、原始串口、命令、设备日志、元数据及检查报告。连接日志另存`data/device_logs/`，含批次外标定记录。运行数据默认不提交Git，应另行备份。

运行中及失败批次带`INCOMPLETE`标记；成功收尾移除。`writer_summary.json`记录接收/写入/拒收/未写入数量。flush不保证断电零丢失。没有明确来源的旧串口数据标为`serial_unverified`，模拟数据不得用于实验结论。

```powershell
.\.venv\Scripts\python.exe session_check.py ../../data/raw/批次编号
.\.venv\Scripts\python.exe -m unittest discover -p "test_*.py" -v
```

详细实现见[开发文档](DEVELOPMENT.md)，本轮测试见[验证记录](VALIDATION.md)，评分定义见[标签量表](LABEL_SCALE.md)。真实设备和至少8小时连续运行尚待验收；历史回放、分析报告及滚筒控制尚未实现。
