# Installe AURORA sur ce PC, en suivant les étapes du README : vérifie Python, Node.js et Ollama, crée l'environnement
# Python dans ~/.aurora/venv, installe les bibliothèques, prépare l'interface, propose les modèles locaux d'après la
# carte graphique, pose une icône sur le Bureau, puis vérifie que le service démarre.
# Rien n'est téléchargé sans un « o » tapé au clavier (ou -Oui). Depuis le 23/11/2026, s'il manque Python (de 3.11 à
# 3.14, en 64 bits), Node.js ou Ollama, il va chercher lui-même l'installateur officiel, à la version essayée, vérifie
# son empreinte et l'installe (comme l'installateur d'Athéna). Il ne touche jamais aux données d'une AURORA déjà installée.
# Usage :  powershell -ExecutionPolicy Bypass -File .\installer-aurora.ps1             pose ses questions
#          … -Verifier            ne change rien : dit seulement ce qui est là et ce qui manque
#          … -Oui                 accepte les téléchargements proposés (bibliothèques, interface, modèles)
#          … -SansModeles         ne propose aucun modèle Ollama
#          … -SansTelechargement  ne lance ni pip, ni npm, ni Ollama (essai du script lui-même)
#          … -SansIcone           ne pose pas l'icône du Bureau
#          … -Dossier <chemin>    dossier d'AURORA à la place de ~/.aurora (essais : l'environnement y est créé)
#          … -Journal <fichier>   garde dans ce fichier tout ce qui s'affiche (essai sur un autre PC, 22/11/2026)
# Pour les essais seulement, deux variables d'environnement :
#   AURORA_ESSAI_SANS=Python,Node.js,Ollama   tient ces programmes pour absents de ce PC
#   AURORA_ESSAI_PROGRAMMES=1                 télécharge et vérifie leurs installateurs, mais n'en installe aucun
#   AURORA_ESSAI_SOURCE=http://127.0.0.1:…    prend ces installateurs à cette adresse (un serveur d'essai sur ce PC) ;
#                                             les empreintes attendues ne changent pas : un faux fichier reste refusé
# Écrit le 21/11/2026 (ID Studio IA : « fais-moi la suite du projet 1.2.3.4 », point 3). Reprend ce que fait start-aurora.ps1
# pour trouver Python ; ne lance pas AURORA : c'est le rôle d'AURORA.bat.
param([switch]$Verifier, [switch]$Oui, [switch]$SansModeles, [switch]$SansTelechargement, [switch]$SansIcone,
      [string]$Dossier = '', [string]$Journal = '')
$ErrorActionPreference = 'Stop'
if ($env:AURORA_FENETRE) {
  # Lancé par la fenêtre d'installation (fenetre-installation.ps1), qui lit ce qui s'affiche : en UTF-8, pour les accents.
  try { [Console]::OutputEncoding = New-Object Text.UTF8Encoding $false } catch { }
}
if ($Journal) {
  # Ce qui s'affiche, les questions et les réponses tapées sont gardés : sur un autre PC, c'est le compte rendu.
  New-Item -ItemType Directory -Force (Split-Path -Parent $Journal) | Out-Null
  Start-Transcript -Path $Journal -Force | Out-Null
}
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$maison = if ($Dossier) { $Dossier } else { Join-Path $HOME '.aurora' }
$venv = Join-Path $maison 'venv'
$python = Join-Path $venv 'Scripts\python.exe'
$manques = @()      # ce qui empêche d'installer
$remarques = @()    # ce qui reste à faire à la main, dit à la fin

function Titre([string]$texte) { Write-Host ''; Write-Host "== $texte" -ForegroundColor Cyan }
function Bon([string]$texte) { Write-Host "  [ok]     $texte" -ForegroundColor Green }
function Manque([string]$texte) { Write-Host "  [manque] $texte" -ForegroundColor Yellow }
function Note([string]$texte) { Write-Host "  $texte" }

function Accord([string]$question) {
  # Un téléchargement ne part que sur un « o » tapé, ou avec -Oui. En mode -Verifier, jamais.
  if ($Verifier -or $SansTelechargement) { return $false }
  if ($Oui) { return $true }
  $reponse = Read-Host "  $question (o/n)"
  return ($reponse -match '^(o|oui|y|yes)$')
}

function Natif-Reussi([scriptblock]$bloc) {
  # Une commande externe qui écrit sur sa sortie d'erreur (un module Python absent, un avertissement) ne doit pas
  # arrêter le script : seul son code de sortie compte. Essai du 21/11/2026 dans un dossier neuf : il s'arrêtait là.
  $ancien = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try { & $bloc *> $null; return ($LASTEXITCODE -eq 0) } catch { return $false } finally { $ErrorActionPreference = $ancien }
}

function Version-De([string]$commande, [string[]]$arguments) {
  # '' quand la commande n'existe pas ; sinon son premier numéro de version.
  $ancien = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try {
    $sortie = (& $commande @arguments 2>&1 | Out-String)
  } catch { return '' } finally { $ErrorActionPreference = $ancien }
  if ($sortie -match '(\d+)\.(\d+)(?:\.(\d+))?') { return $Matches[0] }
  return ''
}

function Au-Moins([string]$version, [int]$majeur, [int]$mineur) {
  if (-not $version) { return $false }
  $parts = $version.Split('.')
  $a = [int]$parts[0]; $b = if ($parts.Count -gt 1) { [int]$parts[1] } else { 0 }
  return ($a -gt $majeur) -or ($a -eq $majeur -and $b -ge $mineur)
}

