# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['src/launcher.py'],
    pathex=[],
    binaries=[],
    datas=[('D:\\Github\\cat\\catapp\\esp32_servo_control\\server\\app.py', 'server'), ('D:\\Github\\cat\\catapp\\esp32_servo_control\\server\\asr_core.py', 'server'), ('D:\\Github\\cat\\catapp\\esp32_servo_control\\server\\audio_stream.py', 'server'), ('D:\\Github\\cat\\catapp\\esp32_servo_control\\server\\cat_motion.py', 'server'), ('D:\\Github\\cat\\catapp\\esp32_servo_control\\server\\emotion_parser.py', 'server'), ('D:\\Github\\cat\\catapp\\esp32_servo_control\\server\\index_html.py', 'server'), ('D:\\Github\\cat\\catapp\\esp32_servo_control\\server\\omni_client.py', 'server'), ('D:\\Github\\cat\\catapp\\esp32_servo_control\\server\\omni_realtime_client.py', 'server'), ('D:\\Github\\cat\\catapp\\esp32_servo_control\\server\\voice_command.py', 'server')],
    hiddenimports=['uvicorn', 'uvicorn.logging', 'uvicorn.loops', 'uvicorn.loops.auto', 'uvicorn.protocols', 'uvicorn.protocols.http', 'uvicorn.protocols.http.auto', 'uvicorn.protocols.websockets', 'uvicorn.protocols.websockets.auto', 'uvicorn.lifespan', 'uvicorn.lifespan.on', 'fastapi', 'starlette', 'pydantic', 'dashscope', 'openai', 'websockets', 'aiohttp', 'cv2', 'numpy', 'PIL', 'mediapipe'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'pandas', 'scipy', 'IPython', 'jupyter', 'notebook'],
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
    name='CyberMiao',
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
    icon='D:\Github\cat\catapp\CyberMiao_App\assets\icon.ico',
    version_file=None,
)