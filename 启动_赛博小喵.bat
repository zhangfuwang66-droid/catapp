@echo off
chcp 65001 >nul
title 赛博小喵 Cyber Miao

echo ========================================
echo   赛博小喵 Cyber Miao - AI 桌面宠物
echo ========================================
echo.

cd /d "%~dp0CyberMiao_App"

REM 检查是否已打包为 EXE
if exist "dist\CyberMiao_Release\CyberMiao.exe" (
    echo 🚀 启动已打包的 EXE 版本...
    start "" "dist\CyberMiao_Release\CyberMiao.exe"
    goto :end
)

REM 检查 Python 环境
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ 错误: 未安装 Python
    echo 请先安装 Python 3.9 或更高版本
    echo 下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo 🐍 检测到 Python 环境
echo 📱 启动开发模式...
echo.

REM 检查是否安装了依赖
python -c "import tkinter" >nul 2>&1
if errorlevel 1 (
    echo ⚠️ 未安装 tkinter，请确保 Python 包含 tkinter 模块
    pause
    exit /b 1
)

REM 启动 GUI
python src\launcher.py

:end
pause
