# USB 设备身份与网络配置

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

诊断不返回 SSID、密码、设备凭证、配对码、聊天正文或原始错误文本；电脑端也对嵌套字段进行白名单和类型过滤。旧版固件不支持时会报错，需要升级主程序后再使用。普通串口日志仍默认关闭。

## 设备热点与账号配对

无可用网络或账号尚未绑定时，新固件启动 `CyberMiao-xxxxxx` 热点，与原 Wi-Fi 连接共存。每台设备生成并保存独立的 12 位 WPA2 密码，热点名、密码和设置页地址显示在设备屏幕上。密码不出现在普通串口状态或设置页接口中。存储失败时拒绝开启无密码热点。

手机连接此热点，打开 `http://192.168.4.1/`，可以修改 Wi-Fi、完整服务器地址及连接方式，查看临时配对码与倒计时。在服务器网站登录主人账号，填写配对码即可领取设备。设置页只向设备热点接口提供，普通家庭局域网不能读取配对码。Wi-Fi 配置用带同源检查和本次启动 CSRF 校验的 POST 提交；密码不进入网址，也不回填旧密码。

设置保存后重启生效；请重新输入 Wi-Fi 密码，空密码表示开放 Wi-Fi。配错网络时等待约 30 秒后重新连接设备热点更正。自动开启的热点在设备绑定并允许业务连接后关闭，屏幕恢复表情。若需要修改已绑定设备，可执行 `python firmware/tools/provision_device.py --port COM3 setup` 手动开启；手动热点持续到设备重启。此操作不清除身份、绑定或记忆。

`local` 使用旧的匿名路径；`accounts` 使用带设备编号的路径，摄像头/音频 WebSocket 和音频播放流都携带同一个设备 Bearer 凭证。HTTPS/WSS 使用内置 CA 信任库验证证书及访问主机，先同步时间；验证失败不会自动改用 HTTP/WS。云服务器应提供与访问域名或 IPv4 匹配的受信任证书；公网 IP 需要证书 SAN 中包含该 IP，只有域名的证书不能替代 IP 证书。自签名证书和证书更新的配置不在本次工具范围内。

设备每隔约 5 秒检查未绑定状态，已绑定时约 30 秒检查一次；配对码请求至少间隔 30 秒，过期后自动刷新。凭证被撤销后停止业务连接；短时 REST 查询失败保留上次绑定状态，业务连接仍由服务器执行权限校验。

配网页浏览器测试：`node firmware/tests/setup_ui_test.cjs`，使用 Playwright 和模拟接口，不访问 ESP32。环境变量与服务器网页测试一致。目前协议、页面与固件构建验证均不能替代真实 USB、重启持久化和 TLS 内存实测。
