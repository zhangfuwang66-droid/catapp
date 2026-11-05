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

当前阶段还没有登录、真实设备身份或配对协议。可以用以下环境变量区分测试数据：

```powershell
$env:CYBERMIAO_MEMORY_USER_ID = 'test-user-001'
$env:CYBERMIAO_MEMORY_DEVICE_ID = 'test-cat-001'
$env:CYBERMIAO_MEMORY_IDLE_SECONDS = '300'
```

本地调试时，`GET /api/memory` 可以查看当前测试身份的摘要、长期记忆和未总结消息；`DELETE /api/memory` 会清除这些测试数据。两个接口默认开启，只能在本机或可信局域网使用。部署到公网前必须关闭 `CYBERMIAO_MEMORY_DEBUG_API=0`，并在登录鉴权后重新提供用户级接口。

记忆数据库和原始聊天内容包含个人信息，请纳入备份、删除和访问控制策略。第一阶段使用 SQLite 是为了不改动现有环境；多用户部署前会迁移到 PostgreSQL。
