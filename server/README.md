# Python 服务器

`app.py` 是 FastAPI 入口，默认监听 `0.0.0.0:8081`，健康检查为 `/api/health`。日常使用建议双击根目录的 `CyberMiao.exe` 启动桌面控制台。

单独开发服务器时，在本目录的 `.env` 中配置 `DASHSCOPE_API_KEY`，再执行：

```powershell
.\.venv\Scripts\python.exe app.py
```

也可运行 `start-server.ps1`。安装依赖使用 `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`。

运行模块直接保留在本目录；辅助的配网语音生成工具放在 `tools/`，它需要访问云端 TTS，生成的 `config_prompt.pcm` 用于 ESP32 资源烧录。

`.venv/`、`.env`、`models/`、`records/` 和 Python 缓存均不提交 Git。桌面模式的数据默认写入 `%LOCALAPPDATA%\CyberMiao`，直接运行服务器时默认写入本目录。

## 第一阶段聊天记忆

服务器把用户消息和小喵回复保存到 `memory.sqlite3`。账号模式使用已认证的主人与设备身份；本地兼容模式仍默认使用 `test-user-001` / `test-cat-001`。默认位置是运行数据目录，也可以通过 `CYBERMIAO_MEMORY_DB_PATH` 指定。

每次聊天结束后会重新计时；默认连续空闲 5 分钟，后台任务调用 DashScope 文本模型生成一段摘要，并提取少量稳定事实和未完成话题。下次建立实时会话时只加载摘要、长期记忆和尚未总结的近期消息，不会把完整历史全部塞进模型上下文。

记忆总结还有三个补偿时机：ESP32 音频 WebSocket 断开时会立即安排当前会话总结；网页控制台最后一个连接关闭时也会安排总结；服务正常关闭时会在短时间窗口内冲刷所有未总结会话。关机冲刷等待时间默认 4 秒，可通过 `CYBERMIAO_MEMORY_SHUTDOWN_TIMEOUT_SECONDS` 调整。若总结请求在窗口内没有完成，原始消息会保留，服务下次启动时会自动扫描并补做总结。

突然断电、系统强制结束进程时无法在断电瞬间调用模型，但已经落库的消息不会丢失，下一次启动仍会自动恢复。只有已经写入 SQLite 的消息才能被恢复；尚未写入数据库的内存内容无法补回。

本地兼容模式可以用以下环境变量区分测试数据；账号模式的归属由登录和设备绑定决定：

```powershell
$env:CYBERMIAO_MEMORY_USER_ID = 'test-user-001'
$env:CYBERMIAO_MEMORY_DEVICE_ID = 'test-cat-001'
$env:CYBERMIAO_MEMORY_IDLE_SECONDS = '300'
$env:CYBERMIAO_MEMORY_SHUTDOWN_TIMEOUT_SECONDS = '4'
```

本地调试时，`GET /api/memory` 查看当前测试身份的摘要、长期记忆和未总结消息；`DELETE /api/memory` 清除测试数据。可设置 `CYBERMIAO_MEMORY_DEBUG_API=0` 关闭本地记忆调试。账号模式关闭旧入口，主人通过 `/api/devices/{device_id}/memory` 访问；清除时需通过 CSRF 校验并提交 `{"confirm_device_id":"实际设备编号"}`。清除会结束该设备会话后重新连接，避免旧任务写回；不会删除账号、凭证或活动图片。

数据库和原始聊天内容请纳入备份、删除和访问控制策略。目前使用 SQLite 与一个 Uvicorn worker；多人部署先评估实际负载，再决定是否迁移 PostgreSQL。

## 数据库初始化与备份

`tools/memory_database.py` 只依赖 Python 标准库和现有记忆模块，支持 Python 3.10 及以上版本。下面命令从仓库根目录运行，路径可按部署环境调整：

```powershell
python server/tools/memory_database.py init --database artifacts/data/memory.sqlite3
python server/tools/memory_database.py inspect --database artifacts/data/memory.sqlite3
python server/tools/memory_database.py backup --database artifacts/data/memory.sqlite3 --output artifacts/backups/memory-before-upgrade.sqlite3
```

