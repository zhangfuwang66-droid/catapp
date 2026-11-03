# -*- coding: utf-8 -*-
"""
PyInstaller 打包配置
"""
import sys
from pathlib import Path

# 应用信息
APP_NAME = "CyberMiao"
APP_VERSION = "1.0.0"
APP_DESCRIPTION = "赛博小喵 - AI 桌面宠物"
APP_AUTHOR = "Cyber Miao Team"

# 路径配置
PROJECT_ROOT = Path(__file__).parent.parent
SERVER_ROOT = PROJECT_ROOT / "esp32_servo_control" / "server"

# 需要包含的数据文件（只添加存在的文件）
def collect_server_files():
    """收集服务器文件"""
    files = [
        "app.py",
        "asr_core.py",
        "audio_stream.py",
        "cat_motion.py",
        "emotion_parser.py",
        "index_html.py",
        "omni_client.py",
        "omni_realtime_client.py",
        "voice_command.py",
    ]

    datas = []
    for filename in files:
        filepath = SERVER_ROOT / filename
        if filepath.exists():
            datas.append((str(filepath), "server"))
        else:
            print(f"⚠️ 警告: 文件不存在 - {filepath}")

    return datas

datas = collect_server_files()

# 隐藏导入（PyInstaller 可能检测不到的模块）
hiddenimports = [
    'uvicorn',
    'uvicorn.logging',
    'uvicorn.loops',
    'uvicorn.loops.auto',
    'uvicorn.protocols',
    'uvicorn.protocols.http',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.lifespan',
    'uvicorn.lifespan.on',
    'fastapi',
    'starlette',
    'pydantic',
    'dashscope',
    'openai',
    'websockets',
    'aiohttp',
    'cv2',
    'numpy',
    'PIL',
    'mediapipe',
]

# 排除的模块（减小体积）
excludes = [
    'matplotlib',
    'pandas',
    'scipy',
    'IPython',
    'jupyter',
    'notebook',
]

# 生成 .spec 文件内容
spec_template = f"""
# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['src/launcher.py'],
    pathex=[],
    binaries=[],
    datas={datas},
    hiddenimports={hiddenimports},
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[],
    excludes={excludes},
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='{APP_NAME}',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # 不显示控制台窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icon.ico' if Path('assets/icon.ico').exists() else None,
    version_file=None,
)
"""

def generate_spec_file():
    """生成 .spec 文件"""
    spec_path = Path(__file__).parent / f"{APP_NAME}.spec"

    # 检查图标是否存在，使用正斜杠（跨平台兼容）
    icon_path = Path(__file__).parent / 'assets' / 'icon.ico'
    if icon_path.exists():
        # 转换为 POSIX 路径（使用正斜杠）
        icon_str = f"icon=r'{str(icon_path)}'"
    else:
        icon_str = "icon=None"

    # 生成内容（替换 icon 部分）
    content = spec_template.strip().replace(
        "icon='assets/icon.ico' if Path('assets/icon.ico').exists() else None",
        icon_str
    )

    with open(spec_path, 'w', encoding='utf-8') as f:
        f.write(content)

    print(f"✅ 已生成 {spec_path}")
    return spec_path

if __name__ == '__main__':
    generate_spec_file()
