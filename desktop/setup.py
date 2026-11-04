"""给当前 Python 环境安装启动器、服务器和构建依赖。"""
from pathlib import Path
import subprocess
import sys


def main():
    if sys.version_info < (3, 10):
        raise SystemExit("需要 Python 3.10 或更高版本；本项目在 Python 3.13.14 上验证。")
    requirements = Path(__file__).resolve().parent / "requirements.txt"
    subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(requirements)], check=True)
    print("依赖已安装。开发启动：python src/launcher.py；打包：python build.py")


if __name__ == "__main__":
    main()
