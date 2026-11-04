# Python 服务器

`app.py` 是 FastAPI 入口，默认监听 `0.0.0.0:8081`，健康检查为 `/api/health`。日常使用建议从根目录的 `启动_赛博小喵.bat` 启动桌面控制台。

单独开发服务器时，在本目录的 `.env` 中配置 `DASHSCOPE_API_KEY`，再执行：

```powershell
.\.venv\Scripts\python.exe app.py
```

也可运行 `start-server.ps1`。安装依赖使用 `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`。

运行模块直接保留在本目录；辅助的配网语音生成工具放在 `tools/`，它需要访问云端 TTS，生成的 `config_prompt.pcm` 用于 ESP32 资源烧录。

`.venv/`、`.env`、`models/`、`records/` 和 Python 缓存均不提交 Git。桌面模式的数据默认写入 `%LOCALAPPDATA%\CyberMiao`，直接运行服务器时默认写入本目录。
