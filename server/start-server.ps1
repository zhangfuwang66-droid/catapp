$serverRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$localIP = '127.0.0.1'
$routeSocket = [System.Net.Sockets.Socket]::new(
    [System.Net.Sockets.AddressFamily]::InterNetwork,
    [System.Net.Sockets.SocketType]::Dgram,
    [System.Net.Sockets.ProtocolType]::Udp
)
try {
    # UDP Connect 只查询系统路由，不发送数据，避免选中首个虚拟网卡。
    $routeSocket.Connect('8.8.8.8', 80)
    $localIP = $routeSocket.LocalEndPoint.Address.ToString()
} catch {
    Write-Warning 'Cannot determine the LAN address. Check your active network adapter.'
} finally {
    $routeSocket.Dispose()
}
$healthUrl = 'http://127.0.0.1:8081/api/health'

try {
    $response = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 2
    if ($response.StatusCode -eq 200) {
        Write-Host "AI Desktop Cat server is already running at http://${localIP}:8081"
        Start-Process 'http://127.0.0.1:8081'
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
