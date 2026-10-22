$serverRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$healthUrl = 'http://127.0.0.1:8081/api/health'

try {
    $response = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 2
    if ($response.StatusCode -eq 200) {
        Write-Host 'AI Desktop Cat server is already running at http://192.168.252.45:8081'
        Start-Process 'http://192.168.252.45:8081'
        exit 0
    }
} catch {
    # The health check is expected to fail when the server is not running.
}

$pythonPath = Join-Path $serverRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Python environment not found: $pythonPath"
}

Set-Location -LiteralPath $serverRoot
Write-Host 'Starting AI Desktop Cat server on http://192.168.252.45:8081'
& $pythonPath 'app.py'
