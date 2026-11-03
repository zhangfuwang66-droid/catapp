# -*- coding: utf-8 -*-
"""
一键打包脚本
"""
import subprocess
import sys
from pathlib import Path
import shutil

def check_dependencies():
    """检查打包依赖"""
    print("🔍 检查打包依赖...")

    try:
        import PyInstaller
        print(f"✅ PyInstaller {PyInstaller.__version__}")
    except ImportError:
        print("❌ 未安装 PyInstaller")
        print("正在安装 PyInstaller...")
        subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"], check=True)
        print("✅ PyInstaller 安装完成")

def clean_build():
    """清理构建目录"""
    print("\n🧹 清理旧的构建文件...")
    dirs_to_clean = ['build', 'dist', '__pycache__']
    for dir_name in dirs_to_clean:
        dir_path = Path(dir_name)
        if dir_path.exists():
            shutil.rmtree(dir_path)
            print(f"  删除: {dir_name}")

def generate_spec():
    """生成 spec 文件"""
    print("\n📝 生成 PyInstaller 配置文件...")
    from build_config import generate_spec_file
    return generate_spec_file()

def build_exe(spec_file):
    """构建 EXE"""
    print(f"\n🔨 开始打包 EXE...")
    cmd = [sys.executable, "-m", "PyInstaller", str(spec_file), "--clean", "--noconfirm"]
    result = subprocess.run(cmd, check=False)

    if result.returncode == 0:
        print("\n✅ 打包成功！")
        exe_path = Path("dist") / "CyberMiao.exe"
        if exe_path.exists():
            print(f"📦 EXE 位置: {exe_path.absolute()}")
            print(f"📦 文件大小: {exe_path.stat().st_size / 1024 / 1024:.2f} MB")
        return True
    else:
        print("\n❌ 打包失败")
        return False

def create_release_package():
    """创建发布包"""
    print("\n📦 创建发布包...")

    dist_dir = Path("dist")
    release_dir = dist_dir / "CyberMiao_Release"

    if release_dir.exists():
        shutil.rmtree(release_dir)
    release_dir.mkdir(parents=True, exist_ok=True)

    # 复制 EXE
    exe_src = dist_dir / "CyberMiao.exe"
    if exe_src.exists():
        shutil.copy(exe_src, release_dir / "CyberMiao.exe")

    # 创建 config 目录
    (release_dir / "config").mkdir(exist_ok=True)

    # 创建 README
    readme_content = """
# 赛博小喵 Cyber Miao - AI 桌面宠物

## 快速开始

1. 双击 `CyberMiao.exe` 启动程序
2. 首次运行会提示配置 API Key
3. 前往 https://dashscope.console.aliyun.com/ 获取 API Key
4. 在配置页面填写 API Key 并保存
5. 点击"启动服务器"
6. 自动打开浏览器，开始使用！

## ESP32 配网

程序启动后，在主控制台中会显示本机 IP 地址（例如：192.168.1.100）
在 ESP32 配网时，填写该 IP 地址即可连接。

## 功能特性

- 🎤 语音对话 (Qwen 3.5 Omni 端到端语音)
- 👁️ 视觉问答 (摄像头实时画面)
- 🌐 同声传译 (中英互译)
- 🎭 表情动作控制 (15 个动作库)
- 📡 WiFi 配网 (热点模式)

## 技术支持

如有问题，请查看日志输出或联系技术支持。

---
© 2024 Cyber Miao Project
"""

    with open(release_dir / "README.txt", 'w', encoding='utf-8') as f:
        f.write(readme_content.strip())

    print(f"✅ 发布包已创建: {release_dir.absolute()}")

def main():
    """主函数"""
    print("=" * 60)
    print("  赛博小喵 Cyber Miao - EXE 打包工具")
    print("=" * 60)

    try:
        # 1. 检查依赖
        check_dependencies()

        # 2. 清理旧文件
        clean_build()

        # 3. 生成配置
        spec_file = generate_spec()

        # 4. 构建 EXE
        success = build_exe(spec_file)

        if success:
            # 5. 创建发布包
            create_release_package()

            print("\n" + "=" * 60)
            print("🎉 打包完成！")
            print("=" * 60)
            print("\n📁 发布包位置: dist/CyberMiao_Release/")
            print("📝 使用说明: dist/CyberMiao_Release/README.txt")
        else:
            print("\n打包过程中出现错误，请检查日志。")
            sys.exit(1)

    except KeyboardInterrupt:
        print("\n\n⚠️ 用户取消")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 错误: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == '__main__':
    main()
