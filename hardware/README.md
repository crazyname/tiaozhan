# 青韵智控硬件开发入口

当前主控固定为 **ESP32-S3-DevKitC-1 N8R8**。

1. [HW-v0.2 引脚、电源、传感器与泵阀接线规范](PINOUT.md)
2. [引脚分配 CSV](pinout.csv)
3. [真实传感器固件：构建、烧录、标定和联调](esp32_s3_mvp/README.md)
4. [旧版模拟固件](esp32_s3_mock/README.md)：只用于联调，不能生成实验数据。
5. [DRUM-v0.1滚筒人工控制基准](MOTOR_BASELINE.md)：固定电机/驱动器/编码器电平转换、GPIO、心跳及独立急停方案；实物待验收。
6. [启动自检](../software/host_mvp/PREFLIGHT.md)：固件v0.4配合主机v0.9执行只读检查，保留报告；[暂停交接](../software/host_mvp/HANDOFF.md)列明未完成的实物和长期运行验收。

固件已通过交叉编译和软件测试；尚未完成板上烧录、器件接线与长期采集验收。
