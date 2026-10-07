$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$frontendRoot = Join-Path $projectRoot 'frontend'

foreach ($tool in @('uv', 'npm')) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        Write-Error "$tool is not installed or is not on PATH. Install it and run this script again."
        exit 1
    }
}

if (-not (Test-Path (Join-Path $frontendRoot 'node_modules/vite/bin/vite.js'))) {
    Write-Host 'Installing frontend dependencies...'
    Push-Location $frontendRoot
    try {
        & npm ci
        if ($LASTEXITCODE -ne 0) {
            Write-Error 'Frontend dependency installation failed.'
            exit 1
        }
    }
    finally {
        Pop-Location
    }
}

function Stop-PortListener([int]$port) {
    $connections = @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
    $processIds = @($connections | Select-Object -ExpandProperty OwningProcess -Unique)

    foreach ($processId in $processIds) {
        if ($processId -and $processId -ne $PID) {
            $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
            if ($process) {
                Write-Host "Stopping PID $processId ($($process.ProcessName)) using port $port..."
                Stop-Process -Id $processId -Force -ErrorAction Stop
            }
        }
    }

    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        $stillListening = @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
        if ($stillListening.Count -eq 0) {
            return
        }
        Start-Sleep -Milliseconds 250
    }

    throw "Port $port is still in use. Close its listener and run this script again."
}

Stop-PortListener 8000
Stop-PortListener 5173

$backendPort = 8000
$frontendPort = 5173
$backendCommand = "Set-Location -LiteralPath '$projectRoot'; uv run uvicorn salesmind.main:app --reload --host 127.0.0.1 --port $backendPort"
$frontendCommand = "`$env:SALESMIND_DEV_BACKEND_URL = 'http://127.0.0.1:$backendPort'; Set-Location -LiteralPath '$frontendRoot'; npm run dev -- --port $frontendPort --strictPort"
$backendEncoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($backendCommand))
$frontendEncoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($frontendCommand))

Start-Process powershell.exe -ArgumentList @('-NoExit', '-NoProfile', '-EncodedCommand', $backendEncoded)
Start-Process powershell.exe -ArgumentList @('-NoExit', '-NoProfile', '-EncodedCommand', $frontendEncoded)

Write-Host "Backend:  http://127.0.0.1:$backendPort/health/live"
Write-Host "Frontend: http://127.0.0.1:$frontendPort"
Write-Host 'Close the two service windows to stop the application.'
