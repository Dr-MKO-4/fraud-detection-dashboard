param(
    [switch]$Verifier,
    [switch]$SansNettoyage,
    [switch]$SansNavigateur,
    [switch]$Detache,
    [switch]$FluxComplet,
    [string]$KafkaDir = 'M:\BigData\Tools\Kafka',
    [string]$RedisDir = 'M:\Redis-x64-3.0.504',
    [string]$JavaHome = 'M:\BigData\java\jdk-17'
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\gestion_services.ps1"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
$services = [System.Collections.Generic.List[System.Diagnostics.Process]]::new()
$records = [System.Collections.Generic.List[object]]::new()
$sessionStarted = $false
$logDir = Join-Path $PSScriptRoot ('logs\' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))

function Test-Port([int]$Port) {
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $task = $client.ConnectAsync('127.0.0.1', $Port)
        return ($task.Wait(300) -and $client.Connected)
    } catch { return $false } finally { $client.Dispose() }
}

function Start-ServiceProcess($Name, $Executable, $Arguments, $Directory) {
    if (Test-Path -LiteralPath $stopPath) { throw 'Arret demande.' }
    Write-Host "[DEMARRAGE] $Name"
    $process = Start-Process -FilePath $Executable -ArgumentList $Arguments `
        -WorkingDirectory $Directory -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $logDir "$Name.log") `
        -RedirectStandardError (Join-Path $logDir "$Name.err.log")
    $services.Add($process)
    $records.Add(@{name=$Name; id=$process.Id; started=$process.StartTime.ToUniversalTime().Ticks.ToString()})
    @{services=@($records.ToArray()); logs=$logDir} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $statePath -Encoding UTF8
    return $process
}

function Wait-Ready($Name, $Process, [scriptblock]$Probe, [int]$Timeout = 120, [scriptblock]$Progress = $null) {
    $deadline = (Get-Date).AddSeconds($Timeout)
    $nextMessage = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $deadline) {
        if (Test-Path -LiteralPath $stopPath) { throw 'Arret demande.' }
        foreach ($running in $services) {
            if ($running.HasExited) {
                $name = ($records | Where-Object id -eq $running.Id).name
                throw "$name s'est arrete (PID $($running.Id)). Voir $logDir\$name.log et $name.err.log"
            }
        }
        if (& $Probe) { Write-Host "[OK] $Name pret"; return }
        if ((Get-Date) -ge $nextMessage) {
            $detail = 'verification en cours...'
            if ($Progress) { try { $detail = & $Progress } catch { } }
            $reste = [int]($deadline - (Get-Date)).TotalSeconds
            Write-Host "[ATTENTE] $Name : $detail (delai restant : $reste s)"
            $nextMessage = (Get-Date).AddSeconds(30)
        }
        Start-Sleep -Seconds 1
    }
    throw "Delai depasse pour $Name. Voir $logDir"
}

