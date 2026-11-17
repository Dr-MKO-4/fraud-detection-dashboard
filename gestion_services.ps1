$statePath = Join-Path $PSScriptRoot 'logs\session.json'
$stopPath = Join-Path $PSScriptRoot 'logs\arreter.signal'

$PortsSession = @(8050, 8766, 9092, 2181, 6379)

function Get-SessionAlive {
    # Services suivis encore vivants (meme PID et meme heure de demarrage)
    if (-not (Test-Path -LiteralPath $statePath)) { return @() }
    try { $session = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json } catch { return @() }
    $vivants = @()
    foreach ($item in @($session.services)) {
        $p = Get-Process -Id $item.id -ErrorAction SilentlyContinue
        if ($p -and $p.StartTime.ToUniversalTime().Ticks.ToString() -eq $item.started) { $vivants += $item }
    }
    return $vivants
}

function Test-ProcessusSession($ProcessId) {
    # Vrai si le processus est un des services de la demo (reconnu a sa ligne de commande)
    $p = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
    if (-not $p -or -not $p.CommandLine) { return $false }
    $c = $p.CommandLine
    return ($c -match 'redis-server' -or $c -match 'kafka\.Kafka' -or $c -match 'zookeeper' -or
            $c -match 'run_live\.py' -or $c -match 'dashboard\.app')
}

function Stop-PortsOrphelins {
    # Arrete les processus de la demo qui tiennent encore un port de la session sans etre suivis
    # (enfant survivant a son parent, fenetre de demarrage fermee par la croix). Les autres sont signales.
    $autres = @()
    foreach ($port in $PortsSession) {
        foreach ($cx in @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)) {
            $owner = $cx.OwningProcess
            if (Test-ProcessusSession $owner) {
                Write-Host "[ARRET] processus orphelin sur le port $port (PID $owner)"
                & "$env:SystemRoot\System32\taskkill.exe" /PID $owner /T /F | Out-Null
            } else {
                $n = (Get-Process -Id $owner -ErrorAction SilentlyContinue).ProcessName
                $autres += "port $port : $n (PID $owner)"
            }
        }
    }
    Start-Sleep -Seconds 2
    return $autres
}

function Stop-DashboardSession {
    param([string]$ExpectedLogDir)
    $gate = [System.Threading.Mutex]::new($false, 'Local\MKANDashboardStop')
    $locked = $false
    try {
        $locked = $gate.WaitOne(30000)
        if (-not $locked) { throw 'Un arret est deja en cours.' }
        if (-not (Test-Path -LiteralPath $statePath)) { return }
        $session = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
        if ($ExpectedLogDir -and $session.logs -ne $ExpectedLogDir) { return }
        $items = @($session.services)
        for ($i = $items.Count - 1; $i -ge 0; $i--) {
            $item = $items[$i]
            $running = Get-Process -Id $item.id -ErrorAction SilentlyContinue
            if ($running -and $running.StartTime.ToUniversalTime().Ticks.ToString() -eq $item.started) {
                Write-Host "[ARRET] $($item.name) (PID $($item.id))"
                & "$env:SystemRoot\System32\taskkill.exe" /PID $item.id /T /F | Out-Null
                if ($LASTEXITCODE -ne 0 -and (Get-Process -Id $item.id -ErrorAction SilentlyContinue)) {
                    throw "Impossible d'arreter $($item.name). Nettoyage annule."
                }
                $running.WaitForExit(10000) | Out-Null
            }
        }
        Remove-Item -LiteralPath $statePath -Force
    } finally {
        if ($locked) { $gate.ReleaseMutex() }
        $gate.Dispose()
    }
}
