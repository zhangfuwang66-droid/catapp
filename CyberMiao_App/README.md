# 赛博小喵桌面程序

Windows GUI 用于配置 DashScope API Key，并管理已有的 FastAPI 服务器。固件、PCB 和外壳继续保留在原目录。

用户入口是 `dist/CyberMiao_Release/CyberMiao.exe`，操作说明见 [使用手册](使用手册.md)。

## 代码结构

- `src/launcher.py`：Tk 界面和主线程事件轮询。
- `src/config.py`、`src/app_paths.py`：配置校验、保存和资源路径。
- `src/server_manager.py`：服务进程状态、停止超时回收、日志脱敏和轮转。
- `src/server_entry.py`：服务子进程入口，普通 import 使 PyInstaller 能分析服务器依赖。
- `src/system_tray.py`：托盘线程向 GUI 投递命令。
- `src/bundle_check.py`：打包后运行检查，不访问云端或摄像头。
- `tests/test_runtime.py`：源码集成测试。
- `CyberMiao.spec`、`build_config.py`、`build.py`：可迁移的构建配置和构建检查。
- `config/settings.example.ini`：无密钥示例；真实配置存于用户目录。

## 开发与构建

在仓库根目录执行，使用同一个 Python 环境安装、运行和打包：

```powershell
python CyberMiao_App/setup.py
python CyberMiao_App/src/launcher.py
python -m unittest discover -s CyberMiao_App/tests -v
python CyberMiao_App/build.py --debug
python CyberMiao_App/build.py
```

代码需要 Python 3.10 或更新版本；本次验证环境为 Windows、Python 3.13.14 和 PyInstaller 6.22.3。发行版目标为 Windows x64；其他机器和系统版本需要另行验证。

构建前会检查依赖，并下载固定版本的 MediaPipe 手部模型、核对 SHA-256。构建后会启动生成的 exe，验证原生推理、HTTP、WebSocket、停止和重启。运行检查通过后才更新正式发布目录。

调试版输出到 `dist/debug/`，正式构建输出到 `dist/release/` 并复制到 `dist/CyberMiao_Release/`。构建日志中的 `Hidden import not found` 和缺少运行时 DLL 必须排查；`warn-CyberMiao.txt` 也会包含只适用于其他操作系统的可选模块，不能把整份警告直接当作失败或通过的依据。

构建记录保存在 `BUILD-INFO.json`，包含 Python/依赖版本、exe 大小、SHA-256 和运行检查结果。详细检查日志位于 `build/<debug或release>/runtime-check.log`。

开发者可单独执行已实现的检查入口：

```powershell
& .\CyberMiao_App\dist\CyberMiao_Release\CyberMiao.exe --check-runtime "$PWD\runtime-check.json"
```

该命令使用临时目录和无效测试 Key，不读取用户的正式配置。返回码 0 表示本地运行检查通过，报告中的 `cloud_api_tested` 和 `hardware_tested` 仍为 false。

手部模型来源：[Google MediaPipe hand_landmarker float16/1](https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task)。构建使用的校验值见 `build_config.py`。该模型只用于本地手部识别，云端对话模型通过 API 调用。
