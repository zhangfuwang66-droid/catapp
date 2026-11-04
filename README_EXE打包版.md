# 赛博小喵 Windows 桌面版

双击 `CyberMiao_App/dist/CyberMiao_Release/CyberMiao.exe`，在配置页保存自己的 DashScope API Key，再到主控制台启动服务器。

默认网页地址为 `http://127.0.0.1:8081`。连接 ESP32 时使用主控制台显示的局域网 IP 和端口。

- [用户使用说明](CyberMiao_App/使用手册.md)
- [代码结构、开发和构建](CyberMiao_App/README.md)
- [发布验证流程](CyberMiao_App/打包检查清单.md)

配置、日志和录制数据默认存放在 `%LOCALAPPDATA%\CyberMiao`，升级 exe 不会覆盖这些文件。真实 Key 不应提交进 Git。

仓库根目录的 `启动_赛博小喵.bat` 优先启动已有的正式 exe；尚未构建时，使用服务器虚拟环境或系统 Python 启动 GUI。

固件、PCB 和 3D 外壳保留原有结构。桌面服务器的本地健康检查与真实云端、硬件验证范围应分别记录。
