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

服务器会把当前测试身份 `test-user-001` / `test-cat-001` 的用户消息和小喵回复保存到 `memory.sqlite3`。默认位置是运行数据目录，也可以通过 `CYBERMIAO_MEMORY_DB_PATH` 指定。

每次聊天结束后会重新计时；默认连续空闲 5 分钟，后台任务调用 DashScope 文本模型生成一段摘要，并提取少量稳定事实和未完成话题。下次建立实时会话时只加载摘要、长期记忆和尚未总结的近期消息，不会把完整历史全部塞进模型上下文。

记忆总结还有三个补偿时机：ESP32 音频 WebSocket 断开时会立即安排当前会话总结；网页控制台最后一个连接关闭时也会安排总结；服务正常关闭时会在短时间窗口内冲刷所有未总结会话。关机冲刷等待时间默认 4 秒，可通过 `CYBERMIAO_MEMORY_SHUTDOWN_TIMEOUT_SECONDS` 调整。若总结请求在窗口内没有完成，原始消息会保留，服务下次启动时会自动扫描并补做总结。

突然断电、系统强制结束进程时无法在断电瞬间调用模型，但已经落库的消息不会丢失，下一次启动仍会自动恢复。只有已经写入 SQLite 的消息才能被恢复；尚未写入数据库的内存内容无法补回。

当前阶段还没有登录、真实设备身份或配对协议。可以用以下环境变量区分测试数据：

```powershell
$env:CYBERMIAO_MEMORY_USER_ID = 'test-user-001'
$env:CYBERMIAO_MEMORY_DEVICE_ID = 'test-cat-001'
$env:CYBERMIAO_MEMORY_IDLE_SECONDS = '300'
$env:CYBERMIAO_MEMORY_SHUTDOWN_TIMEOUT_SECONDS = '4'
```

本地调试时，`GET /api/memory` 可以查看当前测试身份的摘要、长期记忆和未总结消息；`DELETE /api/memory` 会清除这些测试数据。两个接口默认开启，只能在本机或可信局域网使用。部署到公网前必须关闭 `CYBERMIAO_MEMORY_DEBUG_API=0`，并在登录鉴权后重新提供用户级接口。

记忆数据库和原始聊天内容包含个人信息，请纳入备份、删除和访问控制策略。第一阶段使用 SQLite 是为了不改动现有环境；多用户部署前会迁移到 PostgreSQL。

## 数据库初始化与备份

`tools/memory_database.py` 只依赖 Python 标准库和现有记忆模块，支持 Python 3.10 及以上版本。下面命令从仓库根目录运行，路径可按部署环境调整：

```powershell
python server/tools/memory_database.py init --database artifacts/data/memory.sqlite3
python server/tools/memory_database.py inspect --database artifacts/data/memory.sqlite3
python server/tools/memory_database.py backup --database artifacts/data/memory.sqlite3 --output artifacts/backups/memory-before-upgrade.sqlite3
```

初始化与备份都会拒绝覆盖已有文件；检查命令不会创建缺失的数据库。备份通过 SQLite 备份接口读取已提交的 WAL 数据，生成可独立恢复的文件，并输出完整性结果和 SHA-256。命令只输出路径、记录数量与校验信息，不输出聊天正文。

创建数据库只是部署准备。实际保存聊天还需要运行后端，并把 `CYBERMIAO_MEMORY_DB_PATH` 指向该数据库；公网设备认证和账号绑定见[第二阶段计划](../docs/第二阶段开发计划.md)。

## 第二阶段：账号基础（开发中）

服务器状态已按设备拆分，`app.py` 管理路由，`device_runtime.py` 管理单台设备的会话与任务。默认 `CYBERMIAO_ACCESS_MODE=local` 保留原固件使用方式，访问范围限制在本机和私有局域网。账号模式使用 `accounts`；当前仅完成账号与登录会话，设备调用在认证接入前会拒绝，尚不能按此版本公开部署。

先安装更新后的依赖，再从仓库根目录创建账号：

```powershell
python server/tools/identity_admin.py --database artifacts/data/memory.sqlite3 user-add --username owner
python server/tools/identity_admin.py --database artifacts/data/memory.sqlite3 users
python server/tools/identity_admin.py --database artifacts/data/memory.sqlite3 user-password --username owner
```

密码在终端内隐藏输入并确认，长度 10～128 个字符；重设密码会撤销已有登录。`--database` 必须指向实际后端使用的同一个数据库。身份表与聊天表存于同一文件，前述一致性备份也会包含身份表；备份仍需私密保存。设备凭证、配对页面与接口归属验证继续按第二阶段计划实施。
