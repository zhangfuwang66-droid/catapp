# Python 服务器

`app.py` 是 FastAPI 入口，默认监听 `0.0.0.0:8081`，健康检查为 `/api/health`。日常使用建议双击根目录的 `CyberMiao.exe` 启动桌面控制台。

单独开发服务器时，在本目录的 `.env` 中配置 `DASHSCOPE_API_KEY`，再执行：

```powershell
.\.venv\Scripts\python.exe app.py
```

也可运行 `start-server.ps1`。安装依赖使用 `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`。

实时对话和普通多模态调用默认使用 `qwen3.8-omni-flash`，分别可通过 `QWEN_REALTIME_MODEL` 和 `QWEN_OMNI_MODEL` 覆盖。实时对话仍使用 DashScope Realtime WebSocket 接口；此模型在该接口下的可用性、音色和会话参数兼容性需要真实云端联调确认。视觉问答、文本摘要和情绪解析继续使用各自配置的模型。

运行模块直接保留在本目录；辅助的配网语音生成工具放在 `tools/`，它需要访问云端 TTS，生成的 `config_prompt.pcm` 用于 ESP32 资源烧录。

`.venv/`、`.env`、`models/`、`records/` 和 Python 缓存均不提交 Git。桌面模式的数据默认写入 `%LOCALAPPDATA%\CyberMiao`，直接运行服务器时默认写入本目录。

## 运行日志与录制空间

直接运行 `app.py` 会在运行数据目录的 `logs/CyberMiao.log` 写入轮转日志：单文件上限约 2 MiB，保留 5 个历史文件。云端入口使用同一日志配置；桌面入口继续沿用桌面程序的轮转日志。普通运行日志只记录事件、长度和错误类型，聊天正文和摘要保存在数据库，API Key、Bearer 凭证及上游可能回显的请求内容不写入普通日志。Linux 新建及轮转后的日志只允许属主读写；Windows 的访问权限由用户数据目录 ACL 决定。

录制图片默认保留 7 天，受管理图片总量限制为 1 GiB，磁盘预留 512 MiB，每 300 秒巡检。写入前也检查容量，超限时优先清理较旧且未被使用的图片；仍无空间则暂停该设备的录制并向网页提示。聊天和语音服务不因此停止。

可通过以下环境变量调整：

- `CYBERMIAO_RECORD_RETENTION_DAYS`：保留天数，默认 `7`。
- `CYBERMIAO_RECORD_MAX_MB`：图片容量上限，默认 `1024` MiB。
- `CYBERMIAO_RECORD_MIN_FREE_MB`：磁盘预留，默认 `512` MiB。
- `CYBERMIAO_RECORD_CLEAN_INTERVAL_SECONDS`：巡检间隔，默认 `300` 秒。

只清理 `records/<设备号>/<YYYYMMDD-HHMMSS>/<时间戳>_<序号>.jpg` 的应用录制图片。当前活动列表和正在写入、描述的图片受保护，可能超过保留期限，直到释放后再清理。不清理数据库、备份、手工文件或目录链接；旧版目录不自动迁移或删除。此限额仅覆盖受管理图片，不代表整个数据目录的容量限制，数据库和备份仍需独立监控。

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

记忆结构登记为 `schema_migrations` 中的 `memory=1`，身份结构仍为 `identity=2`。完整的无版本旧记忆库会在事务内登记版本，保留原数据；结构残缺或任一组件版本高于当前程序时拒绝继续写入。摘要按精确的同会话消息编号提交，重复、迟到的同批结果不会再次写入。读取聊天、摘要、事实时使用同一个数据库快照。

`inspect` 还会检查账号/设备表、外键、事实归属、总结消息边界和真实账号与设备归属，输出各组件版本、身份表数量、待总结会话数以及数据库/WAL/可复用空间和磁盘剩余空间。只报告数量和状态，不输出聊天、账号、密码哈希或令牌；遇到关系异常会失败，供部署前人工处理。

### 自动备份与运行检查

自动备份默认关闭。设置备份目录后，后端在生命周期内启动后台备份任务，仍使用一个 Uvicorn worker。从仓库根目录启动开发服务前可配置：

```powershell
$env:CYBERMIAO_BACKUP_DIR = Join-Path (Get-Location) 'artifacts/backups/automatic'
```