function Memoire-Carte {
  # La mémoire de la carte graphique la plus grande, en Go (lue dans le registre : Win32_VideoController plafonne à 4 Go).
  $meilleure = @{ Nom = ''; Go = 0.0 }
  $cle = 'HKLM:\SYSTEM\ControlSet001\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}'
  foreach ($sous in @(Get-ChildItem $cle -ErrorAction SilentlyContinue)) {
    $v = Get-ItemProperty $sous.PSPath -ErrorAction SilentlyContinue
    $octets = $v.'HardwareInformation.qwMemorySize'
    if ($octets -and ($octets / 1GB) -gt $meilleure.Go) { $meilleure = @{ Nom = [string]$v.DriverDesc; Go = [math]::Round($octets / 1GB, 1) } }
  }
  if (-not $meilleure.Nom) {
    $carte = Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($carte) { $meilleure = @{ Nom = [string]$carte.Name; Go = [math]::Round($carte.AdapterRAM / 1GB, 1) } }
  }
  return $meilleure
}

# ---------------------------------------------------------------- Les programmes que l'installateur va chercher lui-même
# 23/11/2026, PC du garage d'ID Studio IA. Python y avait été installé à la main depuis python.org, dont la page donne le plus
# récent, et pip s'est arrêté sur « No matching distribution found for onnxruntime » : cette bibliothèque (la voix)
# n'existe sous Windows que pour Python 3.11 à 3.14, en 64 bits, et ce script acceptait « 3.11 ou plus récent ».
# ID Studio IA : « tu peux pas mettre les téléchargements automatiques pour les dépendances comme Athéna ». Comme
# l'INSTALLER.bat d'Athéna (11/10/2026) : l'installateur officiel, à la version essayée, pris chez son éditeur, refusé
# si son empreinte n'est pas celle que l'éditeur publie, puis installé. Empreintes relues le 23/11/2026 sur
# nodejs.org/dist/v22.23.3/SHASUMS256.txt et le « digest » de GitHub pour Ollama v0.35.0 : les fichiers mêmes que
# prend l'installateur d'Athéna. Python 3.11.7 : la version avec laquelle AURORA est essayée (installation neuve du
# 22/11/2026).
# Python n'est PAS pris par l'installateur de python.org. Essayé le jour même au garage par la session Claude de ce
# PC (Windows 11, build 26300) : « python-3.11.7-amd64.exe /passive InstallAllUsers=0 … » s'y arrête en 2 secondes,
# code 3 (0x80070003 sur son paquet MSI « pour ce compte »), deux fois. À sa place : le paquet NuGet officiel
# « python » (Python Software Foundation, celui que docs.python.org donne pour les machines sans Python), qui est
# un zip. Il est déplié dans le dossier d'AURORA, sans installateur, sans droits d'administrateur et sans rien
# inscrire dans Windows ; le retirer, c'est supprimer ce dossier. Son empreinte SHA-512 est celle que nuget.org
# publie (api.nuget.org/v3/catalog0/data/2023.12.04.20.31.39/python.3.11.7.json, lue le 23/11/2026).
$PYTHON_MIN = 11; $PYTHON_MAX = 14
$PROGRAMMES = @{
  'Python'  = @{ Version = '3.11.7'; Taille = '17 Mo'; Source = 'nuget.org'; Fichier = 'python.3.11.7.nupkg'
                 Url = 'https://api.nuget.org/v3-flatcontainer/python/3.11.7/python.3.11.7.nupkg'
                 Sha512 = 'c93cf1bb73b36f8ab5c2173ec3927b8453491ca36543c11d5dfa30a050e8e845c0490f1b3d2846f94511f69265d96c6c70509195dd8667905bfec1814bfb4800'
                 Page = 'https://www.python.org/downloads/windows/' }
  'Node.js' = @{ Version = '22.23.3'; Taille = '30 Mo'; Source = 'nodejs.org'; Fichier = 'node-v22.23.3-x64.msi'
                 Url = 'https://nodejs.org/dist/v22.23.3/node-v22.23.3-x64.msi'
                 Sha256 = '1c0efc8449987e7da5d184786a0a96da83ffa11d334421201e5c09b93017cb8d'
                 Page = 'https://nodejs.org/fr/download' }
  'Ollama'  = @{ Version = '0.35.0'; Taille = '1,5 Go'; Source = 'github.com/ollama'; Fichier = 'OllamaSetup-0.35.0.exe'
                 Url = 'https://github.com/ollama/ollama/releases/download/v0.35.0/OllamaSetup.exe'
                 Sha256 = '92e45c3518daf7d4ea60480896ee7bdb1bed71381593cfc9f2d7c1ceb941efde'
                 Page = 'https://github.com/ollama/ollama/releases/tag/v0.35.0' }
}
$telecharges = Join-Path $env:TEMP 'aurora-installation'
$essaiSans = @("$env:AURORA_ESSAI_SANS" -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$essaiProgrammes = [bool]$env:AURORA_ESSAI_PROGRAMMES
if ($env:AURORA_ESSAI_SOURCE) {
  foreach ($nom in @($PROGRAMMES.Keys)) { $PROGRAMMES[$nom].Url = $env:AURORA_ESSAI_SOURCE.TrimEnd('/') + '/' + $PROGRAMMES[$nom].Fichier }
}

function Sonder-Python([string]$exe) {
  # Ce qu'est ce python.exe : @{ Version; Mineur; Bits; Chemin }. $null s'il ne répond pas (l'alias du Microsoft Store,
  # un Python désinstallé dont il reste un environnement).
  if (-not $exe -or -not (Test-Path -LiteralPath $exe)) { return $null }
  $ancien = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try {
    $sortie = (& $exe -c "import sys,struct;print('AURORA|%d.%d.%d|%d|%d|%s' % (sys.version_info[0],sys.version_info[1],sys.version_info[2],sys.version_info[1],struct.calcsize('P')*8,sys.executable))" 2>$null | Out-String)
  } catch { return $null } finally { $ErrorActionPreference = $ancien }
  if ($sortie -match 'AURORA\|(3\.\d+\.\d+)\|(\d+)\|(\d+)\|(.+)') {
    return @{ Version = $Matches[1]; Mineur = [int]$Matches[2]; Bits = [int]$Matches[3]; Chemin = $Matches[4].Trim() }
  }
  return $null
}

function Python-Convient($p) { return [bool]($p -and $p.Mineur -ge $PYTHON_MIN -and $p.Mineur -le $PYTHON_MAX -and $p.Bits -eq 64) }

function Chercher-Python {
  # Tous les Python de ce PC : @{ Bon = celui qu'AURORA peut utiliser (le plus proche de 3.11) ou $null ; Autres = les
  # autres }. Sans passer par le lanceur « py » : le gestionnaire que python.org donne depuis 2025 peut installer une
  # version à la simple demande, et une vérification ne doit rien changer. Lus : le registre où chaque Python
  # s'inscrit (PEP 514), ses dossiers habituels, puis « python » du PATH.
  $candidats = New-Object System.Collections.ArrayList
  foreach ($racine in 'HKCU:\Software\Python', 'HKLM:\Software\Python', 'HKLM:\Software\WOW6432Node\Python') {
    foreach ($editeur in @(Get-ChildItem $racine -ErrorAction SilentlyContinue)) {
      foreach ($version in @(Get-ChildItem $editeur.PSPath -ErrorAction SilentlyContinue)) {
        $cle = Get-ItemProperty (Join-Path $version.PSPath 'InstallPath') -ErrorAction SilentlyContinue
        if (-not $cle) { continue }
        if ($cle.ExecutablePath) { [void]$candidats.Add([string]$cle.ExecutablePath) }
        elseif ($cle.'(default)') { [void]$candidats.Add((Join-Path ([string]$cle.'(default)') 'python.exe')) }
      }
    }
  }
  # Celui que cet installateur a déjà déplié dans le dossier d'AURORA, lors d'une installation précédente.
  foreach ($deplie in @(Get-ChildItem -LiteralPath $maison -Directory -Filter 'python-3.*' -ErrorAction SilentlyContinue | Where-Object { $_.Name -notlike '*-ecarte-*' })) {
    [void]$candidats.Add((Join-Path $deplie.FullName 'tools\python.exe'))
  }
  foreach ($n in $PYTHON_MIN..$PYTHON_MAX) {
    [void]$candidats.Add((Join-Path $env:LOCALAPPDATA "Programs\Python\Python3$n\python.exe"))
    [void]$candidats.Add((Join-Path $env:ProgramFiles "Python3$n\python.exe"))
    [void]$candidats.Add((Join-Path $env:LOCALAPPDATA "Python\pythoncore-3.$n-64\python.exe"))   # posé par le gestionnaire « py »
  }
  # « python » du PATH, sauf l'alias de Windows (dossier WindowsApps, fichier de 0 octet) : celui du Microsoft Store
  # ouvre le Store, celui du gestionnaire « py » peut installer une version. Lu au garage le 23/11/2026 : le Python
  # posé par ce gestionnaire s'inscrit bien dans le registre (HKCU, PythonCore\3.15, ExecutablePath).
  $duChemin = (Get-Command python -ErrorAction SilentlyContinue).Source
  if ($duChemin -and $duChemin -notlike '*\WindowsApps\*') { [void]$candidats.Add($duChemin) }
  $vus = @{}; $bons = @(); $autres = @()
  foreach ($candidat in $candidats) {
    if ($vus[$candidat.ToLower()]) { continue }
    $vus[$candidat.ToLower()] = $true
    $p = Sonder-Python $candidat
    if (-not $p -or $vus['>' + $p.Chemin.ToLower()]) { continue }
    $vus['>' + $p.Chemin.ToLower()] = $true
    if (Python-Convient $p) { $bons += $p } else { $autres += $p }
  }
  return @{ Bon = ($bons | Sort-Object { $_.Mineur } | Select-Object -First 1); Autres = $autres }
}

function Trouver-Ollama {
  # Le chemin d'ollama.exe, ou '' : dans le PATH, sinon là où son installateur le pose.
  $exe = (Get-Command ollama -ErrorAction SilentlyContinue).Source
  if (-not $exe) {
    $ici = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'
    if (Test-Path -LiteralPath $ici) { $exe = $ici }
  }
  return [string]$exe
}

function Ollama-Repond {
  try { $c = New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1', 11434); $c.Close(); return $true } catch { return $false }
}

function Demarrer-Ollama([string]$exe) {
  # « ollama pull » parle au service d'Ollama : s'il ne répond pas, on le démarre et on l'attend 30 s au plus. Comme
  # l'installateur d'Athéna (19/10/2026) : si son appli tourne déjà, c'est elle qui démarre le service, on n'en lance
  # pas un second par-dessus.
  if (Ollama-Repond) { return $true }
  if (-not (Get-Process -Name 'ollama app' -ErrorAction SilentlyContinue)) { Start-Process -FilePath $exe -ArgumentList 'serve' -WindowStyle Hidden }
  for ($i = 0; $i -lt 30; $i++) { Start-Sleep -Seconds 1; if (Ollama-Repond) { return $true } }
  return $false
}

function Rafraichir-Chemin {
  # Un programme tout juste installé n'est pas dans le PATH de cette fenêtre : relire celui de Windows.
  $chemin = (@([Environment]::GetEnvironmentVariable('Path', 'Machine'), [Environment]::GetEnvironmentVariable('Path', 'User')) | Where-Object { $_ }) -join ';'
  if ($chemin) { $env:Path = $chemin }
  $node = Join-Path $env:ProgramFiles 'nodejs'
  if ((Test-Path -LiteralPath (Join-Path $node 'node.exe')) -and ($env:Path -notlike "*$node*")) { $env:Path = "$node;$env:Path" }
}

function Empreinte-Juste([hashtable]$p, [string]$fichier) {
  if (-not (Test-Path -LiteralPath $fichier)) { return $false }
  if ($p.Sha512 -and (Get-FileHash -Algorithm SHA512 -LiteralPath $fichier).Hash -ne $p.Sha512) { return $false }
  if ($p.Sha256 -and (Get-FileHash -Algorithm SHA256 -LiteralPath $fichier).Hash -ne $p.Sha256) { return $false }
  return [bool]($p.Sha512 -or $p.Sha256)
}

function Deplier-Python([string]$paquet, [string]$cible) {
  # Le paquet NuGet de Python est un zip dont les noms sont écrits comme dans une adresse web (« %20 » pour une
  # espace) : chaque fichier est déplié sous son vrai nom, et jamais hors du dossier cible.
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  $racine = [IO.Path]::GetFullPath($cible).TrimEnd('\') + '\'
  $zip = [IO.Compression.ZipFile]::OpenRead($paquet)
  try {
    foreach ($entree in $zip.Entries) {
      $nom = [Uri]::UnescapeDataString($entree.FullName).Replace('/', '\')
      if (-not $nom -or $nom.EndsWith('\')) { continue }
      $sortie = [IO.Path]::GetFullPath((Join-Path $racine $nom))
      if (-not $sortie.StartsWith($racine, [StringComparison]::OrdinalIgnoreCase)) { throw "nom de fichier refusé dans le paquet : $nom" }
      [void][IO.Directory]::CreateDirectory((Split-Path -Parent $sortie))
      [IO.Compression.ZipFileExtensions]::ExtractToFile($entree, $sortie, $true)
    }
  } finally { $zip.Dispose() }
}

function Telecharger([string]$nom) {
  # L'installateur officiel de ce programme, dans le dossier temporaire, son empreinte vérifiée : son chemin, ou ''.
  # Un fichier déjà là et juste n'est pas repris ; un fichier faux est effacé. curl.exe de Windows, sinon PowerShell.
  $p = $PROGRAMMES[$nom]
  New-Item -ItemType Directory -Force $telecharges | Out-Null
  $fichier = Join-Path $telecharges $p.Fichier
  if (Empreinte-Juste $p $fichier) { Note "$nom $($p.Version) : déjà téléchargé, empreinte vérifiée"; return $fichier }
  if (Test-Path -LiteralPath $fichier) { Remove-Item -LiteralPath $fichier -Force }
  Note "$nom $($p.Version) : téléchargement ($($p.Taille), $($p.Source))…"
  $recu = $false
  $curl = Join-Path $env:SystemRoot 'System32\curl.exe'
  if (Test-Path -LiteralPath $curl) {
    $ancien = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $curl -L --fail --retry 3 --progress-bar -o $fichier $p.Url | Out-Host; $recu = ($LASTEXITCODE -eq 0) }
    catch { $recu = $false } finally { $ErrorActionPreference = $ancien }
  }
  if (-not $recu) {
    try { $ProgressPreference = 'SilentlyContinue'; Invoke-WebRequest -UseBasicParsing -Uri $p.Url -OutFile $fichier; $recu = $true }
    catch { $recu = $false }
  }
  if (-not $recu) { Note "$nom $($p.Version) : le téléchargement a échoué (connexion Internet ?)"; return '' }
  if (-not (Empreinte-Juste $p $fichier)) {
    Remove-Item -LiteralPath $fichier -Force -ErrorAction SilentlyContinue
    Write-Host "  ERREUR : le fichier téléchargé n'a pas l'empreinte publiée par $($p.Source). Il est effacé, rien n'est installé." -ForegroundColor Red
    return ''
  }
  Note "$nom $($p.Version) : empreinte officielle vérifiée"
  return $fichier
}

function Lancer-Installateur([string]$exe, [string[]]$arguments) {
  # Le code de fin de cet installateur. On n'attend que lui : OllamaSetup ouvre l'appli Ollama à la fin, qui reste
  # ouverte, et « Start-Process -Wait » l'attendrait elle aussi, pour toujours.
  $proc = Start-Process -FilePath $exe -ArgumentList $arguments -PassThru
  $null = $proc.Handle
  $proc.WaitForExit()
  return $proc.ExitCode
}

function Prendre([string]$nom) {
  # Télécharge ce programme, vérifie son empreinte et l'installe. $true s'il l'a été (en essai : téléchargé et vérifié).
  $p = $PROGRAMMES[$nom]
  $fichier = Telecharger $nom
  if (-not $fichier) { return $false }
  if ($essaiProgrammes) { Note "[essai] $nom $($p.Version) : téléchargé et vérifié, pas installé"; return $true }
  Note "$nom $($p.Version) : installation…"
  $code = 1
  try {
    if ($nom -eq 'Python') {
      # Déplié dans le dossier d'AURORA (voir plus haut) : aucun autre Python de ce PC n'est touché. Un dossier du
      # même nom resté d'un essai interrompu est mis de côté, pas effacé.
      $cible = Join-Path $maison "python-$($p.Version)"
      New-Item -ItemType Directory -Force $maison | Out-Null
      if (Test-Path -LiteralPath $cible) { Rename-Item -LiteralPath $cible -NewName "python-$($p.Version)-ecarte-$(Get-Date -Format 'yyyyMMdd-HHmmss')" }
      Deplier-Python $fichier $cible
      $code = if (Python-Convient (Sonder-Python (Join-Path $cible 'tools\python.exe'))) { 0 } else { 1 }
    }
    elseif ($nom -eq 'Node.js') {
      Note 'Windows demande une autorisation pour Node.js : acceptez-la.'
      $code = Lancer-Installateur (Join-Path $env:SystemRoot 'System32\msiexec.exe') @('/i', "`"$fichier`"", '/passive', '/norestart')
    }
    else { $code = Lancer-Installateur $fichier @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART') }
  } catch { Note "$nom : l'installation a échoué ($($_.Exception.Message))"; return $false }
  if ($code -notin 0, 3010) { Note "$nom : l'installation s'est arrêtée (code $code)."; return $false }
  Rafraichir-Chemin
  return $true
}

Write-Host "Installation d'AURORA" -ForegroundColor Cyan
Write-Host "  projet  : $root"
Write-Host "  données : $maison"
if ($Verifier) { Write-Host '  mode vérification : rien ne sera changé' -ForegroundColor Yellow }

# ---------------------------------------------------------------- 1. Ce que le PC a déjà
Titre '1. Ce que ce PC a déjà'
$aPrendre = @()     # les programmes que l'installateur ira chercher lui-même, après accord
function A-Prendre([string]$nom, [string]$pourquoi) {
  $p = $PROGRAMMES[$nom]
  Write-Host "  [à prendre] $nom $($p.Version) ($($p.Taille), $($p.Source)) : $pourquoi" -ForegroundColor Yellow
}
$sansOllama = "Sans Ollama, AURORA démarre mais n'a pas de modèle local : relancez INSTALLER.bat pour l'installer."

$pythons = if ($essaiSans -contains 'Python') { @{ Bon = $null; Autres = @() } } else { Chercher-Python }
$pythonSysteme = if ($pythons.Bon) { $pythons.Bon.Chemin } else { '' }
if ($pythons.Bon) { Bon "Python $($pythons.Bon.Version) (64 bits)" }
else {
  $trouves = (@($pythons.Autres | ForEach-Object { "$($_.Version) en $($_.Bits) bits" }) -join ', ')
  A-Prendre 'Python' "aucun Python de 3.$PYTHON_MIN à 3.$PYTHON_MAX en 64 bits sur ce PC (trouvé : $(if ($trouves) { $trouves } else { 'aucun' }))"
  $aPrendre += 'Python'
}

$vNode = if ($essaiSans -contains 'Node.js') { '' } else { Version-De 'node' @('--version') }
if (Au-Moins $vNode 20 0) { Bon "Node.js $vNode" }
else {
  A-Prendre 'Node.js' "il faut Node.js 20 ou plus récent (trouvé : $(if ($vNode) { $vNode } else { 'aucun' }))"
  $aPrendre += 'Node.js'
}

$ollama = if ($essaiSans -contains 'Ollama') { '' } else { Trouver-Ollama }
if ($ollama) { Bon "Ollama $(Version-De $ollama @('--version'))" }
elseif ($SansModeles) { Note 'Ollama (les modèles locaux) : absent, et laissé de côté (-SansModeles)'; $remarques += $sansOllama }
else { A-Prendre 'Ollama' "le moteur des modèles locaux n'est pas sur ce PC"; $aPrendre += 'Ollama' }

$carte = Memoire-Carte
if ($carte.Nom) { Bon "Carte graphique : $($carte.Nom), $($carte.Go) Go" } else { Note 'Carte graphique : non reconnue (les modèles tourneront sur le processeur)' }
$ram = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1)
Note "Mémoire vive : $ram Go"
$disque = Get-PSDrive -Name ($maison.Substring(0, 1)) -ErrorAction SilentlyContinue
if ($disque) {
  $libre = [math]::Round($disque.Free / 1GB, 1)
  if ($libre -lt 12) { Manque "Place libre sur $($disque.Name): : $libre Go (il en faut environ 12 avec un modèle)" } else { Bon "Place libre sur $($disque.Name): : $libre Go" }
}
foreach ($fichier in 'backend\requirements.txt', 'frontend\package.json', 'start-aurora.ps1', 'AURORA.bat') {
  if (-not (Test-Path (Join-Path $root $fichier))) { Manque "Fichier du projet absent : $fichier"; $manques += $fichier }
}

if ($manques.Count -gt 0) {
  Write-Host ''
  Write-Host "Installation impossible pour l'instant : il manque $($manques -join ', ')." -ForegroundColor Red
  Write-Host "Le dossier d'AURORA est incomplet : recopiez-le en entier, puis relancez ce script. Rien n'a été changé."
  exit 1
}

# Ce qui manque est pris maintenant, un programme après l'autre, chacun après accord. En mode vérification : rien.
if ($aPrendre.Count -gt 0 -and -not $Verifier) {
  foreach ($nom in $aPrendre) {
    $p = $PROGRAMMES[$nom]
    $pris = $false
    if (Accord "Télécharger et installer $nom $($p.Version) ($($p.Taille), $($p.Source)) ?") { $pris = Prendre $nom }
    if ($pris) {
      # Ce qui est vraiment sur ce PC maintenant (en essai, rien n'a été installé : on continue avec ce qui y était).
      if ($nom -eq 'Python') {
        # Celui qui vient d'être déplié, s'il y en a un ; sinon (essai) ce que ce PC a.
        $pose = Sonder-Python (Join-Path $maison "python-$($p.Version)\tools\python.exe")
        $pythons = if (Python-Convient $pose) { @{ Bon = $pose; Autres = @() } } else { Chercher-Python }
        $pris = [bool]$pythons.Bon
        if ($pris) { $pythonSysteme = $pythons.Bon.Chemin; Bon "Python $($pythons.Bon.Version) (64 bits)$(if (-not $essaiProgrammes) { ' installé' })" }
      }
      elseif ($nom -eq 'Node.js') {
        $vNode = Version-De 'node' @('--version')
        $pris = (Au-Moins $vNode 20 0)
        if ($pris) { Bon "Node.js $vNode$(if (-not $essaiProgrammes) { ' installé' })" }
      }
      else {
        $ollama = Trouver-Ollama
        $pris = [bool]$ollama
        if ($pris) {
          Bon "Ollama $(Version-De $ollama @('--version'))$(if (-not $essaiProgrammes) { ' installé' })"
          if (-not $essaiProgrammes) {
            $remarques += "Ollama se met à jour tout seul, vers des versions avec lesquelles AURORA n'est pas essayée : clic droit sur l'icône du lama près de l'horloge, « Settings », et désactivez la mise à jour automatique."
          }
        }
      }
    }
    if (-not $pris) {
      Manque "$nom n'a pas été installé — $($p.Page)"
      if ($nom -eq 'Ollama') { $remarques += $sansOllama } else { $manques += $nom }
    }
  }
  if ($manques.Count -gt 0) {
    Write-Host ''
    Write-Host "Installation impossible pour l'instant : il manque $($manques -join ', ')." -ForegroundColor Red
    Write-Host "Installez-le à la main depuis la page indiquée (Python : de 3.$PYTHON_MIN à 3.$PYTHON_MAX, en 64 bits), puis relancez ce script."
    exit 1
  }
}

# ---------------------------------------------------------------- 2. L'environnement Python
Titre "2. L'environnement Python ($venv)"
$dansVenv = Sonder-Python $python
if (Python-Convient $dansVenv) { Bon "déjà là : Python $($dansVenv.Version)" }
elseif ($Verifier) {
  # 23/11/2026, PC du garage : l'environnement avait été créé avec un Python pour lequel la voix n'existe pas.
  if (Test-Path $venv) { Manque "à refaire : celui qui est là a été créé avec $(if ($dansVenv) { "Python $($dansVenv.Version) en $($dansVenv.Bits) bits" } else { 'un Python qui ne répond plus' }), qu'AURORA ne peut pas utiliser (il sera mis de côté, pas effacé)" }
  else { Manque "à créer dans $venv" }
}
else {
  if (-not $pythonSysteme) { Write-Host "Aucun Python utilisable : l'environnement ne peut pas être créé." -ForegroundColor Red; exit 1 }
  New-Item -ItemType Directory -Force $maison | Out-Null
  if (Test-Path $venv) {
    # Un environnement qu'AURORA ne peut pas utiliser (mauvais Python, ou Python désinstallé) : mis de côté, jamais effacé.
    $ecarte = "venv-ecarte-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    try { Rename-Item -LiteralPath $venv -NewName $ecarte }
    catch { Write-Host "L'ancien environnement ($venv) n'a pas pu être mis de côté : fermez AURORA, puis relancez ce script." -ForegroundColor Red; exit 1 }
    Note "l'ancien environnement, inutilisable, est mis de côté : $(Join-Path $maison $ecarte)"
    $remarques += "Un ancien environnement Python, inutilisable, a été mis de côté : $(Join-Path $maison $ecarte) (vous pouvez le supprimer)."
  }
  & $pythonSysteme -m venv $venv
  if (-not (Test-Path $python)) { Write-Host "L'environnement n'a pas pu être créé." -ForegroundColor Red; exit 1 }
  Bon "créé avec Python $($pythons.Bon.Version)"
}

# ---------------------------------------------------------------- 3. Les bibliothèques Python
Titre '3. Les bibliothèques Python (backend\requirements.txt)'
$besoins = @(Get-Content (Join-Path $root 'backend\requirements.txt') | Where-Object { $_ -and $_ -notmatch '^\s*#' })
Note "$($besoins.Count) bibliothèques : $(($besoins | ForEach-Object { ($_ -split '[<>=\[]')[0] }) -join ', ')"
$present = $false
if (Test-Path $python) {
  $present = Natif-Reussi { & $python -c "import fastapi, uvicorn, psutil, httpx, anthropic, numpy, scipy, pypdf, sgp4" }
}
if ($present) { Bon 'déjà installées' }
elseif (Accord 'Télécharger et installer ces bibliothèques depuis pypi.org (environ 350 Mo avec la voix) ?') {   # 353 Mo mesurés le 22/11/2026
  & $python -m pip install --upgrade pip
  & $python -m pip install -r (Join-Path $root 'backend\requirements.txt')
  if ($LASTEXITCODE -ne 0) { Write-Host "pip a échoué : voir les lignes au-dessus." -ForegroundColor Red; exit 1 }
  Bon 'installées'
  $present = $true
}
else { Manque 'non installées'; $remarques += "Bibliothèques Python : `"$python`" -m pip install -r `"$root\backend\requirements.txt`"" }

# La voix neuronale locale (Piper) est à part depuis le 23/11/2026 (backend\requirements-voix.txt) : elle est
# facultative, et son échec n'arrête plus l'installation. Au garage d'ID Studio IA, la bibliothèque dont elle a besoin
# (onnxruntime) n'existait pas pour le Python du PC, et tout s'arrêtait là. Sans elle, AURORA parle avec les voix du
# navigateur. Elle s'installe avec le même accord que les autres bibliothèques.
$voix = Join-Path $root 'backend\requirements-voix.txt'
if ($present -and (Test-Path $voix)) {
  if (Natif-Reussi { & $python -c "import piper" }) { Bon 'voix neuronale (Piper) déjà installée' }
  elseif (Accord 'Installer la voix neuronale locale (Piper), depuis pypi.org ?') {
    & $python -m pip install -r $voix
    if ($LASTEXITCODE -eq 0) { Bon 'voix neuronale (Piper) installée' }
    else {
      Note "voix neuronale (Piper) non installée, sans gravité : AURORA parlera avec les voix du navigateur"
      $remarques += "Voix neuronale, facultative : non installée. Pour réessayer : `"$python`" -m pip install -r `"$voix`""
    }
  }
  else { $remarques += "Voix neuronale, facultative : `"$python`" -m pip install -r `"$voix`"" }
}

# ---------------------------------------------------------------- 4. L'interface
Titre "4. L'interface (frontend)"
$frontend = Join-Path $root 'frontend'
if (Test-Path (Join-Path $frontend 'node_modules\.bin\vite.cmd')) { Bon 'déjà prête' }
elseif (Accord "Télécharger les bibliothèques de l'interface depuis npmjs.com (environ 140 Mo) ?") {   # 140 Mo mesurés le 22/11/2026
  Push-Location $frontend
  try { if (Test-Path 'package-lock.json') { npm ci } else { npm install } } finally { Pop-Location }
  if ($LASTEXITCODE -ne 0) { Write-Host "npm a échoué : voir les lignes au-dessus." -ForegroundColor Red; exit 1 }
  Bon 'prête'
}
else { Manque 'non préparée'; $remarques += "Interface : dans `"$frontend`", lancer « npm ci »." }

# ---------------------------------------------------------------- 5. Les modèles locaux
Titre '5. Les modèles locaux (Ollama)'
$ollamaViendra = ($Verifier -and $aPrendre -contains 'Ollama')     # la vérification dit ce que l'installation prendra
if ($SansModeles) { Note 'passé (-SansModeles)' }
elseif (-not $ollama -and -not $ollamaViendra) { Manque "Ollama absent : aucun modèle n'est proposé" }
elseif ($ollama -and -not $Verifier -and -not (Demarrer-Ollama $ollama)) {
  Manque "Ollama ne répond pas : aucun modèle n'a été téléchargé"
  $remarques += "Ollama ne démarre pas : lancez-le depuis le menu Démarrer, puis relancez INSTALLER.bat pour les modèles."
}
else {
  # Mesuré sur une carte de 12 Go (13/11/2026) : qwen3.5:9b y tient en entier. En dessous de 10 Go, le modèle de
  # 4 milliards est proposé à sa place : ce choix n'a pas été mesuré dans AURORA sur une carte de 8 Go.
  $principal = if ($carte.Go -ge 10) { @{ Nom = 'qwen3.5:9b'; Go = 6.6 } } else { @{ Nom = 'qwen3.5:4b'; Go = 3.4 } }
  $voulus = @($principal, @{ Nom = 'bge-m3'; Go = 1.2 })
  if ($carte.Go -ge 10) { $voulus += @{ Nom = 'qwen3.5:4b'; Go = 3.4 } }
  $liste = ''
  if ($ollama) {
    $ancien = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'      # « ollama list » peut avertir sur sa sortie d'erreur quand Ollama dort
    $liste = (& $ollama list 2>$null | Out-String)
    $ErrorActionPreference = $ancien
  }
  $installes = @()
  foreach ($modele in $voulus) {
    if ($liste -match [regex]::Escape($modele.Nom)) { Bon "$($modele.Nom) déjà là"; $installes += $modele.Nom; continue }
    if ($ollama -and (Accord "Télécharger le modèle $($modele.Nom) ($($modele.Go) Go) avec Ollama ?")) {
      Note "$($modele.Nom) : téléchargement ($($modele.Go) Go)…"
      & $ollama pull $modele.Nom
      if ($LASTEXITCODE -eq 0) { Bon "$($modele.Nom) téléchargé"; $installes += $modele.Nom } else { Manque "$($modele.Nom) : le téléchargement a échoué" }
    }
    else { Manque "$($modele.Nom) non téléchargé ($($modele.Go) Go)"; $remarques += "Modèle : ollama pull $($modele.Nom)" }
  }
  if ($principal.Nom -ne 'qwen3.5:9b' -and $installes -contains $principal.Nom) {
    # Depuis le 23/11/2026, AURORA prend d'elle-même le modèle installé (config.note_installed_models) : au garage de
    # ID Studio IA, sa première question échouait tant que le modèle n'était pas changé à la main dans Paramètres.
    Note "carte de moins de 10 Go : AURORA répondra avec $($principal.Nom), sans réglage à faire"
  }
}

# ---------------------------------------------------------------- 6. L'icône du Bureau
Titre "6. L'icône du Bureau"
$bureau = [Environment]::GetFolderPath('Desktop')
$raccourci = Join-Path $bureau 'AURORA.lnk'
if ($SansIcone) { Note 'passé (-SansIcone)' }
elseif (Test-Path $raccourci) { Bon 'déjà là' }
elseif ($Verifier) { Manque "à poser : $raccourci" }
else {
  $lien = (New-Object -ComObject WScript.Shell).CreateShortcut($raccourci)
  $lien.TargetPath = Join-Path $root 'AURORA.bat'
  $lien.WorkingDirectory = $root
  # 22/11/2026, ID Studio IA : « L'icône du Bureau sur une installation neuve ». Sans cette ligne, l'icône était celle d'un .bat.
  $icone = Join-Path $root 'assets\aurora.ico'
  if (Test-Path $icone) { $lien.IconLocation = "$icone,0" }
  $lien.WindowStyle = 7
  $lien.Description = 'AURORA — assistant IA local'
  $lien.Save()
  Bon "posée : $raccourci"
}

# ---------------------------------------------------------------- 7. Le service démarre-t-il ?
Titre '7. Essai du service (port 8799, données temporaires)'
if ($Verifier) { Note 'passé (mode vérification)' }
elseif (-not (Test-Path $python)) { Note "passé : pas d'environnement Python" }
else {
  if (-not (Natif-Reussi { & $python -c "import fastapi, uvicorn" })) { Manque 'bibliothèques absentes : essai impossible' }
  elseif (Get-NetTCPConnection -State Listen -LocalPort 8799 -ErrorAction SilentlyContinue) { Note 'port 8799 occupé : essai passé' }
  else {
    # Un dossier de données temporaire : cet essai ne lit ni n'écrit jamais la base d'une AURORA installée.
    $essai = Join-Path $env:TEMP "aurora-installation-essai-$PID"
    New-Item -ItemType Directory -Force $essai | Out-Null
    $avant = @{ H = $env:AURORA_HOME; D = $env:AURORA_DB_PATH; O = $env:AURORA_OFFLINE }
    $env:AURORA_HOME = $essai; $env:AURORA_DB_PATH = Join-Path $essai 'aurora.db'; $env:AURORA_OFFLINE = '1'
    $service = Start-Process -FilePath $python -WorkingDirectory (Join-Path $root 'backend') -PassThru -WindowStyle Hidden `
      -ArgumentList @('-m', 'uvicorn', 'aurora.main:app', '--host', '127.0.0.1', '--port', '8799') `
      -RedirectStandardOutput (Join-Path $essai 'sortie.log') -RedirectStandardError (Join-Path $essai 'erreurs.log')
    $env:AURORA_HOME = $avant.H; $env:AURORA_DB_PATH = $avant.D; $env:AURORA_OFFLINE = $avant.O
    $code = 0
    for ($i = 0; $i -lt 40 -and $code -eq 0; $i++) {
      Start-Sleep -Milliseconds 500
      try { $code = (Invoke-WebRequest -Uri 'http://127.0.0.1:8799/api/health' -UseBasicParsing -TimeoutSec 2).StatusCode }
      catch { if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode } }
    }
    if (-not $service.HasExited) { taskkill /PID $service.Id /T /F | Out-Null }
    if ($code -eq 401) { Bon 'le service démarre et exige bien son jeton (réponse 401 sans jeton)' }
    else { Manque "le service n'a pas répondu comme attendu (code $code) : journaux dans $essai"; $remarques += "Essai du service en échec : lire $essai\erreurs.log" }
    if ($code -eq 401) { Remove-Item $essai -Recurse -Force -ErrorAction SilentlyContinue }
  }
}

# ---------------------------------------------------------------- Bilan
Titre 'Bilan'
if ($remarques.Count -eq 0) {
  if ($Verifier) { Write-Host '  Vérification terminée.' -ForegroundColor Green }
  else { Write-Host "  AURORA est installée. Pour la lancer : l'icône « AURORA » du Bureau, ou AURORA.bat dans $root" -ForegroundColor Green }
}
else {
  Write-Host '  Il reste à faire :' -ForegroundColor Yellow
  foreach ($ligne in $remarques) { Write-Host "  - $ligne" }
}
Write-Host '  Facultatif, dans AURORA : la clé Claude (Paramètres), et la voix française Piper (README, « Crédits »).'
exit 0
