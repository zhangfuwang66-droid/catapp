# ESP32 固件

- `esp32_servo_control/esp32_servo_control.ino`：主程序。
- `config_flash_files/config_flash_files.ino`：配网语音资源烧录工具。
- `flash_files/flash_files.ino`：表情等资源的分批烧录工具，目录内 Python 脚本用于转换资源。
- `partitions.csv`：分区表参考；各 Arduino 草图目录也保留了配套分区表。

Arduino 草图的文件夹名与 `.ino` 文件名保持一致。烧录步骤见 [硬件使用说明](../docs/硬件使用说明.md)，PCB 和外壳文件位于根目录的 `hardware/`。