首次没有有效备份时立即生成；重启会校验已有快照，距离最新备份未满间隔时继续等待。以下环境变量均可选：

- `CYBERMIAO_BACKUP_INTERVAL_SECONDS`：默认 `21600`，即 6 小时；环境配置至少 60 秒。
- `CYBERMIAO_BACKUP_KEEP`：默认保留最新 `28` 份有效快照，正常连续运行时约 7 天；这是数量限制，不是按日期保留承诺。
- `CYBERMIAO_BACKUP_MIN_FREE_MB`：默认 `256`，创建前还额外预留两倍数据库逻辑大小的空间。空间不足时保留原数据库与现有备份，暂缓生成。
- `CYBERMIAO_BACKUP_TIMEOUT_SECONDS`：SQLite 复制阶段默认限时 `30` 秒；校验、磁盘 I/O 与线程清理仍需时间，不是整个任务的硬截止时间。

每份独立的 `.sqlite3` 快照带有 `.json` 清单，记录 SHA-256、时间、大小、组件版本和表数量，不含聊天、账号或凭证正文。快照包含完整数据库，必须私密保存；Windows 依赖目录 ACL，Linux 新建文件只允许属主读写。清单以同盘硬链接发布，备份目录需支持硬链接。

只轮换严格符合自动备份命名、同一源数据库路径、清单和 SHA-256 校验的快照；手工备份、其他源数据库、损坏或不完整记录不会自动删除。更换源库路径会开始新的备份序列，旧序列需另行管理。降低保留数量后，下一轮即使无需新建快照，也会执行轮换。失败会记录异常类型并在最多 60 秒后重试；普通日志不包含原始异常正文。

以下命令只读检查同源自动备份；源数据库丢失时仍可列出备份，不会重建数据库：

```powershell
python server/tools/memory_database.py backup-status --database artifacts/data/memory.sqlite3 --directory artifacts/backups/automatic
```

返回 `valid_backups`、`skipped_unverified` 和最新备份的文件名、时间戳、SHA-256。`ok` 表示检查执行成功；`valid_backups=0` 表示没有可用快照，不能据此认定备份健康。有跳过记录时需管理员检查目录。同机、同盘备份不能抵御整机或磁盘故障，实际云端部署还需异机复制与恢复演练，本轮没有配置外部上传。

`GET /api/health` 继续只表示服务响应；新增 `GET /api/ready` 只读检查数据库可访问、组件版本、必需表和列：可用返回 `200 OK`，失败返回 `503 NOT READY`，不暴露内部细节。它不是完整性巡检，也不保证写入、备份或模型连接正常；完整检查仍用 `inspect`。

清除设备记忆只影响当前数据库，历史快照中的旧内容会保留到轮换或管理员单独处理。停用自动备份可清空 `CYBERMIAO_BACKUP_DIR` 后重启后端，已有快照保留。

### 恢复演练与正式恢复

先保存备份命令返回的 SHA-256 到独立记录。恢复默认只读预览，不创建目标目录：

```powershell
python server/tools/memory_database.py restore --backup artifacts/backups/memory-before-upgrade.sqlite3 --output artifacts/data/memory-restored.sqlite3 --sha256 备份时保存的64位校验值
```

停止后端并另行备份当前库后，在同一命令加 `--apply --confirm-service-stopped` 才实际生成恢复库。只接受包含完整性/关联检查且 SHA-256 匹配的独立备份；不接受运行中的 WAL 数据库，不覆盖已有数据库或同名 WAL/SHM/journal。先在私密临时目录完成恢复，再使用同盘硬链接无覆盖发布；文件系统不支持硬链接时安全失败。

恢复库保留聊天、摘要、事实、账号与绑定，撤销备份中的网页登录会话并作废未领取配对码，需要重新登录、重新申请临时码。设备长期凭证会恢复到备份时的状态：备份之后的停用、密码修改和凭证撤销不在旧备份中，重新上线前需管理员核对。该命令不会自动停止服务、替换当前数据库或修改服务配置；核对恢复结果后，再把后端数据库路径切换到新文件。

创建数据库只是部署准备。实际保存聊天还需要运行后端，并把 `CYBERMIAO_MEMORY_DB_PATH` 指向该数据库；公网设备认证和账号绑定见[第二阶段计划](../docs/第二阶段开发计划.md)。

