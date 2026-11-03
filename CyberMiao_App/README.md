# 赛博小喵 Cyber Miao - EXE 打包版

## 项目说明

本目录包含将赛博小喵服务器打包成 Windows EXE 可执行文件的完整解决方案。

## 目录结构

```
CyberMiao_App/
├── src/
│   └── launcher.py         # GUI 启动器主程序
├── assets/                  # 资源文件（图标等）
├── config/                  # 配置文件目录
├── resources/               # 其他资源
├── build_config.py          # PyInstaller 打包配置
├── build.py                 # 一键打包脚本
└── README.md                # 本文件
```

## 打包步骤

### 1. 安装依赖

```powershell
cd CyberMiao_App
python -m pip install pyinstaller
```

### 2. 运行打包脚本

```powershell
python build.py
```

打包脚本会自动：
- 检查并安装 PyInstaller
- 生成 .spec 配置文件
- 打包成单个 EXE 文件
- 创建发布包（包含 README 和配置目录）

### 3. 输出文件

打包完成后，在 `dist/CyberMiao_Release/` 目录下会生成：
- `CyberMiao.exe` - 主程序
- `config/` - 配置文件目录
- `README.txt` - 用户使用说明

## 开发测试

在打包前测试程序：

```powershell
python src/launcher.py
```

## 特性

✅ **一键启动**：双击 EXE 即可运行，无需安装 Python 环境
✅ **图形界面**：友好的 GUI 配置和控制界面
✅ **自动配置**：首次运行引导配置 API Key
✅ **系统托盘**：后台运行，最小化到托盘
✅ **日志显示**：实时查看服务器运行状态
✅ **自动打开**：启动后自动打开浏览器

## 技术栈

- **GUI 框架**: tkinter（Python 标准库）
- **打包工具**: PyInstaller
- **服务器**: FastAPI + Uvicorn
- **AI 模型**: Qwen 3.5 Omni (DashScope)

## 文件大小优化

当前打包文件约 200-400MB，主要包含：
- OpenCV (图像处理)
- MediaPipe (手势识别)
- NumPy (数值计算)
- AI SDK (DashScope)

可通过以下方式优化：
1. 移除不需要的模块（如 MediaPipe 手势功能）
2. 使用 UPX 压缩
3. 排除测试和文档文件

## 常见问题

### Q: 打包失败怎么办？
A: 检查是否安装了所有依赖，可以运行 `pip install -r ../esp32_servo_control/server/requirements.txt`

### Q: EXE 文件太大？
A: 这是正常的，因为包含了 AI 和图像处理库。可以考虑在线安装版本。

### Q: 启动报错找不到模块？
A: 检查 `build_config.py` 中的 `hiddenimports` 是否包含所有需要的模块。

### Q: 如何添加自定义图标？
A: 将 `.ico` 文件放到 `assets/icon.ico`，重新打包即可。

## 许可证

© 2024 Cyber Miao Project
