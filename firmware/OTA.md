# 小鹿远程更新

本轮先支持同款 ESP32-S3、8 MB Flash、8 MB OPI PSRAM 的小鹿。小猫继续使用原分区和 USB 升级，原性格、表情及用户记忆不随固件发布转移。

这是源码与离线工具说明。首次分区迁移、真实远程升级、异常启动回退、手动回退及最终听感仍需实机验收；本地模拟测试和编译通过不能代替这些验收。

## 使用方式

设备首次通过 USB 安装 OTA 版本并联网后，主人在账号控制台的“设备更新”中查看当前版本、更新说明、任务进度，可手动安排升级或回退到另一个已验证槽。默认手动升级；可选连续空闲 5 分钟后自动升级。借用期间不允许安排更新。

离线时任务保存在服务器数据库，下次设备联网后领取。升级期间暂停对话与摄像头上传，完成并通过自检后重连。失败或回退的版本不自动反复安装，主人仍可手动重试。固件升级不能解决百炼账号额度、设备断网等外部问题。

## 双分区与恢复边界

- `app0`：`0x10000`，2 MB；`app1`：`0x210000`，2 MB。
- LittleFS：`0x410000`，`0x3f0000` 字节。NVS 仍为 `0x9000`～`0xe000`。
- 首次迁移会改 bootloader、分区表、启动记录、应用槽和文件系统，需要保持 USB 与稳定供电。先读取并回读验证完整 8 MB Flash，再搬迁并逐文件核验全部资源，保留当前 NVS。首次 USB 安装的启动槽为 VALID；它没有兼容旧分区的自动回退槽，失败时用整机备份经 USB 恢复。
- 后续 OTA 只写备用应用槽。下载、签名或摘要验证失败不会切换启动槽。候选启动时等待外设稳定及 HTTPS 成功上报，至少稳定 30 秒后确认；3 分钟未通过则回退。待确认程序异常重启也由 bootloader 回退。
- 手动回退需要另一个槽已有已验证且不同版本的程序。首次 USB 安装后暂不能回退；完成一次 OTA 后才具备旧版本槽。新旧版本应维持 NVS 与资源格式兼容，不能随意加入不可逆迁移。

## 发布签名

使用 P-256 ECDSA 和 SHA-256。设备内置公钥，校验同源 HTTPS、硬件型号、分区布局、递增发布序号、镜像大小、发布签名和整包摘要。更新说明摘要也包含在签名中。设备归属变化、借用或凭证撤销后旧任务失效，下载中及切换启动槽前重新核验授权。

私钥仅在发布者电脑的私密目录保存，另做安全备份，禁止提交 Git、上传服务器或放入设备。此处是软件发布签名，不等同于启用 ESP32 硬件 Secure Boot；本轮不烧写 eFuse。

## 离线构建和制作发布包

使用 ESP32 Arduino 核心 3.3.12，安装 `server/requirements.txt` 与 `firmware/tools/requirements.txt`。以下命令仅在本地构建，不连接串口。路径参数替换成实际路径；先提交源码，确保构建来源可追溯。

```powershell
python firmware/tools/build_ota.py --cli <arduino-cli.exe> --work artifacts/ota/build-v1 --version 0.3.0-ota.1 --sequence 1
python firmware/tools/ota_release.py package --image artifacts/ota/build-v1/build-deer/esp32_servo_control.ino.bin --private <私密签名密钥.pem> --version 0.3.0-ota.1 --sequence 1 --notes <中文更新说明.txt> --output artifacts/ota/releases/deer-0.3.0-ota.1
```

每次正式发布使用新的版本号和递增序号，保留原发布包。`build_ota.py` 从已提交源码生成独立构建副本，使用 `partitions-ota.csv`，不覆盖猫的分区。`ota_release.py keygen` 可创建新部署所用的私钥、公钥和设备头文件；已有设备不能随意换公钥。

## 首次迁移与云端发布

`ota_migrate.py prepare --help` 用经过校验的原整机备份、旧应用、签名首版、LittleFS 工具和构建结果制作指定设备的私密迁移包。`migrate` 默认仅检查材料；加入 `--approved-partition-migration` 才访问串口和写 Flash。不得将它与旧的“只写主程序”工具混用。

原表情文件必须能完整放入新分区，准备和执行时都核对旧程序、资源及设备身份。执行时重新备份当前实机，不能使用过期备份中的 NVS 覆盖当前网络或凭证。`restore` 默认只检查备份；整机恢复需要明确的 `--approved-full-restore`，并会恢复该备份时的本机配置。

云端先部署匹配后端，再由运维账号执行：

```bash
python server/tools/firmware_publish.py --source <已签名发布目录> --target <持久数据目录>/firmware
```

服务默认从数据库同级的 `firmware/` 读取公开发布包，也可设 `CYBERMIAO_FIRMWARE_DIR`。发布工具仅复制 `manifest.json` 和 `application.bin`，重新验签并原子发布；拒绝覆盖同名不同内容。不要上传迁移包、整机备份或私钥。

## 验证

`server/tests/test_firmware_ota.py` 验证签名、离线任务、进度、权限、撤销及发布流程；`server/tests/firmware_ui_test.cjs` 验证更新页与手机布局。`firmware/tests/test_ota_migrate.py` 模拟整机迁移、NVS 保留、校验失败及整机恢复；`ota_recovery_test.cpp` 验证启动恢复决策和音频回复代次竞态。这些测试不会烧录真实设备。