try {
    foreach ($file in @($python, "$RedisDir\redis-server.exe", "$RedisDir\redis-cli.exe",
        "$JavaHome\bin\java.exe", "$KafkaDir\bin\windows\zookeeper-server-start.bat",
        "$KafkaDir\bin\windows\kafka-server-start.bat", "$KafkaDir\config\server.properties",
        "$KafkaDir\config\zookeeper.properties", "$root\nettoyer_caches.ps1", "$root\MOMTSIM\run_live.py")) {
        if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "Fichier manquant : $file" }
    }
    # Le nettoyeur existant utilise ces deux emplacements fixes.
    if (-not $SansNettoyage -and ($KafkaDir -ne 'M:\BigData\Tools\Kafka' -or $RedisDir -ne 'M:\Redis-x64-3.0.504')) {
        throw 'Chemins personnalises : utiliser -SansNettoyage (nettoyeur configure avec des chemins fixes).'
    }
    # Une session suivie tourne deja (par exemple apres fermeture de cette fenetre par la croix) :
    # si elle est complete, on rouvre simplement le dashboard ; sinon on explique quoi faire.
    $vivants = @(Get-SessionAlive)
    if ($vivants.Count -gt 0) {
        $dashOk = $false
        try { $dashOk = (Invoke-WebRequest 'http://127.0.0.1:8050' -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200 } catch { }
        if ($dashOk -and $vivants.Count -ge 5) {
            Write-Host '[INFO] Une session tourne deja : ouverture du dashboard (http://localhost:8050).'
            Write-Host '       Pour l arreter et nettoyer : arreter_nettoyer.bat'
            if (-not $SansNavigateur) { Start-Process 'http://localhost:8050' }
            exit 0
        }
        throw ("Une session est deja en cours de demarrage ou incomplete (" + (($vivants | ForEach-Object { $_.name }) -join ', ') +
               "). Attendez la fin de son demarrage, ou lancez arreter_nettoyer.bat puis relancez.")
    }
    if (Test-Path -LiteralPath $statePath) {
        Write-Host '[INFO] La session precedente s est terminee sans arret propre : son etat est efface.'
        Remove-Item -LiteralPath $statePath -Force
    }
    foreach ($port in @(6379, 2181, 9092, 8766, 8050)) {
        if (Test-Port $port) {
            throw ("Port $port deja utilise (programme etranger ou service de la demo reste ouvert). " +
                   "Lancez arreter_nettoyer.bat : il arrete les services de la demo restes ouverts et signale les autres.")
        }
    }
    if ($Verifier) { Write-Host '[OK] Prerequis et ports verifies. Aucun service lance, aucun cache efface.'; exit 0 }
    Remove-Item -LiteralPath $stopPath -Force -ErrorAction SilentlyContinue
    $sessionStarted = $true

    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    Write-Host "Journaux : $logDir"
    if (-not $SansNettoyage) {
        # Nettoyage habituel : Python, Redis et topic de demo uniquement.
        # Pas de -Tout : les autres topics et ZooKeeper sont conserves.
        & "$root\nettoyer_caches.ps1"
    }
    $env:JAVA_HOME = $JavaHome
    $env:PYTHONUNBUFFERED = '1'
    $p = Start-ServiceProcess Redis "$RedisDir\redis-server.exe" @('--dir', "`"$RedisDir`"") $RedisDir
    Wait-Ready Redis $p { Test-Port 6379 }
    $p = Start-ServiceProcess ZooKeeper $env:ComSpec @('/d', '/s', '/c', '"bin\windows\zookeeper-server-start.bat config\zookeeper.properties"') $KafkaDir
    Write-Host 'Attente de ZooKeeper et de l expiration des anciennes sessions Kafka...'
    Wait-Ready ZooKeeper $p {
        if (-not (Test-Port 2181)) { return $false }
        $probeOut = Join-Path $logDir 'ZooKeeper-probe.log'
        $probeErr = Join-Path $logDir 'ZooKeeper-probe.err.log'
        $probe = Start-Process $env:ComSpec -ArgumentList @('/d', '/s', '/c', '"bin\windows\zookeeper-shell.bat localhost:2181 ls /brokers/ids"') -WorkingDirectory $KafkaDir -WindowStyle Hidden -PassThru -RedirectStandardOutput $probeOut -RedirectStandardError $probeErr
        if (-not $probe.WaitForExit(15000)) {
            & "$env:SystemRoot\System32\taskkill.exe" /PID $probe.Id /T /F | Out-Null
            return $false
        }
        # Une reponse vide signifie que ZooKeeper sert les requetes et que
        # les anciennes inscriptions ephemeres des brokers ont expire.
        $output = Get-Content -LiteralPath $probeOut -Raw
        return ($output -match '(?m)^\[\]\s*$' -or $output -match 'Node does not exist: /brokers/ids')
    } 240
    $p = Start-ServiceProcess Kafka $env:ComSpec @('/d', '/s', '/c', '"bin\windows\kafka-server-start.bat config\server.properties"') $KafkaDir
    Wait-Ready Kafka $p {
        if (-not (Test-Port 9092)) { return $false }
        $code = "from kafka import KafkaAdminClient; c=KafkaAdminClient(bootstrap_servers='127.0.0.1:9092',request_timeout_ms=5000); c.list_topics(); c.close(); print('KAFKA_READY')"
        $probe = Start-Process $python -ArgumentList @('-c', "`"$code`"") -WindowStyle Hidden -PassThru -RedirectStandardOutput "$logDir\Kafka-probe.log" -RedirectStandardError "$logDir\Kafka-probe.err.log"
        if (-not $probe.WaitForExit(12000)) {
            & "$env:SystemRoot\System32\taskkill.exe" /PID $probe.Id /T /F | Out-Null
            return $false
        }
        return (Get-Content -LiteralPath "$logDir\Kafka-probe.log" -Raw) -match '(?m)^KAFKA_READY\s*$'
    } 180
    # Flux reduit par defaut : les finalistes (634 a 1 290 ms pour 256 fenetres sur ce poste) ne suivent pas le flux
    # par defaut du producteur. -FluxComplet garde les reglages par defaut de run_live.py.
    $argsMoMTSim = if ($FluxComplet) { @('run_live.py') } else {
        @('run_live.py', '--n-clients', '4000', '--volume-scale', '0.01', '--n-mules', '30', '--warmup-steps', '300', '--tick', '2') }
    $p = Start-ServiceProcess MoMTSim $python $argsMoMTSim "$root\MOMTSIM"
    Write-Host 'Attente de la fin de l echauffement MoMTSim (jusqu a 15 minutes)...'
    Wait-Ready MoMTSim $p {
        try {
            $health = Invoke-RestMethod 'http://127.0.0.1:8766/live/health' -TimeoutSec 2
            return ($health.status -eq 'ok' -and $health.warming -eq $false -and $health.step -gt 0)
        } catch { return $false }
    } 900 {
        try {
            $h = Invoke-RestMethod 'http://127.0.0.1:8766/live/health' -TimeoutSec 2
            if ($h.warming) { "echauffement en cours, pas $($h.step)" } else { "pas $($h.step)" }
        } catch { 'producteur en cours de chargement' }
    }
    $p = Start-ServiceProcess Dashboard $python @('-m', 'dashboard.app') $root
    Wait-Ready Dashboard $p {
        try { return (Invoke-WebRequest 'http://127.0.0.1:8050' -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200 }
        catch { return $false }
    } 180
    if (-not $SansNavigateur) { Start-Process 'http://localhost:8050' }
    Write-Host "Dashboard disponible : http://localhost:8050"
    if (-not $Detache) {
        Write-Host 'Gardez cette fenetre ouverte. Pour arreter et nettoyer : arreter_nettoyer.bat (ou Ctrl+C ici).'
        Write-Host 'Si cette fenetre est fermee par la croix, les services continuent : relancer demarrer_dashboard.bat rouvre le dashboard, arreter_nettoyer.bat les arrete.'
        while (Test-Path -LiteralPath $statePath) {
            if (Test-Path -LiteralPath $stopPath) { break }
            foreach ($running in $services) {
                if ($running.HasExited) { throw "Service arrete (PID $($running.Id)). Voir $logDir" }
            }
            Start-Sleep -Seconds 2
        }
    }
} catch {
    if ($sessionStarted -and (Test-Path -LiteralPath $stopPath)) {
        Write-Host '[INFO] Arret demande par arreter_nettoyer.bat.'
    } else {
        Write-Host "[ERREUR] $($_.Exception.Message)" -ForegroundColor Red
        $failed = $true
    }
} finally {
    if ($services.Count -gt 0 -and (-not $Detache -or $failed)) { Stop-DashboardSession -ExpectedLogDir $logDir }
}
if ($failed) { exit 1 }
