# ESP32 固件

- `esp32_servo_control/esp32_servo_control.ino`：主程序。
- `config_flash_files/config_flash_files.ino`：配网语音资源烧录工具。
- `flash_files/flash_files.ino`：表情等资源的分批烧录工具，目录内 Python 脚本用于转换资源。
- `partitions.csv`：分区表参考；各 Arduino 草图目录也保留了配套分区表。

Arduino 草图的文件夹名与 `.ino` 文件名保持一致。烧录步骤见 [硬件使用说明](../docs/硬件使用说明.md)，PCB 和外壳文件位于根目录的 `hardware/`。

## 编译配置

以下配置已完成源码编译验证，适用于对应的 ESP32-S3 / 8MB Flash / 8MB OPI PSRAM 硬件。使用其他板型前，需要核对实际芯片、接线与存储规格：

- 开发板：ESP32S3 Dev Module，ESP32 核心 `3.3.12`。
- Flash：8MB，QIO 80MHz 选项；该核心生成的镜像头显示 DIO。
- PSRAM：OPI PSRAM，8MB。
- USB：Hardware CDC and JTAG，USB CDC On Boot 启用。
- 分区：Custom，使用草图目录内的 `partitions.csv`。程序分区上限为 `2621440` 字节。

安装相应核心和项目依赖库后，从仓库根目录编译。示例要求 `arduino-cli` 已加入 PATH；也可以替换为本机可执行文件路径：

```powershell
arduino-cli compile --fqbn 'esp32:esp32:esp32s3:FlashSize=8M,PSRAM=opi,PartitionScheme=custom,CDCOnBoot=cdc,USBMode=hwcdc,FlashMode=qio' --build-property 'upload.maximum_size=2621440' --build-path artifacts/build/firmware --warnings default --jobs 4 firmware/esp32_servo_control
```

首次编译需要准备库与核心缓存。第三方 ArduinoWebsockets 与 ESP32Servo 在上述核心下存在弃用警告，应结合完整日志判断结果。`compile` 不访问串口或烧录设备；编译成功也不能替代实物验收。

## 设备身份与网络

主草图中的 `DeviceConfig` 与 `DeviceProvisioning` 提供芯片派生的稳定编号、独立 NVS 身份、服务器协议/主机/端口配置、旧 Wi-Fi 配置兼容，以及 USB 状态查询和凭证配发。工具和模拟串口测试位于 `tools/`、`tests/`，操作说明见 [USB 配置说明](tools/README.md)。

账号模式下，摄像头、音频与播放流携带设备凭证。HTTPS/WSS 验证 CA 与主机名，等待时钟同步，不自动降级明文。设备热点使用独立 WPA2 密码，配对码只在设备屏幕与受保护的热点接口显示；普通串口日志默认关闭，以保留 USB 协议完整性。

已有编译、USB 模拟协议和设置页浏览器测试，并已在实物验证凭证配发、网络配置重启持久化与公网账号绑定。TLS 并发内存、摄像头、语音与舵机的完整表现仍待实物验收。LittleFS 挂载失败时不会自动格式化。

排查无声或无画面时，可通过 USB 的 `diagnostics` 命令读取硬件初始化结果、内存、WebSocket 阶段和音视频计数，详见 [只读运行诊断](tools/README.md#只读运行诊断)。此命令需要支持诊断的新主程序；普通串口日志保持关闭，诊断输出不含凭证或聊天内容。

## 升级与恢复

- 升级已有设备前，保存完整 Flash 备份，并记录芯片、容量、分区布局、文件大小与 SHA-256。源码标签不能替代设备备份；完整 Flash 可能包含 Wi-Fi 和设备凭证，必须私密保存。
- 本仓库分区表中，`app0` 从 `0x10000` 开始、大小为 `0x280000`；文件系统从 `0x290000` 开始。先确认设备实际布局一致，再确定写入范围。
- 仅升级主程序时，不应顺带写入 bootloader、分区表或文件系统，也不要直接使用 `merged.bin` 或全盘擦除。首次烧录与资源写入应按对应硬件说明分别处理。
- 写入前重新识别端口，核对目标镜像及哈希。重启时舵机可能初始化回中，需保持设备平稳、供电和 USB 连接稳定。
- 完成后先验收屏幕、表情、音频、摄像头与舵机，再进行身份配发、账号绑定和 HTTPS/WSS 联调。TLS 与音视频并发的运行内存需实测，不能用编译期静态内存数字代替。

固件镜像、编译日志和设备备份保存在已忽略的 `artifacts/`，不提交 Git。镜像必须与当前源码对应；源码变更后需重新构建和校验。
