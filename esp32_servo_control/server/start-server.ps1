$serverRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$localIP = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } | Select-Object -First 1).IPAddress
$healthUrl = 'http://127.0.0.1:8081/api/health'

try {
    $response = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 2
    if ($response.StatusCode -eq 200) {
        Write-Host "AI Desktop Cat server is already running at http://${localIP}:8081"
        Start-Process "http://${localIP}:8081"
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
Write-Host "Starting AI Desktop Cat server on http://${localIP}:8081"
Write-Host "Please configure ESP32 with this IP address: $localIP"
& $pythonPath 'app.py'