## 旧测试记忆迁移

旧测试身份的数据不会自动交给首个账号。`tools/migrate_memory.py` 默认以只读方式核对源身份、目标账号和设备绑定，仅显示数量，不输出聊天。先创建真实账号、完成设备绑定，并在设备开始产生新聊天前决定是否迁移。目标已有会话、归属不符、源为真实账号或数据引用异常时都会拒绝。

以下从仓库根目录运行，把 `usr_实际账号编号`、设备编号和路径替换为实际值；账号编号通过前述 `users` 命令查看：

```powershell
python server/tools/migrate_memory.py --database artifacts/data/memory.sqlite3 --source-user test-user-001 --source-device test-cat-001 --target-user usr_实际账号编号 --target-device cat-example
```

确认数据归属并停止后端后，在同一命令末尾加上 `--apply --confirm-service-stopped --backup artifacts/backups/memory-before-identity.sqlite3` 才会执行。工具先取得写锁、再次检查并创建包含 WAL 的独立备份，再用事务更新会话及长期记忆的归属；保留原消息、摘要、编号和总结进度。备份存在则拒绝覆盖；中途失败回滚，已成功创建的备份保留。迁移结束后才能重新启动服务。空库无需迁移；目前未对云端数据库执行此操作。

## 第二阶段：账号基础（开发中）

服务器状态已按设备拆分，`app.py` 管理路由，`device_runtime.py` 管理单台设备的会话与任务。默认 `CYBERMIAO_ACCESS_MODE=local` 保留原固件使用方式，访问范围限制在本机和私有局域网。账号模式使用 `accounts`，设备接口已接入凭证、登录和归属检查。账号网页已完成本地浏览器验证；新版固件与实物 TLS 联调尚未完成，正式部署仍按第二阶段计划执行。

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

## 账号网页本地联调

账号模式首页 `/` 提供登录、设备列表、配对码输入、在线状态、记忆查看、确认清除与停用。控制台为 `/console/{device_id}`，打开时验证登录和设备归属；页面的请求、画面与控制都使用该设备的接口，HTTPS 页面使用 WSS。退出、切换账号或权限失效会断开旧订阅，清除聊天、画面、活动图片和播放内容。页面刷新不会把 Cookie 或设备凭证写入浏览器本地存储。

从仓库根目录运行开发服务示例（先安装服务器依赖，配置 API Key，并在同一个数据库创建账号）：

```powershell
$env:CYBERMIAO_ACCESS_MODE = 'accounts'
$env:CYBERMIAO_MEMORY_DB_PATH = (Join-Path (Get-Location) 'artifacts/data/memory.sqlite3')
python -m uvicorn app:app --app-dir server --host 127.0.0.1 --port 8081 --workers 1
```

浏览器访问 `http://127.0.0.1:8081/`。本例仅供本机开发，云端部署还需配置域名、HTTPS、服务进程和备份。网页资源在 `server/web/`，桌面构建配置已包含资源目录；本阶段没有重新打包 exe。旧固件仍应使用 `local` 模式，新身份协议固件完成前不能直接切换实物。

后端测试：从仓库根目录设置 `PYTHONPATH=server`，运行 `python -m unittest discover -s server/tests -v`。浏览器测试为 `node server/tests/web_ui_test.cjs`，需要可用的 Playwright、浏览器与服务器 Python 依赖；可通过 `PYTHON_EXECUTABLE` 指定解释器，`BROWSER_EXECUTABLE` 指定浏览器，`NODE_PATH` 指向 Playwright 所在的 `node_modules`。测试只启动回环地址的临时服务，用模拟账号和数据库验证页面操作，不调用真实模型或写入硬件；截图存入已忽略的 `artifacts/web-tests/`。

新版固件通过设备 Bearer 查询 `GET /api/devices/{device_id}/binding`，未绑定时调用 `POST /api/devices/{device_id}/pairing` 申请配对码。响应包含原来的 `expires_at` 和新增的 `expires_in`（服务器计算的剩余秒数，最大 300），便于尚未同步时钟的本地调试设备倒计时。配对码只在设备屏幕及受保护的设备热点页显示，长期凭证仅通过 USB 配发。固件与工具见 [USB 配置说明](../firmware/tools/README.md)；实际烧录与 TLS、真实模型对话仍待实物联调。
