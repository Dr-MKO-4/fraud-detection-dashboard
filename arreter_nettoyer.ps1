param([switch]$SansNettoyage)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\gestion_services.ps1"
try {
    New-Item -ItemType Directory -Path "$PSScriptRoot\logs" -Force | Out-Null
    Set-Content -LiteralPath $stopPath -Value 'stop'
    Stop-DashboardSession
    # Processus de la demo restes sans suivi (fenetre fermee par la croix, enfant orphelin) : arretes aussi.
    $autres = Stop-PortsOrphelins
    if ($autres.Count -gt 0) {
        throw ("Ports occupes par des programmes etrangers a la demo, non arretes : " + ($autres -join ' ; ') +
               ". Nettoyage annule ; fermez-les puis relancez arreter_nettoyer.bat.")
    }
    Remove-Item -LiteralPath $statePath -Force -ErrorAction SilentlyContinue
    if (-not $SansNettoyage) { & "$(Split-Path -Parent $PSScriptRoot)\nettoyer_caches.ps1" }
    Write-Host '[OK] Services arretes. Nettoyage termine.'
} catch {
    Write-Host "[ERREUR] $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