初始化与备份都会拒绝覆盖已有文件；检查命令不会创建缺失的数据库。备份通过 SQLite 备份接口读取已提交的 WAL 数据，生成可独立恢复的文件，并输出完整性结果和 SHA-256。命令只输出路径、记录数量与校验信息，不输出聊天正文。

创建数据库只是部署准备。实际保存聊天还需要运行后端，并把 `CYBERMIAO_MEMORY_DB_PATH` 指向该数据库；公网设备认证和账号绑定见[第二阶段计划](../docs/第二阶段开发计划.md)。

## 旧测试记忆迁移

旧测试身份的数据不会自动交给首个账号。`tools/migrate_memory.py` 默认以只读方式核对源身份、目标账号和设备绑定，仅显示数量，不输出聊天。先创建真实账号、完成设备绑定，并在设备开始产生新聊天前决定是否迁移。目标已有会话、归属不符、源为真实账号或数据引用异常时都会拒绝。

以下从仓库根目录运行，把 `usr_实际账号编号`、设备编号和路径替换为实际值；账号编号通过前述 `users` 命令查看：

```powershell
python server/tools/migrate_memory.py --database artifacts/data/memory.sqlite3 --source-user test-user-001 --source-device test-cat-001 --target-user usr_实际账号编号 --target-device cat-example
```

确认数据归属并停止后端后，在同一命令末尾加上 `--apply --confirm-service-stopped --backup artifacts/backups/memory-before-identity.sqlite3` 才会执行。工具先取得写锁、再次检查并创建包含 WAL 的独立备份，再用事务更新会话及长期记忆的归属；保留原消息、摘要、编号和总结进度。备份存在则拒绝覆盖；中途失败回滚，已成功创建的备份保留。迁移结束后才能重新启动服务。空库无需迁移；目前未对云端数据库执行此操作。

## 第二阶段：账号基础（开发中）

服务器状态已按设备拆分，`app.py` 管理路由，`device_runtime.py` 管理单台设备的会话与任务。默认 `CYBERMIAO_ACCESS_MODE=local` 保留原固件使用方式，访问范围限制在本机和私有局域网。账号模式使用 `accounts`，设备接口已经接入凭证、登录和归属检查；账号网页、固件及 TLS 联调尚未完成，正式部署仍按第二阶段计划执行。

先安装更新后的依赖，再从仓库根目录创建账号：

```powershell
python server/tools/identity_admin.py --database artifacts/data/memory.sqlite3 user-add --username owner
python server/tools/identity_admin.py --database artifacts/data/memory.sqlite3 users
python server/tools/identity_admin.py --database artifacts/data/memory.sqlite3 user-password --username owner
```

密码在终端内隐藏输入并确认，长度 10～128 个字符；重设密码会撤销已有登录。`--database` 必须指向实际后端使用的同一个数据库。身份表与聊天表存于同一文件，前述一致性备份也会包含身份表；备份仍需私密保存。

设备登记由终端执行，示例编号须替换成实际设备的稳定编号：

```powershell
python server/tools/identity_admin.py --database artifacts/data/memory.sqlite3 device-add --device-id cat-example --name 我的赛博小喵 --credential-output artifacts/provision/cat-example.secret.json
python server/tools/identity_admin.py --database artifacts/data/memory.sqlite3 devices
```

凭证文件只生成一次并拒绝覆盖，已被 Git 忽略。此命令不会烧录设备；后续通过 USB 配发。`device-rotate` 配合同样的 `--credential-output` 可生成新凭证并撤销旧凭证；`device-revoke --device-id ...` 停用设备，不更换主人或转移历史记忆。若签发后文件写盘失败，使用轮换命令配发新凭证，不能从数据库取回原文。

账号模式使用 `/api/devices/{device_id}/...` 和 `/ws/devices/{device_id}/audio|camera|ui|viewer`。旧匿名入口关闭。设备麦克风、摄像头、播放流使用 `Authorization: Bearer ...`；网页通过 HttpOnly Cookie 登录，写请求附带 `Origin` 与 `/api/auth/session` 返回的 `X-CSRF-Token`。设备凭证不接受 URL 参数，也不能用于读取网页记忆和控制接口。网页退出会结束自己的订阅，设备语音可继续工作。
