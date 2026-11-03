# -*- coding: utf-8 -*-
"""
开发环境设置脚本
安装所有必需的依赖
"""
import subprocess
import sys
from pathlib import Path

def install_launcher_deps():
    """安装启动器依赖"""
    print("📦 安装 GUI 启动器依赖...")
    requirements_file = Path(__file__).parent / "requirements.txt"

    if requirements_file.exists():
        cmd = [sys.executable, "-m", "pip", "install", "-r", str(requirements_file)]
        subprocess.run(cmd, check=True)
        print("✅ 启动器依赖安装完成")
    else:
        print("⚠️ requirements.txt 不存在")

def install_server_deps():
    """安装服务器依赖"""
    print("\n📦 安装服务器依赖...")
    server_requirements = Path(__file__).parent.parent / "esp32_servo_control" / "server" / "requirements.txt"

    if server_requirements.exists():
        cmd = [sys.executable, "-m", "pip", "install", "-r", str(server_requirements)]
        subprocess.run(cmd, check=True)
        print("✅ 服务器依赖安装完成")
    else:
        print("⚠️ 服务器 requirements.txt 不存在")

def check_python_version():
    """检查 Python 版本"""
    print(f"🐍 Python 版本: {sys.version}")

    if sys.version_info < (3, 9):
        print("⚠️ 建议使用 Python 3.9 或更高版本")
    elif sys.version_info >= (3, 13):
        print("⚠️ Python 3.13+ audioop 模块已被移除，已使用 numpy 替代")

    print("")

def main():
    """主函数"""
    print("=" * 60)
    print("  赛博小喵 Cyber Miao - 开发环境设置")
    print("=" * 60)
    print("")

    try:
        # 检查 Python 版本
        check_python_version()

        # 升级 pip
        print("⬆️ 升级 pip...")
        subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "pip"], check=True)
        print("")

        # 安装依赖
        install_launcher_deps()
        install_server_deps()

        print("\n" + "=" * 60)
        print("✅ 环境设置完成！")
        print("=" * 60)
        print("\n下一步:")
        print("1. 测试 GUI: python src/launcher.py")
        print("2. 打包 EXE: python build.py")

    except subprocess.CalledProcessError as e:
        print(f"\n❌ 安装失败: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n\n⚠️ 用户取消")
        sys.exit(1)

if __name__ == '__main__':
    main()
