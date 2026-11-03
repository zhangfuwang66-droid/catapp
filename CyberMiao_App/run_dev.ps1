# 开发模式快速启动脚本
Write-Host "🚀 启动赛博小喵 (开发模式)" -ForegroundColor Cyan
Write-Host ""

# 检查虚拟环境
$venvPath = "..\esp32_servo_control\server\.venv"
if (Test-Path $venvPath) {
    Write-Host "✅ 使用虚拟环境: $venvPath" -ForegroundColor Green
    $pythonExe = Join-Path $venvPath "Scripts\python.exe"
} else {
    Write-Host "⚠️ 虚拟环境不存在，使用系统 Python" -ForegroundColor Yellow
    $pythonExe = "python"
}

# 启动 GUI
Write-Host "📱 启动 GUI 界面..." -ForegroundColor Cyan
& $pythonExe "src\launcher.py"
