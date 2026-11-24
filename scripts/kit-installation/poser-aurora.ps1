# Pose AURORA sur ce PC depuis le kit (cle USB), puis lance son installateur en gardant ce qui s'affiche.
# Lance par "Installer AURORA.bat" (nomme POSER-AURORA.bat jusqu'au 24/11/2026), depuis le dossier du kit. Ce script :
#   1. copie le dossier "aurora" du kit vers C:\AURORA (ou -Destination) : AURORA ne doit pas tourner depuis la cle,
#      son icone du Bureau pointerait vers elle ;
#   2. compare le nombre de fichiers et d'octets des deux cotes ;
#   3. lance installer-aurora.ps1 depuis la copie, avec -Journal : tout ce qui s'affiche est garde dans
#      resultats\<nom du PC>_<date>\installation.txt, sur la cle.
# Il n'ecrase rien : si la destination existe, il s'arrete. Il ne telecharge rien lui-meme : c'est l'installateur
# qui pose ses questions (o/n) avant chaque telechargement.
# Pas d'accents dans ce fichier : PowerShell 5.1 lit mal un script UTF-8 sans BOM (lecon du kit d'Athena).
#
#   -Destination <dossier>   a la place de C:\AURORA
#   -Verifier                l'installateur dit seulement ce qui est la et ce qui manque, sans rien changer
#   -SansInstallation        copie et compare seulement
#   -Console                 l'installateur dans cette console, avec ses questions (o/n), au lieu de la fenetre
# Ecrit le 22/11/2026 (ID Studio IA : "prepare aussi un kit installation comme Athena si besoin sur le disc H").
# Le meme soir (ID Studio IA : "mets-moi une animation pour installation sur l'autre PC") : apres la copie, c'est la fenetre
# d'installation (fenetre-installation.ps1) qui s'ouvre ; elle garde son journal au meme endroit, sur la cle.
param([string]$Destination = 'C:\AURORA', [switch]$Verifier, [switch]$SansInstallation, [switch]$Console)
$ErrorActionPreference = 'Stop'
$kit = $PSScriptRoot
$source = Join-Path $kit 'aurora'

if (-not (Test-Path -LiteralPath (Join-Path $source 'installer-aurora.ps1'))) {
    Write-Host "ARRET : le dossier 'aurora' du kit est introuvable a cote de ce script ($source)."
    exit 1
}
if (Test-Path -LiteralPath $Destination) {
    Write-Host "ARRET : $Destination existe deja. Rien n'est ecrase."
    Write-Host "  - Pour reprendre une installation commencee : double-clic sur $Destination\INSTALLER.bat"
    Write-Host "  - Pour poser AURORA ailleurs : poser-aurora.ps1 -Destination <autre dossier>"
    exit 1
}

$resultats = Join-Path $kit ('resultats\' + $env:COMPUTERNAME + '_' + (Get-Date -Format 'yyyy-MM-dd_HH-mm'))
New-Item -ItemType Directory -Force -Path $resultats | Out-Null
$notes = Join-Path $resultats 'copie.txt'

Write-Host "Copie d'AURORA : $source  ->  $Destination"
$debut = Get-Date
& robocopy.exe $source $Destination /E /R:1 /W:1 /NFL /NDL /NP /NJH /NJS | Out-Null
$code = $LASTEXITCODE                      # robocopy : 0 a 7 = reussi, 8 et plus = au moins un echec
$duree = [math]::Round(((Get-Date) - $debut).TotalSeconds, 1)
$la = @(Get-ChildItem -LiteralPath $source -Recurse -File -Force)
$ici = @(Get-ChildItem -LiteralPath $Destination -Recurse -File -Force)
$octetsLa = ($la | Measure-Object Length -Sum).Sum
$octetsIci = ($ici | Measure-Object Length -Sum).Sum
$lignes = @(
    "PC : $env:COMPUTERNAME, le $(Get-Date -Format 'dd/MM/yyyy HH:mm')",
    "kit : $kit",
    "destination : $Destination",
    "copie en $duree s (code robocopy $code)",
    "kit : $($la.Count) fichiers, $octetsLa octets",
    "PC  : $($ici.Count) fichiers, $octetsIci octets"
)
$lignes | Set-Content -LiteralPath $notes -Encoding UTF8
$lignes | ForEach-Object { Write-Host "  $_" }
if ($code -ge 8 -or $la.Count -ne $ici.Count -or $octetsLa -ne $octetsIci) {
    Write-Host "ARRET : la copie n'est pas complete (voir les deux lignes au-dessus). L'installation n'est pas lancee."
    exit 1
}
Write-Host "  copie complete et verifiee"

if ($SansInstallation) { Write-Host "Installation non lancee (-SansInstallation)."; exit 0 }

$journal = Join-Path $resultats 'installation.txt'
$fenetre = Join-Path $Destination 'fenetre-installation.ps1'
if (-not $Verifier -and -not $Console -and (Test-Path -LiteralPath $fenetre)) {
    Write-Host ""
    Write-Host "La fenetre d'installation d'AURORA s'ouvre. Rien n'est telecharge avant le clic sur INSTALLER."
    Write-Host "Ce que l'installateur ecrit est garde sur la cle : $journal"
    Start-Process -FilePath 'powershell.exe' -WindowStyle Hidden -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass',
        '-STA', '-File', ('"' + $fenetre + '"'), '-Journal', ('"' + $journal + '"'))
    exit 0
}
$arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $Destination 'installer-aurora.ps1'), '-Journal', $journal)
if ($Verifier) { $arguments += '-Verifier' }
Write-Host ""
& powershell.exe @arguments
$fin = $LASTEXITCODE
Write-Host ""
Write-Host "Ce qui s'est affiche est garde sur la cle : $journal"
exit $fin
