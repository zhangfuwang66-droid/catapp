# 赛博小喵 Cyber Miao

ESP32 AI 桌面猫，包含桌面控制台、Python 服务器、固件、PCB 和 3D 外壳。

双击根目录带猫咪图标的 **`CyberMiao.exe`**，在配置页保存自己的 DashScope API Key，再到主控制台启动服务器。

默认网页地址为 `http://127.0.0.1:8081`。连接 ESP32 时使用主控制台显示的局域网 IP 和端口。

## 目录

```text
catapp/
├── CyberMiao.exe              带猫咪图标的主程序，双击启动
├── README.md                 项目导航
├── desktop/                  桌面 GUI、图标、测试、开发启动与打包脚本
├── server/                   FastAPI 服务器、依赖与本地虚拟环境
├── firmware/                 ESP32 主程序和资源烧录工具
├── hardware/
│   ├── pcb/                  PCB 图片和物料清单
│   └── enclosure/            3D 打印文件
├── docs/                     使用、烧录和发布说明
│   ├── archive/              历史排障记录
│   └── tools/                文档生成脚本
└── artifacts/                本地生成文件，不提交 Git
    ├── release/              正式版 exe 和使用说明
    ├── debug/                调试版 exe
    ├── build/                构建缓存和运行检查报告
    └── archive/              整理前的构建文件和误放文件
```

## 使用与开发

- [软件使用说明](docs/使用手册.md)
- [ESP32 烧录与硬件使用](docs/硬件使用说明.md)
- [桌面代码结构、开发和构建](desktop/README.md)
- [发布验证流程](docs/打包检查清单.md)

在仓库根目录执行：

```powershell
python desktop/setup.py
python desktop/src/launcher.py
python -m unittest discover -s desktop/tests -v
python desktop/build.py
```

服务器开发环境位于 `server/.venv`；也可使用已安装依赖的系统 Python。只启动服务器可运行 `server/start-server.ps1`，此方式需要配置 `server/.env` 中的 `DASHSCOPE_API_KEY`。

尚未打包时可运行 `desktop/run.bat`，或用 `desktop/run_dev.ps1` 进入开发模式。正式打包通过运行检查后，会自动更新根目录的 `CyberMiao.exe`；完整发布包和构建记录保存在 `artifacts/release/`。exe 是本地构建产物，不提交 Git。

配置、日志和录制数据默认存放在 `%LOCALAPPDATA%\CyberMiao`，升级 exe 不会覆盖这些文件。真实 Key 不应提交进 Git。

固件草图目录保留与 `.ino` 文件一致的名称，方便 Arduino IDE 打开。历史日志保留旧路径，仅供追溯；当前路径以本文为准。本地服务检查与真实云端、硬件联调应分别验证。
