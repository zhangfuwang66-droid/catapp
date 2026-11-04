@echo off
chcp 65001 >nul
title 赛博小喵 Cyber Miao
setlocal
set "APP_ROOT=%~dp0desktop"
if exist "%~dp0artifacts\release\CyberMiao.exe" (
    start "" "%~dp0artifacts\release\CyberMiao.exe"
    exit /b 0
)
set "PYTHON_EXE=%~dp0server\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"
"%PYTHON_EXE%" "%APP_ROOT%\src\launcher.py"
if errorlevel 1 (
    echo 启动失败。请使用 Python 3.10 或更高版本运行 desktop\setup.py 安装依赖。
    pause
    exit /b 1
)
