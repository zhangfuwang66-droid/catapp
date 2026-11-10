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

### 小鹿设备版本

同款 ESP32-S3 硬件可从同一主草图编译鹿版本。须先核对屏幕、摄像头、麦克风、功放、四路舵机接线与现有引脚定义一致；芯片与分区相同不能证明外设兼容。

```powershell
arduino-cli compile --fqbn 'esp32:esp32:esp32s3:FlashSize=8M,PSRAM=opi,PartitionScheme=custom,CDCOnBoot=cdc,USBMode=hwcdc,FlashMode=qio' --build-property 'upload.maximum_size=2621440' --build-property 'compiler.cpp.extra_flags=-DCYBERMIAO_DEVICE_DEER=1' --build-path artifacts/build/firmware-deer --warnings default --jobs 4 firmware/esp32_servo_control
```

`DeviceProfile.h` 默认使用猫配置，鹿编译选项只改变设备类型、编号前缀、热点名称、屏幕连接标题和配网页品牌，不改接线、分区或音视频协议。猫编号保持 `cat-<芯片后缀>`，鹿使用 `deer-<芯片后缀>`，热点为 `CyberDeer-<后六位>`。USB `status` 返回 `device_type`、`product_name`；原凭证只有编号完全一致时才会加载，不能在猫、鹿间复用。

仅更新 `0x10000` 主程序可保留原文件系统和 NVS，但原表情的目录及帧格式仍须兼容主草图。`diagnostics` 的 `filesystem_ready`、`animation_sets`、`animation_frames` 和 `psram_bytes` 可辅助核对挂载、动画扫描和 PSRAM；这些计数不能代替查看屏幕、试听及检查舵机。不得因为资源不兼容而自动格式化或覆盖原资源。

鹿使用独立设备凭证登记到同一后端，然后由账号通过配对码绑定。写入鹿程序并不等于已经登记、联网或完成云端验收，首次身份配发与网络配置仍按 [USB 配置说明](tools/README.md) 操作。

设备类型测试可用原生 C++ 编译器分别运行 `firmware/tests/device_profile_test.cpp`（默认猫）和带 `-DCYBERMIAO_DEVICE_DEER=1` 的鹿版本。USB 模拟测试包含跨类型凭证拒绝，设置页浏览器测试覆盖两种品牌与鹿的保存回执。

## 设备身份与网络

主草图中的 `DeviceConfig` 与 `DeviceProvisioning` 提供芯片派生的稳定编号、独立 NVS 身份、服务器协议/主机/端口配置、旧 Wi-Fi 配置兼容，以及 USB 状态查询和凭证配发。工具和模拟串口测试位于 `tools/`、`tests/`，操作说明见 [USB 配置说明](tools/README.md)。

账号模式下，摄像头、音频与播放流携带设备凭证。HTTPS/WSS 验证 CA 与主机名，等待时钟同步，不自动降级明文。设备热点使用独立 WPA2 密码，配对码只在设备屏幕与受保护的热点接口显示；普通串口日志默认关闭，以保留 USB 协议完整性。

已有编译、USB 模拟协议和设置页浏览器测试，并已在实物验证凭证配发、网络配置重启持久化与公网账号绑定。LittleFS 挂载失败时不会自动格式化。

