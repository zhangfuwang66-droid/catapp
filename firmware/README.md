# ESP32 固件

- `esp32_servo_control/esp32_servo_control.ino`：主程序。
- `config_flash_files/config_flash_files.ino`：配网语音资源烧录工具。
- `flash_files/flash_files.ino`：表情等资源的分批烧录工具，目录内 Python 脚本用于转换资源。
- `partitions.csv`：分区表参考；各 Arduino 草图目录也保留了配套分区表。

Arduino 草图的文件夹名与 `.ino` 文件名保持一致。烧录步骤见 [硬件使用说明](../docs/硬件使用说明.md)，PCB 和外壳文件位于根目录的 `hardware/`。

## 当前测试设备的编译基线

2026-11-05 已对现有源码完成编译验证，尚未将本次编译结果写入设备：

- 开发板：ESP32S3 Dev Module，ESP32 核心 `3.3.12`。
- Flash：8MB，QIO 80MHz 选项；该核心生成的镜像头显示 DIO，与原机镜像一致。
- PSRAM：OPI PSRAM；设备识别为内置 8MB PSRAM。
- USB：Hardware CDC and JTAG，USB CDC On Boot 启用。
- 分区：Custom，使用草图目录内的 `partitions.csv`。程序分区上限为 `2621440` 字节。

从仓库根目录使用本机 Arduino CLI 编译：

```powershell
& 'D:\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe' compile --fqbn 'esp32:esp32:esp32s3:FlashSize=8M,PSRAM=opi,PartitionScheme=custom,CDCOnBoot=cdc,USBMode=hwcdc,FlashMode=qio' --build-property 'upload.maximum_size=2621440' --build-path artifacts/build/firmware-baseline --warnings default --jobs 4 firmware/esp32_servo_control
```

首次编译需准备库与核心缓存，可能耗时数分钟。第三方 ArduinoWebsockets 与 ESP32Servo 在当前核心下有弃用警告，本次没有编译错误。编译通过只证明源码可构建，运行表现仍需实物联调。

完整设备备份位置、校验结果及恢复注意事项见[第二阶段联调记录](../docs/第二阶段联调记录.md)。固件镜像和设备备份均留在已忽略的 `artifacts/`，不提交 Git。

## 第二阶段开发中

主草图增加 `DeviceConfig` 与 `DeviceProvisioning` 模块：芯片派生的稳定编号、独立 NVS 身份、服务器协议/主机/端口、兼容读取旧 Wi-Fi 配置，以及 USB 状态查询和凭证配发。工具与模拟串口测试在 `tools/`、`tests/`，操作说明见 [USB 配置说明](tools/README.md)。

账号连接、配对状态、受保护的设备热点设置页和 TLS 已接入，尚未写入实物。2026-11-05 按原 8MB/OPI/自定义分区配置编译通过：程序 `1433886 / 2621440` 字节，静态变量 `100484 / 327680` 字节；这不是 TLS 运行内存的实测值。镜像文件 `1434032` 字节，ESP32-S3/8MB/DIO/80MHz 镜像头、checksum 与 validation hash 均验证有效。六项 USB 工具模拟测试和设备设置页浏览器测试通过。44 项硬件常量和分区表已与基线比对一致。真实 USB、重启持久化、TLS 并发内存、语音和舵机表现需要后续实物验证。

新增模块分别负责配置、USB、认证连接、WebSocket 并发访问和热点页面，主草图保留现有硬件与音视频逻辑。账号模式下摄像头、音频与播放流携带设备凭证；HTTPS/WSS 验证 CA 与主机名，等待时钟同步，不自动降级明文。热点使用独立 WPA2 密码，配对码只在设备屏幕与热点接口显示。普通串口日志默认关闭以保留 USB 协议完整性；USB 配置命令的接收缓冲增至 2048 字节；LittleFS 挂载失败不再自动格式化。

下一步与拟写入命令见 [第二阶段烧录准备](../docs/第二阶段烧录准备.md)，实际写入必须先核对端口和备份、展示具体文件及影响，再取得用户确认。

本机命令可加 `--config-file 'C:\Users\20388\.arduinoIDE\arduino-cli.yaml'` 使用已安装的核心与 `D:\document\Arduino\libraries`，把 `--build-path` 改为 `artifacts/build/firmware-phase2` 区分新固件；仅执行 `compile` 不会访问串口或烧录设备。
