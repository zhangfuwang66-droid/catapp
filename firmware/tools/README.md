# USB 设备身份与网络配置

小鹿首次 OTA 分区迁移与签名发布使用独立工具，见 [远程更新说明](../OTA.md)。旧的只写主程序工具继续限定原分区，不能用于首次 OTA 迁移。

已有同款设备的共同功能升级使用 [统一固件工具](#同款设备的统一固件工具)。它复用已审核的猫/鹿构建包，默认只输出计划；身份配发和配网仍由本文的 `provision_device.py` 负责。

`provision_device.py` 使用新版主固件提供的 USB 串口协议。它不会烧录程序，也不能给原固件直接添加身份功能；必须先完成备份并烧录支持该协议的固件。已验证实物 USB 配发、网络配置持久化和公网账号绑定；音视频与记忆的完整联调仍需单独验收。

依赖安装：`python -m pip install -r firmware/tools/requirements.txt`。以下命令从仓库根目录运行，`COM3` 和设备编号均需按实际结果替换。

1. 查询编号：`python firmware/tools/provision_device.py --port COM3 status`。编号由芯片唯一标识生成，重启不变；输出只含公开状态，不含网络密码、热点密码或长期设备凭证。打开串口可能让设备短暂复位。
2. 在后端的实际数据库登记这个编号：`python server/tools/identity_admin.py --database artifacts/data/memory.sqlite3 device-add --device-id cat-实际编号 --credential-output artifacts/provision/cat-实际编号.secret.json`。凭证文件私密保存，不上传 Git。
3. 配发凭证：`python firmware/tools/provision_device.py --port COM3 provision --credential-file artifacts/provision/cat-实际编号.secret.json`。工具先查询 USB 设备编号，匹配后才发送凭证。成功返回 `identity_saved: true` 与 `pending_reboot: true`，设备随后重启；返回中的其他状态描述本次启动，重启后重新查询才是新状态。
4. 等设备重新出现后配置网络：`python firmware/tools/provision_device.py --port COM3 configure --server-url https://cat.example --mode accounts --wifi`。Wi-Fi 名称在终端输入，密码隐藏输入；不把密码放进命令参数。已有 Wi-Fi 配置时可省略 `--wifi`。保存后设备再次重启。

`https://` 默认 443，`http://` 默认 80，非默认端口必须填写，例如 `http://192.168.1.100:8081`（请替换为实际地址）。仅支持域名与 IPv4，不接受路径、URL 凭据、查询参数或 IPv6 字面地址。`local` 是明确选择的原接口兼容模式；`accounts` 使用独立设备凭证，缺失凭证时拒绝配置，不会降级为匿名连接。

身份使用独立的 `cmiao_id` NVS 命名空间；新网络配置使用 `cmiao_cfg`，以单个 JSON 值原子保存。只在新配置不存在时读取原 `wifi` 命名空间，保留现有 Wi-Fi 和主机名；删除了源码中的真实网络凭据兜底。设置在重启后生效，运行中的网络任务读取固定配置，避免串口配置与音视频任务并发修改字符串。

协议行以 `CMIAO1 ` 开头，携带随机请求编号，最大 1536 字节；响应只返回对应请求的公开状态。凭证和 Wi-Fi 密码仅从本机私密文件或隐藏输入读取，经 USB 写入 NVS，不回显原始请求或串口日志。固件尚未启用 Flash 加密，持有设备完整 Flash 的人可能读取其中配置，因此设备备份必须私密保存。

模拟测试：`python -m unittest discover -s firmware/tests -v`，不打开任何真实串口。接线、分区和升级前核对见 [固件说明](../README.md) 及 [硬件使用说明](../../docs/硬件使用说明.md)。

## 只读运行诊断

支持诊断命令的主固件可执行：

```powershell
python firmware/tools/provision_device.py --port COM3 diagnostics
```

此命令不保存配置、不重启设备，也不主动连接模型。它按需返回 Wi-Fi 是否在线、时钟/绑定状态、摄像头初始化错误码、I2S 初始化结果、音视频收发计数、任务创建失败位图、内部堆内存及最大连续块、WebSocket 连接阶段和数值 TLS/HTTP 错误码。连接任务即使堵塞，诊断也不等待其 socket 锁。连续查询时，可用 `socket_heartbeat_ms` 是否增长及 `socket_phase` 定位卡住的位置；计数自本次启动累计，跨任务快照不保证同一时刻。

`task_start_failures` 为 0 表示任务均创建成功；位 0～7 依次为摄像头采集、摄像头发送、麦克风采集、麦克风上传、WebSocket 服务、音频播放、舵机动作、账号连接任务。`camera_init_error` 为 0 表示初始化成功；`last_http_status: 101` 只表示收到升级响应，不能代替收到真实音视频的验收。

`tls_psram_preferred: true` 表示启动网络任务前已为 mbedTLS 安装 PSRAM 优先的分配器，外部内存分配失败时回退内部堆；没有 PSRAM 或核心不支持运行时分配器钩子时为 false。`last_tls_error: -32512` 是 TLS 内存分配失败，应结合内部堆、最大连续块和 PSRAM 余量排查，不能据此判断为 API Key 或证书错误。

播放读取修复版还会返回 `speaker_volume_percent`、`playback_pcm_bytes`（送入 I2S 的 PCM 字节数）、`playback_read_timeouts`、`playback_body_errors` 和 `playback_write_retries`。它们用于区分网络读超时、HTTP 分块损坏和 I2S 写入停顿；字节数增加不能证明扬声器没有失真，仍需听感验收。旧固件不会返回这些新增字段。

播放缓冲版增加 `playback_buffered_bytes`（当前排队 PCM 字节数）、`playback_buffer_capacity_bytes`、`playback_buffer_allocation_failures`、`playback_rebuffers`（开始播放后，缓冲不足且实际恢复输出的次数）、`playback_max_network_read_ms`（单次非阻塞预取的最长耗时）和 `playback_max_feed_gap_ms`（同一回复中，相邻两块完整写入 I2S 的最大时间间隔）。这些计数自启动累计，供数间隔排除首次预缓冲和回复之间的等待；它不是驱动的 DMA 欠载计数，仍需结合录音或听感判断。单纯句尾等待 HTTP 结束不增加重新缓冲次数。

诊断不返回 SSID、密码、设备凭证、配对码、聊天正文或原始错误文本；电脑端也对嵌套字段进行白名单和类型过滤。旧版固件不支持时会报错，需要升级主程序后再使用。普通串口日志仍默认关闭。

## 设备热点与账号配对

无可用网络或账号尚未绑定时，新固件启动 `CyberMiao-xxxxxx` 热点，与原 Wi-Fi 连接共存。每台设备生成并保存独立的 12 位 WPA2 密码，热点名、密码和设置页地址显示在设备屏幕上。密码不出现在普通串口状态或设置页接口中。存储失败时拒绝开启无密码热点。

手机连接此热点后，系统通常自动弹出配网页；未弹出时在浏览器打开 `http://192.168.4.1/`。新版页面默认提供“选择 Wi-Fi → 输入密码 → 连接”，附近网络在选择面板中显示信号与是否需要密码，支持扫描重试和手动输入。服务器地址与连接方式收在“高级设置”，默认沿用设备现有配置。配对码在设备联网且未绑定时显示；登录服务器网站，在“添加设备”中填写即可绑定。

同一个 Wi-Fi 下开机自动重连；首次使用或更换 Wi-Fi 才需配网，目前只保存一组网络。网页对原网络提供“留空沿用已保存密码”，更换名称后需重新输入密码；开放网络通过扫描识别，手动输入时需明确勾选。设置保存后约 8 秒重启生效；“已保存”表示已写入 NVS，不代表 Wi-Fi 密码、上网或服务器连接已验证。保存响应丢失时页面会查询同一请求的回执；仍不能确认则提示检查屏幕和重新加入热点，不自动重发密码。配错网络时等待约 30 秒，再连接设备热点更正。

设置页只向设备热点接口提供，普通家庭局域网不能读取配对码。Wi-Fi 配置用带同源检查和本次启动 CSRF 校验的 POST 提交；密码不进入网址、不回填。手机连接热点或页面刚有活动时暂停未成功的旧 Wi-Fi 重试，避免连接和扫描争用无线电；现有已连接的 STA 保持在线。扫描异步执行，固件约 12 秒截止，浏览器请求及轮询也有超时；页面关闭后过渡 10 秒再允许自动重连。自动热点在设备绑定、允许业务连接且没有配网活动时关闭。若需修改已绑定设备，可执行 `python firmware/tools/provision_device.py --port COM3 setup` 手动开启；手动热点持续到重启。换网不清除身份、账号绑定、云端记忆或资料。

`local` 使用旧的匿名路径；`accounts` 使用带设备编号的路径，摄像头/音频 WebSocket 和音频播放流都携带同一个设备 Bearer 凭证。HTTPS/WSS 使用内置 CA 信任库验证证书及访问主机，先同步时间；验证失败不会自动改用 HTTP/WS。云服务器应提供与访问域名或 IPv4 匹配的受信任证书；公网 IP 需要证书 SAN 中包含该 IP，只有域名的证书不能替代 IP 证书。自签名证书和证书更新的配置不在本次工具范围内。

设备每隔约 5 秒检查未绑定状态，已绑定时约 30 秒检查一次；配对码请求至少间隔 30 秒，过期后自动刷新。凭证被撤销后停止业务连接；短时 REST 查询失败保留上次绑定状态，业务连接仍由服务器执行权限校验。

配网页浏览器测试：`node firmware/tests/setup_ui_test.cjs`，使用 Playwright 和模拟接口，不访问 ESP32。环境变量与服务器网页测试一致。覆盖移动布局、弹层选网、SSID 去重与转义、扫描错误/挂起/截止时间、密码保留、保存回执/响应丢失/未确认、配对、本地模式和慢状态轮询。`setup_session_test.cpp` 用模拟时钟覆盖手机会话保护、扫描截止、保存后延迟重启与计时回绕，可用 C++17 编译器执行。页面测试与构建成功不能替代实际手机弹窗、错误密码恢复、重启持久化及音视频验收；本次页面改动需要更新主固件，不是重打电脑 exe。

## 同款设备的统一固件工具

`fleet_firmware.py` 将离线准备、端口识别、备份和主程序升级分开。适用范围是接线相同的 ESP32-S3、8MB Flash、8MB OPI PSRAM，以及本仓库固定分区；猫和鹿使用各自构建包，不互换设备编号前缀。相同芯片不能证明屏幕、摄像头、麦克风、功放和舵机接线相同，执行人必须事先核对。

`upgrade` 仅支持已经具备 `CMIAO1` USB 身份及诊断协议的设备。原厂或未知固件先用 `backup` 只读保存，再单独审核适配；工具不会绕过协议，也不会把未知程序自动加入允许列表。后续一批同款设备可以复用同一份已审核的构建包，每台仍有独立身份、原人格档案、备份与验收回执。工具不并行烧多块板，也不自动连接云服务器。

以下命令从仓库根目录执行。将示例 `COM9`、设备编号、路径与摘要替换为实际审核结果。Python 依赖仍是 `requirements.txt` 中的 `pyserial`；实际读写另需安装 ESP32 核心提供的 **esptool 5.3.1**。没有 `--apply` 的 `upgrade` 不打开串口、不调用 esptool。

### 1. 离线准备固件包

先按 [编译说明](../README.md#编译配置) 生成对应猫/鹿镜像并保存完整编译日志。固件源码须已提交 Git；允许工作区存在与固件无关的改动，但固件源文件、源码分区表和构建副本必须与所记录的提交一致。旧镜像必须来自已审查的同类型设备版本，不能使用 `merged.bin` 或完整 app0 分区备份代替应用镜像。

```powershell
python -X utf8 firmware/tools/fleet_firmware.py prepare `
  --build-dir artifacts/build/firmware `
  --compile-log artifacts/build/firmware-compile.log `
  --profile cat `
  --approved-previous-image artifacts/firmware/reviewed-cat/application.bin `
  --output artifacts/firmware/fleet-cat-v1
```

多个经过审查的旧版本可以重复传入 `--approved-previous-image`。准备鹿版本时，编译须有 `compiler.cpp.extra_flags=-DCYBERMIAO_DEVICE_DEER=1`，并选 `--profile deer`；猫构建不接受这个宏。板型、核心 `3.3.12`、编译覆盖项、源文件副本、ESP 应用段/校验和/内置 SHA-256、分区 MD5 和布局都会核验。

输出目录不可覆盖，包含 `application.bin`、`partitions.bin`、`manifest.json`。终端返回 `manifest_sha256`，请将这个摘要单独保存在本次审核记录中；升级时必须提供。它用于检测包被修改，**不是发布者数字签名**。准备成功只证明离线材料满足检查，不代表任何实体设备或人格已经验收。

### 2. 识别与备份

```powershell
# 只列出端口名、VID/PID，不打开串口。
python -X utf8 firmware/tools/fleet_firmware.py scan

# 明确打开一个端口查询公开身份及诊断，不回显原始串口日志。
python -X utf8 firmware/tools/fleet_firmware.py scan --probe --port COM9

$esptoolExe = 'C:/实际路径/esptool.exe'
python -X utf8 firmware/tools/fleet_firmware.py backup `
  --port COM9 --esptool $esptoolExe --profile cat `
  --device-id cat-AABBCCDDEEFF `
  --output artifacts/devices/cat-AABBCCDDEEFF/before-review
```

`backup` 会让程序暂停、进入下载模式并在完成后重启，但不写 Flash。芯片类型、实际 MAC、8MB 容量、安全启动与 Flash 加密状态必须符合要求。编号须与 MAC 派生值对应；选择 `cat/deer` 只指定这次审核的产品类型，不能据此认定未知原程序的类型或兼容性。

每次输出使用一个新的目录。完整 8MB 备份不仅计算本地 SHA-256，还使用 `verify-flash` 与当前芯片比较；随后保存完整原 app0、受保护的头部及资源尾部。这款板曾在 stub 批量读取时中断，因此所有只读 esptool 操作使用 `--no-stub`、115200；仅写主程序使用 stub、460800。esptool 5.3.1 的 `verify-flash` 默认先比较 `SPI_FLASH_MD5`，ESP32-S3 ROM 支持该摘要命令；不加 `--diff`，校验失败不会打印 Flash 差异原文。相关实现可查 [cmds.py](https://github.com/espressif/esptool/blob/v5.3.1/esptool/cmds.py) 与 [loader.py](https://github.com/espressif/esptool/blob/v5.3.1/esptool/loader.py)。

Windows 目录移除继承权限，仅授予当前用户和 SYSTEM；其他系统使用 `0700`。目录权限设置失败会停止读取，不把原始内容打印到终端。Flash 可能含 Wi-Fi 和设备长期凭证，请保存在本机私密磁盘，不上传 Git、网盘分享或聊天；`artifacts/`、`*.secret.*`、`*.bin` 已被忽略。

### 3. 锁定每台原人格

表情存在设备文件系统，性格可能来自后端提示词、配置或旧系统。**不写文件系统只能保护表情等资源，不能证明原性格保留。** 先从可信原配置中保存人格快照，人工核对，再建立每台设备的离线档案。不能从任意旧二进制自动恢复性格，也不能用全局“小猫”提示词代替未知原人格。

优先直接使用后端 `server/device_personas.py` 的 `PersonaStore.export_private(device_id, destination)` 导出的私密锁档 JSON，作为 `--personality` 输入。它含 `schema: 1`、`device_id`、`kind`、`name`、`version`、`prompt`、`sha256`、`created_at`；摘要对应完整 `prompt` 的 UTF-8 字节。导出前后端 metadata 必须是 `status: "locked"`，工具核对类型、编号、摘要和字段格式，不回显 prompt；无需手工重抄正文或制作第二份档案。导出文件本身仍需私密存放，目标已存在时导出器拒绝覆盖。此操作只导出已锁定档案，不能靠导出调用给来源不明的设备建立“原人格”。

没有后端导出文件、但已有经人工核实的历史原文时，也支持外部引用档案。建议放在 `artifacts/devices/<设备编号>/personality.secret.json`；快照与档案放同目录。示意格式如下，摘要需替换成原快照的真实 SHA-256（64 位小写十六进制）：

```json
{
  "device_id": "cat-AABBCCDDEEFF",
  "device_type": "cat",
  "personality_reference": "backend://device-personality/cat-AABBCCDDEEFF/v1",
  "personality_sha256": "替换为原人格快照的真实SHA256",
  "personality_file": "original-personality.secret.txt",
  "verified_original": true
}
```

可用 `Get-FileHash -Algorithm SHA256` 获取快照摘要，再转为小写。只有确认快照确实来自这台设备的原人格后才填 `verified_original: true`；缺来源就保持待核对。`personality_reference` 是档案标识，工具不会访问它；有一个 `backend://` 字符串不代表后端已保存或正在加载该人格。工具仅核对离线快照，始终返回 `backend_personality_verified: false`；云端实际人格、角色名称和行为需要另行验收。

### 4. 先看计划，再执行主程序升级

```powershell
$firmwareManifestSha256 = '替换为准备时单独保存的64位小写摘要'
python -X utf8 firmware/tools/fleet_firmware.py upgrade `
  --package artifacts/firmware/fleet-cat-v1 `
  --manifest-sha256 $firmwareManifestSha256 `
  --device-id cat-AABBCCDDEEFF `
  --personality artifacts/devices/cat-AABBCCDDEEFF/personality.secret.json
```

此命令只检查本地包和可选人格档案并输出计划。缺人格档案也能查看计划，`ready_for_device` 保持 false。执行时必须有已核对的原人格档案，先在控制台暂停语音，确保设备没有正在播放；再确认同款接线、原型号和当前 USB 端口，保持机身平稳，不在操作中换板或换线：

```powershell
python -X utf8 firmware/tools/fleet_firmware.py upgrade `
  --package artifacts/firmware/fleet-cat-v1 `
  --manifest-sha256 $firmwareManifestSha256 `
  --device-id cat-AABBCCDDEEFF `
  --personality artifacts/devices/cat-AABBCCDDEEFF/personality.secret.json `
  --port COM9 --esptool $esptoolExe `
  --backup-dir artifacts/devices/cat-AABBCCDDEEFF/upgrade-001 `
  --same-wiring-confirmed --apply
```

执行阶段依次核对 USB 身份/类型、无待重启、无正在播放、PSRAM 和资源就绪；读取芯片、MAC、容量与安全状态；生成并回读核验完整备份；精确比较分区；匹配旧程序 SHA-256 白名单；仅写 `0x10000` 应用。不会运行全盘擦除，也不写启动器、分区表、NVS 或表情文件系统。

新程序写入后，先校验应用，以及 `[0, 0x10000)` 与 `[0x290000, 0x800000)` 两段保护区域，再允许首次启动。重启后只在原端口重试 USB，核对身份、类型、网络/凭证配置状态、文件系统与表情计数；不会猜测另一个串口。USB 若重新枚举到别的端口，会停在待检查状态，不能因此再写一次。回执记录每个阶段；写入中断标为 `flash_written: "possibly"`，不误报“没写过”。未知旧固件、不同分区、资源异常或任何校验失败均不自动跳过。

成功回执仍有 `physical_acceptance_complete: false` 和 `backend_personality_verified: false`。请实测屏幕/原表情、舵机、麦克风、扬声器、画面、联网、重新绑定和原人格，再做后台共同功能验收。工具不改云端账号、数据库、模型 Key，也不自动恢复语音。

### 失败与恢复

工具**没有自动回退**。校验失败时保留备份与阶段回执；程序可能停在下载模式。先检查电源、线缆、端口、当前芯片和回执，不重复盲写。完整 Flash 保留作恢复材料，正常恢复只考虑原 app0，不能把旧 NVS/资源一起写回覆盖后来的合法变更。

需要人工恢复时，先用 `backup` 为**当前状态**再建一份新快照，核对芯片 MAC 和安全状态；将 `original-app0.secret.bin` 的 SHA-256 与原 `backup.secret.json` 的 `original_app0_sha256` 核对。审核确认恢复兼容性后，只向 `0x10000` 写原 app0，然后在首次重启前对照“恢复前的新快照”校验当前保护区域。命令结构如下，仅在已完成上述核对并获得实际写入授权后执行：

```powershell
$esptoolBase = @('--chip', 'esp32s3', '--port', 'COM9', '--baud', '460800', '--before', 'default-reset', '--after', 'no-reset')
$romVerifyBase = @('--chip', 'esp32s3', '--port', 'COM9', '--baud', '115200', '--before', 'default-reset', '--after', 'no-reset', '--no-stub')
& $esptoolExe @esptoolBase write-flash --flash-mode keep --flash-freq keep --flash-size keep `
  0x10000 artifacts/devices/cat-AABBCCDDEEFF/upgrade-001/original-app0.secret.bin
& $esptoolExe @romVerifyBase verify-flash `
  0x0 artifacts/devices/cat-AABBCCDDEEFF/before-restore/protected-head.secret.bin `
  0x10000 artifacts/devices/cat-AABBCCDDEEFF/upgrade-001/original-app0.secret.bin `
  0x290000 artifacts/devices/cat-AABBCCDDEEFF/before-restore/protected-assets-tail.secret.bin
```

每条命令都必须成功且确认同一芯片，之后才可复位。此段是人工恢复操作规范，未作为自动恢复命令实现，也未在本轮连接硬件验证；不要直接照抄占位路径执行。

### 工具验证范围

`python -X utf8 -m unittest discover -s firmware/tests -p test_fleet_firmware.py -v` 使用临时文件、模拟 USB/esptool，覆盖默认不访问设备、包/源码/分区/镜像校验、错类型与 MAC、旧程序白名单、人格来源门槛、备份不完整/回读失败、只写 app0、保护区校验顺序、失败回执和重启后状态变化。它不枚举真实串口、不烧录、不访问云端。本轮还只读解析过既有编译文件的镜像与分区格式；统一工具的真实 USB 升级、恢复和批量长稳测试仍需在授权后验收。