配网流程与换网恢复见 [设备热点与账号配对](tools/README.md#设备热点与账号配对)。新版页面提供网络选择面板和折叠的高级设置，请求超时后可重试；保存回执与实际联网结果分别提示。配网期间暂缓未成功的旧网络重试，避免打断扫描和手机请求。

2026 年 11 月 10 日，配网页与时序修复已完成 13 组浏览器模拟场景及 ESP32-S3 编译验证，并在一台实物上仅更新主程序。烧录前核对了旧程序、分区、完整 Flash 备份并另存当前 NVS；写入哈希校验通过。用户随后确认能看到新版页面、扫描和保存均正常；USB 诊断确认保存重启后已连接 Wi-Fi、完成时钟同步、恢复原账号绑定，摄像头与音频 WebSocket 在线且有收发计数，摄像头、I2S 和任务创建未报告初始化错误。本轮未单独验收实际画面与听感、错误密码恢复、再次开机自动重连及更多手机和网络；连接状态与计数不能代替这些实测。

TLS 并发连接在实物诊断中曾出现 `MBEDTLS_ERR_SSL_ALLOC_FAILED`（`-32512`）；主程序在启动网络任务前配置 mbedTLS 优先使用 PSRAM，分配失败时才回退内部堆，给 DMA、Wi-Fi 和任务栈保留内部内存。该设置同时适用于账号查询、音视频 WSS 和播放 HTTPS，保留原有证书验证。无 PSRAM 时保留核心默认分配器。2026 年 11 月 9 日，单台设备已确认公网摄像头画面和语音回答恢复。

同日，播放缓冲版已完成编译、源码与构建副本核对及主程序烧录，写入哈希校验通过；原账号绑定、Wi-Fi、音视频 WSS 在重启后恢复。针对语音中途停顿进行“连贯地从一数到二十”的单机试听，用户确认“连续顺畅，原来的卡顿明显改善”。随后读取的本次启动累计诊断显示：已向 I2S 提交 645120 字节 PCM，64 KiB 缓冲分配成功，重新缓冲、读取超时、HTTP 解析错误和 I2S 写入重试均为 0；网络预取调用最大耗时为 8ms，连续音频块的最大供数间隔为 21ms。供数间隔不等同于 DMA 欠载测量；这些结果支持本次试听改善，不能代替多网络、长时间及其他设备的验收。完整音质、舵机与长期稳定性仍需继续验证。

播放端的 `HttpAudioBody.h` 持续保存 HTTP 分块解析状态，短超时返回已收到的音频字节，不丢弃半个分块长度行、PCM 或 CRLF。播放循环保留半个 16 位采样，并消费不足 20 毫秒的结尾。协议解析的原生 C++ 测试不需要硬件，可在仓库根目录执行：

```powershell
clang++ -std=c++17 -Wall -Wextra -Werror firmware/tests/http_audio_body_test.cpp -o artifacts/build/http_audio_body_test.exe
artifacts/build/http_audio_body_test.exe
```

也可使用兼容的 `g++` 或 `zig c++`。测试覆盖网络延迟、任意位置分片、非分块响应、末尾短块与错误格式；通过不代表实际扬声器音质已验证。

`PcmPlaybackBuffer.h` 为播放循环提供有界 PCM 环形缓冲。优先在 PSRAM 分配 64 KiB，分配失败时使用 8 KiB 内部内存；两者都失败则结束本次播放并增加诊断计数。每轮非阻塞预取最多 4096 字节，再向 I2S 写入 20ms 音频，避免读取不足一块时等待网络而直接饿住扬声器。正常配置先积累 320ms 音频后播放；短回复在 HTTP 结束后直接消费，缓冲不足时重新积累，容量不足 320ms 时以实际容量为上限。不改变采样率或音量，不补零；连接结束后排空收到的完整采样。缓冲只能吸收有限抖动，不能保证在长时间网络中断或 TLS 调用阻塞时没有停顿。

缓冲与真实 HTTP 解析器的组合测试覆盖短时网络停顿、较长断流后的恢复、缓冲背压与回绕、奇数分块、短回复、不同采样率和截断响应：

```powershell
clang++ -std=c++17 -Wall -Wextra -Werror firmware/tests/pcm_playback_buffer_test.cpp -o artifacts/build/pcm_playback_buffer_test.exe
artifacts/build/pcm_playback_buffer_test.exe
```

排查无声或无画面时，可通过 USB 的 `diagnostics` 命令读取硬件初始化结果、内存、WebSocket 阶段和音视频计数，详见 [只读运行诊断](tools/README.md#只读运行诊断)。此命令需要支持诊断的新主程序；普通串口日志保持关闭，诊断输出不含凭证或聊天内容。

## 升级与恢复

- 升级已有设备前，保存完整 Flash 备份，并记录芯片、容量、分区布局、文件大小与 SHA-256。源码标签不能替代设备备份；完整 Flash 可能包含 Wi-Fi 和设备凭证，必须私密保存。
- 本仓库分区表中，`app0` 从 `0x10000` 开始、大小为 `0x280000`；文件系统从 `0x290000` 开始。先确认设备实际布局一致，再确定写入范围。
- 仅升级主程序时，不应顺带写入 bootloader、分区表或文件系统，也不要直接使用 `merged.bin` 或全盘擦除。首次烧录与资源写入应按对应硬件说明分别处理。
- 写入前重新识别端口，核对目标镜像及哈希。重启时舵机可能初始化回中，需保持设备平稳、供电和 USB 连接稳定。
- 完成后先验收屏幕、表情、音频、摄像头与舵机，再进行身份配发、账号绑定和 HTTPS/WSS 联调。TLS 与音视频并发的运行内存需实测，不能用编译期静态内存数字代替。

固件镜像、编译日志和设备备份保存在已忽略的 `artifacts/`，不提交 Git。镜像必须与当前源码对应；源码变更后需重新构建和校验。
