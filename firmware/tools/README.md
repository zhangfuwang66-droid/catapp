# USB 设备身份与网络配置

`provision_device.py` 使用新版主固件提供的 USB 串口协议。它不会烧录程序，也不能给原固件直接添加身份功能；必须先完成备份并烧录支持该协议的固件。当前设备尚未烧录，新协议暂时只能进行模拟测试。

依赖安装：`python -m pip install -r firmware/tools/requirements.txt`。以下命令从仓库根目录运行，`COM3` 和设备编号均需按实际结果替换。

1. 查询编号：`python firmware/tools/provision_device.py --port COM3 status`。编号由芯片唯一标识生成，重启不变；输出只含公开状态，不含网络密码、热点密码或长期设备凭证。打开串口可能让设备短暂复位。
2. 在后端的实际数据库登记这个编号：`python server/tools/identity_admin.py --database artifacts/data/memory.sqlite3 device-add --device-id cat-实际编号 --credential-output artifacts/provision/cat-实际编号.secret.json`。凭证文件私密保存，不上传 Git。
3. 配发凭证：`python firmware/tools/provision_device.py --port COM3 provision --credential-file artifacts/provision/cat-实际编号.secret.json`。工具先查询 USB 设备编号，匹配后才发送凭证。成功返回 `identity_saved: true` 与 `pending_reboot: true`，设备随后重启；返回中的其他状态描述本次启动，重启后重新查询才是新状态。
4. 等设备重新出现后配置网络：`python firmware/tools/provision_device.py --port COM3 configure --server-url https://cat.example --mode accounts --wifi`。Wi-Fi 名称在终端输入，密码隐藏输入；不把密码放进命令参数。已有 Wi-Fi 配置时可省略 `--wifi`。保存后设备再次重启。

`https://` 默认 443，`http://` 默认 80，非默认端口必须填写，例如 `http://192.168.2.105:8081`。仅支持域名与 IPv4，不接受路径、URL 凭据、查询参数或 IPv6 字面地址。`local` 是明确选择的原接口兼容模式；`accounts` 使用独立设备凭证，缺失凭证时拒绝配置，不会降级为匿名连接。

身份使用独立的 `cmiao_id` NVS 命名空间；新网络配置使用 `cmiao_cfg`，以单个 JSON 值原子保存。只在新配置不存在时读取原 `wifi` 命名空间，保留现有 Wi-Fi 和主机名；删除了源码中的真实网络凭据兜底。设置在重启后生效，运行中的网络任务读取固定配置，避免串口配置与音视频任务并发修改字符串。

协议行以 `CMIAO1 ` 开头，携带随机请求编号，最大 1536 字节；响应只返回对应请求的公开状态。凭证和 Wi-Fi 密码仅从本机私密文件或隐藏输入读取，经 USB 写入 NVS，不回显原始请求或串口日志。此阶段未启用 Flash 加密，持有设备完整 Flash 的人可能读取其中配置，因此设备备份必须私密保存。

模拟测试：`python -m unittest discover -s firmware/tests -v`，不打开任何真实串口。接线、分区和恢复资料见 [固件说明](../README.md) 及 [第二阶段联调记录](../../docs/第二阶段联调记录.md)。
