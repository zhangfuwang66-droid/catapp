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
