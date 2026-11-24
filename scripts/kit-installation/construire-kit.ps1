# Construit le kit d'installation d'AURORA dans un dossier (une cle USB, par exemple), a partir de ce qui est
# enregistre dans Git : jamais la base, les cles, les journaux ni les modeles, qui ne sont pas dans Git.
# Le dossier des rapports de travail (reviews/) n'est pas emporte : il raconte l'usage d'ID Studio IA.
# Son projet de robot (docs/robot-quadrupede) non plus, depuis le 23/11/2026 : "il faut supprimer dans la version a
# distribuer mon projet de robot". Il reste dans son AURORA ; une AURORA posee par ce kit demarre sans projet.
# Rien n'est ecrase : si <Destination>\aurora existe, le script s'arrete.
# Pas d'accents dans ce fichier : PowerShell 5.1 lit mal un script UTF-8 sans BOM.
# Usage : powershell -ExecutionPolicy Bypass -File scripts\kit-installation\construire-kit.ps1 -Destination H:\aurora-kit-installation
# Ecrit le 22/11/2026 (ID Studio IA : "prepare aussi un kit installation comme Athena si besoin sur le disc H").
#         ... -MettreAJour   remet a jour un kit deja construit par ce script (son dossier "aurora" et ses fichiers
#                            de tete) ; les autres fichiers du kit (REPRISE, resultats) ne sont pas touches.
param([Parameter(Mandatory = $true)][string]$Destination, [string]$Version = 'HEAD', [switch]$MettreAJour)
$ErrorActionPreference = 'Stop'
$projet = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$cible = Join-Path $Destination 'aurora'

$dejaLa = Test-Path -LiteralPath $cible
if ($dejaLa -and -not $MettreAJour) {
    Write-Host "ARRET : $cible existe deja. Rien n'est ecrase : donner un autre dossier, ou -MettreAJour pour un kit de ce script."
    exit 1
}
if ($dejaLa -and -not ((Test-Path -LiteralPath (Join-Path $Destination 'VERSION.txt')) -and (Test-Path -LiteralPath (Join-Path $cible 'installer-aurora.ps1')))) {
    Write-Host "ARRET : $Destination ne ressemble pas a un kit construit par ce script (VERSION.txt, aurora\installer-aurora.ps1). Rien n'est touche."
    exit 1
}
$enCours = (& git -C $projet status --porcelain --untracked-files=no | Out-String).Trim()
if ($enCours) {
    Write-Host "ARRET : des changements ne sont pas enregistres dans Git. Le kit ne prend que ce qui est enregistre :"
    Write-Host $enCours
    exit 1
}
$commit = (& git -C $projet rev-parse --short $Version | Out-String).Trim()
$dateCommit = (& git -C $projet log -1 --format=%cd --date=format:'%d/%m/%Y %H:%M' $Version | Out-String).Trim()

$nonEmportes = @('reviews', 'docs/robot-quadrupede')      # ce que la version a distribuer ne porte pas
$exclus = @($nonEmportes | ForEach-Object { ":(exclude)$_" })
$archive = Join-Path $env:TEMP "aurora-kit-$commit-$PID.zip"
& git -C $projet archive --format=zip -o $archive $Version -- . @exclus
if ($LASTEXITCODE -ne 0) { Write-Host "ARRET : git archive a echoue."; exit 1 }
if ($dejaLa) {
    # Mise a jour : l'archive est ouverte a part, puis le dossier du kit est rendu identique a elle (robocopy /MIR).
    $neuf = Join-Path $env:TEMP "aurora-kit-$commit-$PID"
    Expand-Archive -LiteralPath $archive -DestinationPath $neuf
    & robocopy.exe $neuf $cible /MIR /R:1 /W:1 /NFL /NDL /NP /NJH /NJS | Out-Null
    $codeCopie = $LASTEXITCODE
    Remove-Item -LiteralPath $neuf -Recurse -Force      # le dossier ouvert a part, cree par ce script dans TEMP
    if ($codeCopie -ge 8) { Write-Host "ARRET : la mise a jour du kit a echoue (robocopy $codeCopie)."; exit 1 }
    Write-Host "Kit mis a jour sur place : $cible"
} else {
    New-Item -ItemType Directory -Force -Path $cible | Out-Null
    Expand-Archive -LiteralPath $archive -DestinationPath $cible
}
Remove-Item -LiteralPath $archive -Force

# Les fichiers du kit sont pris dans l'archive, pas dans le dossier de travail : c'est la version enregistree,
# avec les fins de ligne de Windows (un .bat aux fins de ligne Unix peut mal se lire).
foreach ($fichier in 'Installer AURORA.bat', 'poser-aurora.ps1', 'LISEZ-MOI.txt') {
    Copy-Item -LiteralPath (Join-Path $cible "scripts\kit-installation\$fichier") -Destination $Destination
}
# 24/11/2026 (ID Studio IA : "je veux avoir une icone Installer Aurora a la place (poser Aurora)") : le lanceur du kit
# s'appelait POSER-AURORA.bat. Un kit mis a jour ne garde pas les deux : l'ancien, pose par ce script, est retire.
$ancienLanceur = Join-Path $Destination 'POSER-AURORA.bat'
if (Test-Path -LiteralPath $ancienLanceur) { Remove-Item -LiteralPath $ancienLanceur -Force }
New-Item -ItemType Directory -Force -Path (Join-Path $Destination 'resultats') | Out-Null

$fichiers = @(Get-ChildItem -LiteralPath $cible -Recurse -File -Force)
$octets = ($fichiers | Measure-Object Length -Sum).Sum
$lignes = @(
    "AURORA, version $commit du $dateCommit",
    "kit construit le $(Get-Date -Format 'dd/MM/yyyy HH:mm') sur $env:COMPUTERNAME",
    "dossier aurora : $($fichiers.Count) fichiers, $octets octets",
    "non emportes : les rapports de travail (reviews/), le projet de robot d'ID Studio IA (docs/robot-quadrupede), l'historique Git, la base, les cles, les journaux, les modeles"
)
$lignes | Set-Content -LiteralPath (Join-Path $Destination 'VERSION.txt') -Encoding UTF8
$lignes | ForEach-Object { Write-Host $_ }
Write-Host "Kit pret : $Destination"
