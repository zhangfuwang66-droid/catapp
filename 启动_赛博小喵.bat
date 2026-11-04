@echo off
chcp 65001 >nul
title 赛博小喵 Cyber Miao
setlocal
set "APP_ROOT=%~dp0CyberMiao_App"
if exist "%APP_ROOT%\dist\CyberMiao_Release\CyberMiao.exe" (
    start "" "%APP_ROOT%\dist\CyberMiao_Release\CyberMiao.exe"
    exit /b 0
)
set "PYTHON_EXE=%~dp0esp32_servo_control\server\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"
"%PYTHON_EXE%" "%APP_ROOT%\src\launcher.py"
if errorlevel 1 (
    echo 启动失败。请使用 Python 3.10 或更高版本运行 CyberMiao_App\setup.py 安装依赖。
    pause
    exit /b 1
)
