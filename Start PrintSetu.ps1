$ErrorActionPreference = 'Stop'
$projectDirectory = $PSScriptRoot
$pythonExecutable = Join-Path $projectDirectory '.venv\Scripts\python.exe'
$frontendDirectory = Join-Path $projectDirectory 'frontend'
$runtimeDirectory = Join-Path $projectDirectory '.runtime'

function Test-ListeningPort([int] $Port) {
    $connection = New-Object System.Net.Sockets.TcpClient
    try {
        $pending = $connection.ConnectAsync('127.0.0.1', $Port)
        return ($pending.Wait(500) -and $connection.Connected)
    } catch { return $false }
    finally { $connection.Dispose() }
}

function Wait-ForEndpoint([string] $Url, $Process) {
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        if ($Process -and $Process.HasExited) { throw "Server exited. See logs in $runtimeDirectory" }
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 2
            if ($response.StatusCode -eq 200) { return }
        } catch { }
        Start-Sleep -Milliseconds 500
    }
    throw "Server did not become ready: $Url. See logs in $runtimeDirectory"
}

if (-not (Test-Path -LiteralPath $pythonExecutable)) { throw 'Python environment missing. Complete the setup in README.md first.' }
if (-not (Test-Path -LiteralPath (Join-Path $frontendDirectory 'node_modules'))) { throw 'Frontend dependencies missing. Run npm install in the frontend folder first.' }
if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) { throw 'Node.js and npm must be installed.' }
New-Item -ItemType Directory -Path $runtimeDirectory -Force | Out-Null
$backendProcess = $null
$frontendProcess = $null

try {
    if (-not (Test-ListeningPort 8000)) {
        & $pythonExecutable -m alembic upgrade head
        if ($LASTEXITCODE -ne 0) { throw 'Database migration failed.' }
        & $pythonExecutable -m backend.app.seed
        if ($LASTEXITCODE -ne 0) { throw 'Local demo initialization failed.' }
        $backendProcess = Start-Process -FilePath $pythonExecutable -ArgumentList '-m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000' -WorkingDirectory $projectDirectory -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimeDirectory 'backend.log') -RedirectStandardError (Join-Path $runtimeDirectory 'backend-errors.log')
    }
    Wait-ForEndpoint 'http://127.0.0.1:8000/api/health' $backendProcess
    $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 3
    if ($health.product_name -ne 'PrintSetu') { throw 'Port 8000 is occupied by another application.' }
    if (-not (Test-ListeningPort 5173)) {
        $frontendProcess = Start-Process -FilePath 'powershell.exe' -ArgumentList '-NoProfile -Command "& npm.cmd run dev"' -WorkingDirectory $frontendDirectory -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimeDirectory 'frontend.log') -RedirectStandardError (Join-Path $runtimeDirectory 'frontend-errors.log')
    }
    Wait-ForEndpoint 'http://127.0.0.1:5173/api/health' $frontendProcess
    $proxiedHealth = Invoke-RestMethod -Uri 'http://127.0.0.1:5173/api/health' -TimeoutSec 3
    if ($proxiedHealth.product_name -ne 'PrintSetu') { throw 'Port 5173 is occupied by another application.' }
    $shopAddress = '127.0.0.1'
    try {
        $network = [System.Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces() | Where-Object {
            $_.OperationalStatus -eq 'Up' -and $_.NetworkInterfaceType -ne 'Loopback' -and $_.GetIPProperties().GatewayAddresses.Count -gt 0
        } | Sort-Object @{ Expression = { if ($_.NetworkInterfaceType -eq 'Wireless80211') { 0 } else { 1 } } } | Select-Object -First 1
        $address = $network.GetIPProperties().UnicastAddresses | Where-Object { $_.Address.AddressFamily -eq 'InterNetwork' } | Select-Object -First 1
        if ($address) { $shopAddress = $address.Address.ToString() }
    } catch { }
    $ownerUrl = "http://${shopAddress}:5173/owner"
    Write-Host "PrintSetu is ready: $ownerUrl"
    Start-Process $ownerUrl
} catch {
    if ($frontendProcess -and -not $frontendProcess.HasExited) { Stop-Process -Id $frontendProcess.Id -ErrorAction SilentlyContinue }
    if ($backendProcess -and -not $backendProcess.HasExited) { Stop-Process -Id $backendProcess.Id -ErrorAction SilentlyContinue }
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
