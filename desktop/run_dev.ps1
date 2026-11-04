# 开发模式快速启动脚本
Write-Host "🚀 启动赛博小喵 (开发模式)" -ForegroundColor Cyan
Write-Host ""

# 检查虚拟环境
$venvPath = Join-Path $PSScriptRoot '..\server\.venv'
if (Test-Path -LiteralPath (Join-Path $venvPath 'Scripts\python.exe')) {
    Write-Host "✅ 使用虚拟环境: $venvPath" -ForegroundColor Green
    $pythonExe = Join-Path $venvPath "Scripts\python.exe"
} else {
    Write-Host "⚠️ 虚拟环境不存在，使用系统 Python" -ForegroundColor Yellow
    $pythonExe = "python"
}

# 启动 GUI
Write-Host "📱 启动 GUI 界面..." -ForegroundColor Cyan
& $pythonExe (Join-Path $PSScriptRoot 'src\launcher.py')
exit $LASTEXITCODE
