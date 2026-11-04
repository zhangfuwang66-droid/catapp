# 赛博小喵 Cyber Miao

赛博小喵是一只基于 ESP32 的 AI 桌面猫，包含设备固件、FastAPI 服务、Windows 桌面控制台、PCB 和 3D 外壳。

当前服务默认在本机 `8081` 端口运行。ESP32 与运行服务的电脑需要处于可互通的局域网；云端语音对话需要有效的 DashScope API Key 和互联网连接。

## 功能

- 流式语音对话与中英同声传译
- ESP32 摄像头实时画面、视觉问答
- 表情动画、舵机动作和手势追随
- Windows 控制台配置 API Key、启动和管理本地服务
- Wi-Fi 配网时设置设备要连接的服务地址

## 快速开始

### 使用正式版

Windows x64 用户可在 GitHub Releases 选择安装版 `CyberMiao-Setup-x64.exe`，或免安装版 `CyberMiao-Portable-x64.exe`。安装版提供开始菜单快捷方式和可选桌面快捷方式；便携版下载后直接运行。GitHub 会自动附上源码 ZIP 和 tar.gz。

启动桌面程序后，在“连接设置”中保存 DashScope API Key，再到“工作台”启动服务器。安装版和便携版共用 `%LOCALAPPDATA%\CyberMiao` 中的配置和数据；卸载安装版不会删除这些用户数据。

服务器启动后，把控制台显示的局域网 IP 和端口填入 ESP32 配网页面。浏览器可打开 `http://127.0.0.1:8081`；设备和其他局域网客户端使用电脑的局域网 IP。

### 从源码运行

Windows 下使用 Python 3.10 或更高版本，在仓库根目录执行：

```powershell
python desktop/setup.py
python desktop/src/launcher.py
```

也可运行 `desktop/run_dev.ps1` 进入开发模式。需要单独运行 FastAPI 服务时，在 `server/.env` 配置 `DASHSCOPE_API_KEY`，再运行 `server/start-server.ps1`。

## 构建与测试

```powershell
python -m unittest discover -s desktop/tests -v
python desktop/build.py --version 1.0.0
```

正式构建需要安装 Inno Setup 6，并执行 `python desktop/build.py --version 1.0.0`。构建会执行依赖检查、打包和本地运行检查；通过后生成 `CyberMiao-Portable-x64.exe` 与 `CyberMiao-Setup-x64.exe`，并尝试更新根目录的开发启动入口 `CyberMiao.exe`。如果该程序仍在运行，构建会保留正在使用的入口文件，发行文件仍可正常使用。构建信息和使用说明保存在 `artifacts/release/`，构建缓存放在 `artifacts/build/`。调试版 `python desktop/build.py --debug` 不需要 Inno Setup。

推送 `v` 开头的版本标签会自动运行 Windows x64 构建、运行检查并创建 GitHub Release，只上传安装版和便携版两个 exe；源码 ZIP 和 tar.gz 由 GitHub 自动生成。例如：

```powershell
git tag v0.1.0
git push origin v0.1.0
```

## 项目结构

```text
catapp/
├── desktop/       Windows 桌面控制台、图标、测试和构建脚本
├── server/        FastAPI 服务及依赖说明
├── firmware/      ESP32 主程序和烧录工具
├── hardware/
│   ├── pcb/       PCB 文件与物料清单
│   └── enclosure/ 3D 外壳文件
├── docs/          使用、硬件和发布文档
└── artifacts/     本地构建产物（Git 忽略）
```

## 文档

- [软件使用说明](docs/使用手册.md)
- [ESP32 烧录与硬件使用](docs/硬件使用说明.md)
- [桌面开发与构建](desktop/README.md)
- [打包检查清单](docs/打包检查清单.md)
- [Python 服务器说明](server/README.md)

配置、日志和录制数据默认保存在 `%LOCALAPPDATA%\CyberMiao`。API Key 不应提交到 Git。当前架构面向本地单实例使用；公网多用户和多设备隔离尚未实现。
