# 赛博小喵 Cyber Miao - 一键 EXE 版本

## 🎯 项目说明

本项目已完整支持 **一键 EXE 打包**，用户只需双击即可使用，无需安装 Python 环境和依赖。

## 📦 快速开始

### 方式 1：使用已打包的 EXE（推荐用户）

如果已经打包完成：

1. 进入 `CyberMiao_App/dist/CyberMiao_Release/` 目录
2. 双击 `CyberMiao.exe`
3. 首次运行会提示配置 API Key
4. 配置完成后点击"启动服务器"
5. 自动打开浏览器开始使用！

### 方式 2：开发模式（开发者）

如果需要开发或测试：

```powershell
# 1. 安装依赖
cd CyberMiao_App
python setup.py

# 2. 启动 GUI
python src/launcher.py

# 或使用快速启动脚本
.\run_dev.ps1
```

### 方式 3：一键启动脚本（最简单）

双击项目根目录的 `启动_赛博小喵.bat`，自动选择合适的启动方式。

## 🔨 打包 EXE

```powershell
cd CyberMiao_App
python build.py
```

打包完成后，EXE 文件位于 `dist/CyberMiao_Release/CyberMiao.exe`

**预计文件大小**: 200-400 MB（包含 AI 模型、图像处理库等）

## 📁 新项目结构

```
catapp/
├── CyberMiao_App/              # ⭐ EXE 打包版（新增）
│   ├── src/
│   │   ├── launcher.py         # GUI 启动器
│   │   └── system_tray.py      # 系统托盘
│   ├── assets/
│   │   ├── icon.ico            # 应用图标
│   │   └── icon.png
│   ├── config/
│   │   └── settings.ini        # 配置文件
│   ├── build.py                # 打包脚本
│   ├── build_config.py         # 打包配置
│   ├── setup.py                # 环境设置
│   ├── requirements.txt        # 启动器依赖
│   ├── run_dev.ps1             # 开发启动脚本
│   └── README.md
│
├── esp32_servo_control/        # 原服务器代码
│   ├── server/
│   │   ├── app.py              # ✅ 已优化（Python 3.13+ 兼容）
│   │   ├── .venv/              # 虚拟环境
│   │   ├── requirements.txt
│   │   └── start-server.ps1    # ✅ 已优化（自动检测 IP）
│   └── esp32_servo_control/    # ESP32 固件
│
├── PCB/                        # 硬件 PCB 设计
├── 3Dprint.3mf                 # 3D 打印文件
├── 启动_赛博小喵.bat            # ⭐ 一键启动脚本（新增）
└── README_EXE打包版.md         # 本文件

```

## ✨ 新功能特性

### GUI 启动器
- ✅ 图形化配置界面（API Key、端口等）
- ✅ 一键启动/停止服务器
- ✅ 实时日志显示
- ✅ 自动检测本机 IP
- ✅ 自动打开浏览器
- ✅ 配置持久化保存
- ✅ 系统托盘支持（可选）

### 打包优化
- ✅ 单文件 EXE（所有依赖打包）
- ✅ 无控制台窗口（纯 GUI）
- ✅ 自定义图标（猫咪主题）
- ✅ 自动生成发布包
- ✅ 包含使用说明

### 代码优化
- ✅ Python 3.13+ 兼容（替换 audioop）
- ✅ 自动检测 IP 地址
- ✅ 防火墙友好提示
- ✅ 错误处理完善

## 🔧 技术栈

- **GUI 框架**: Tkinter（Python 标准库，无需额外安装）
- **打包工具**: PyInstaller 6.0+
- **图标生成**: Pillow (PIL)
- **系统托盘**: pystray（可选）
- **Web 服务器**: FastAPI + Uvicorn
- **AI 模型**: Qwen 3.5 Omni (阿里云 DashScope)

## 📝 使用说明

### 首次配置

1. 启动程序后，进入"配置"标签页
2. 填写 DashScope API Key（从 https://dashscope.console.aliyun.com/ 获取）
3. 可选配置：端口号、自动打开浏览器
4. 点击"保存配置"

### ESP32 配网

1. 启动服务器后，在"主控制台"查看显示的本机 IP
2. ESP32 开机进入配网模式
3. 手机连接 `CyberMiao-Setup` 热点
4. 浏览器访问 `192.168.4.1`
5. 填写 WiFi 信息和服务器 IP（即步骤 1 的 IP）

### 功能使用

- 🎤 **语音对话**: 对着麦克风说话，AI 实时语音回复
- 👁️ **视觉问答**: 对话时自动抓取摄像头画面
- 🌐 **同声传译**: 网页中切换翻译模式
- 🎭 **表情动作**: TFT 屏幕显示对应表情
- 📱 **实时画面**: 网页 viewer 实时查看摄像头

## 🐛 常见问题

### Q: 打包失败？
**A**: 
1. 检查是否安装了所有依赖：`python setup.py`
2. 确保 Python 版本 >= 3.9
3. 查看构建日志中的错误信息

### Q: EXE 启动报错？
**A**:
1. 检查是否有杀毒软件拦截
2. 确保配置文件目录可写
3. 查看日志输出中的错误信息

### Q: 服务器启动失败？
**A**:
1. 检查 API Key 是否正确配置
2. 检查端口是否被占用
3. 检查防火墙是否放行

### Q: ESP32 连接不上？
**A**:
1. 确认电脑 IP 地址正确（不要用旧 IP）
2. 检查电脑和 ESP32 在同一局域网
3. Windows 防火墙检查是否允许 8081 端口

## 📊 性能说明

- **EXE 大小**: 约 200-400 MB（包含完整 AI 和图像处理库）
- **启动时间**: 首次约 5-10 秒（解压临时文件），后续约 2-3 秒
- **内存占用**: 约 300-500 MB（服务器运行时）
- **CPU 占用**: 待机约 1-5%，对话时约 20-50%

## 🎯 文件大小优化建议

如果需要减小 EXE 文件大小：

1. **移除手势识别功能**：在 `build_config.py` 的 `excludes` 中添加 `'mediapipe'`
2. **使用 UPX 压缩**：在 `.spec` 文件中设置 `upx=True`（已启用）
3. **精简依赖**：检查 `requirements.txt`，移除不必要的库

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📜 许可证

© 2024 Cyber Miao Project

---

## 🎉 版本更新日志

### v1.0.0 (2024-xx-xx)
- ✅ 完整的 GUI 启动器
- ✅ PyInstaller 一键打包
- ✅ Python 3.13+ 支持
- ✅ 自动 IP 检测
- ✅ 配置管理系统
- ✅ 系统托盘支持
- ✅ 图标和发布包

### 原始版本
- ESP32 固件
- FastAPI 服务器
- Qwen 3.5 Omni 集成
- 语音对话、视觉问答、同声传译
