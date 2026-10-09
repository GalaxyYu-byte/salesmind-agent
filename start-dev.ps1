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
    $deadline = (Get-Date).AddSeconds(10)

    do {
        $connections = @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
        if ($connections.Count -eq 0) {
            return
        }

        # Uvicorn workers inherit the listening socket. Windows can still report
        # the reloader's PID after it exits, so find children even for a dead PID.
        $snapshot = @(Get-CimInstance Win32_Process -ErrorAction Stop)
        $pending = New-Object 'System.Collections.Generic.Queue[int]'
        $seen = New-Object 'System.Collections.Generic.HashSet[int]'
        $processTree = @()
        foreach ($ownerId in @($connections | Select-Object -ExpandProperty OwningProcess -Unique)) {
            if ($ownerId -gt 0) {
                $pending.Enqueue([int]$ownerId)
            }
        }

        while ($pending.Count -gt 0) {
            $processId = $pending.Dequeue()
            if (-not $seen.Add($processId)) {
                continue
            }
            if ($processId -eq $PID) {
                throw "Refusing to stop the current startup process using port $port."
            }
            $processTree += @($snapshot | Where-Object { $_.ProcessId -eq $processId })
            foreach ($child in @($snapshot | Where-Object { $_.ParentProcessId -eq $processId })) {
                $pending.Enqueue([int]$child.ProcessId)
            }
        }

        # Stop supervisors before workers so they cannot restart the workers.
        foreach ($entry in $processTree) {
            $process = Get-Process -Id $entry.ProcessId -ErrorAction SilentlyContinue
            $startedAt = if ($process) { $process.StartTime } else { $null }
            if ($startedAt -and [Math]::Abs(($startedAt.ToUniversalTime() - $entry.CreationDate.ToUniversalTime()).TotalMilliseconds) -lt 1) {
                Write-Host "Stopping PID $($entry.ProcessId) ($($entry.Name)) using port $port..."
                # Children can exit on their own while their parent is stopping.
                Stop-Process -InputObject $process -Force -ErrorAction SilentlyContinue
            }
        }

        Start-Sleep -Milliseconds 250
    } while ((Get-Date) -lt $deadline)

    $remaining = @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
    if ($remaining.Count -gt 0) {
        $owners = ($remaining | Select-Object -ExpandProperty OwningProcess -Unique) -join ', '
        throw "Port $port is still in use by PID(s) $owners after stopping its process tree."
    }
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
