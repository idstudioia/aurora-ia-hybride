# Démarre AURORA : service Python (port 8765) + interface Vite (port 5173).
# Usage :  powershell -ExecutionPolicy Bypass -File .\start-aurora.ps1           fenêtres visibles (développement)
#          powershell -ExecutionPolicy Bypass -File .\start-aurora.ps1 -Hidden   serveurs cachés, journaux dans ~/.aurora/logs,
#                                                                                  puis AURORA dans sa propre fenêtre (AURORA.bat)
#          powershell -ExecutionPolicy Bypass -File .\start-aurora.ps1 -Stop     arrête AURORA (AURORA-arreter.bat)
#          … -Hidden -SansFenetre   serveurs cachés, sans ouvrir la fenêtre d'AURORA : c'est ce que lance le démarrage
#                                   avec Windows (Paramètres › Démarrage) ; l'icône du Bureau ouvre ensuite la fenêtre
# Variables utiles (facultatives) : $env:AURORA_LOCAL_MODEL, $env:AURORA_PY
#          … -FermerFenetre <titre>   essai seulement : ferme la fenêtre d'application qui porte exactement ce titre, et sort
param([switch]$Hidden, [switch]$Stop, [switch]$SansFenetre, [string]$FermerFenetre = '')
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$pidFile = Join-Path $HOME '.aurora\aurora.pids'
$logs = Join-Path $HOME '.aurora\logs'
$appUrl = 'http://127.0.0.1:5173/'
$appTitle = 'AURORA — Assistant IA hybride'      # le titre de la page (frontend/index.html), donc celui de sa fenêtre

function Test-Port([int]$port) { [bool](Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) }

function Close-AuroraWindow([string]$titre) {
  # 23/11/2026, PC du garage d'ID Studio IA : après AURORA-arreter.bat, la fenêtre d'AURORA (Edge ou Chrome en mode
  # application) restait ouverte, sur une page qui ne répond plus. Elle est fermée ici comme par un clic sur sa croix.
  # Seule une fenêtre d'Edge ou de Chrome dont le titre est EXACTEMENT celui de la page est fermée : une fenêtre
  # ordinaire du navigateur ajoute son nom au titre (« … - Microsoft Edge »), et n'est donc jamais touchée, même
  # si AURORA y est ouverte dans un onglet. Rend le nombre de fenêtres fermées.
  try {
    Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class AuroraFenetres {
  delegate bool Suite(IntPtr fenetre, IntPtr rien);
  [DllImport("user32.dll")] static extern bool EnumWindows(Suite suite, IntPtr rien);
  [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetWindowText(IntPtr fenetre, StringBuilder texte, int taille);
  [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetClassName(IntPtr fenetre, StringBuilder texte, int taille);
  [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr fenetre);
  [DllImport("user32.dll")] static extern bool PostMessage(IntPtr fenetre, uint message, IntPtr w, IntPtr l);
  public static int Fermer(string titre) {
    int fermees = 0;
    EnumWindows((fenetre, rien) => {
      if (!IsWindowVisible(fenetre)) return true;
      var texte = new StringBuilder(512); GetWindowText(fenetre, texte, 512);
      var classe = new StringBuilder(256); GetClassName(fenetre, classe, 256);
      if (texte.ToString() == titre && classe.ToString().StartsWith("Chrome_WidgetWin")) {
        PostMessage(fenetre, 0x0010, IntPtr.Zero, IntPtr.Zero);   // WM_CLOSE : la fenêtre se ferme d'elle-même
        fermees++;
      }
      return true;
    }, IntPtr.Zero);
    return fermees;
  }
}
'@
    return [AuroraFenetres]::Fermer($titre)
  } catch { return 0 }      # l'arrêt des serveurs ne dépend jamais de la fermeture de la fenêtre
}

if ($FermerFenetre) {
  Write-Host "Fenêtres fermées : $(Close-AuroraWindow $FermerFenetre)"
  exit 0
}

function Stop-Aurora {
  # Processes started by this script (recorded), then AURORA's own processes left on its two ports
  # (started with visible windows). Nothing else is ever stopped.
  $stopped = 0
  if (Test-Path $pidFile) {
    foreach ($id in Get-Content $pidFile) {
      if ($id -match '^\d+$' -and (Get-Process -Id $id -ErrorAction SilentlyContinue)) { taskkill /PID $id /T /F | Out-Null; $stopped++ }
    }
    Remove-Item $pidFile -Force
  }
  foreach ($port in 8765, 5173) {
    foreach ($c in @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)) {
      $cmd = (Get-CimInstance Win32_Process -Filter "ProcessId=$($c.OwningProcess)").CommandLine
      if ($cmd -match 'aurora\.main:app|vite') { taskkill /PID $c.OwningProcess /T /F | Out-Null; $stopped++ }
    }
  }
  return $stopped
}

if ($Stop) {
  $n = Stop-Aurora
  $fenetres = Close-AuroraWindow $appTitle
  Write-Host "AURORA arrêtée ($n processus$(if ($fenetres) { ', sa fenêtre fermée' }))." -ForegroundColor Green
  exit 0
}

# Python : variable AURORA_PY, sinon venv du projet, sinon venv court dans ~/.aurora (chemins longs Windows).
$candidates = @($env:AURORA_PY, (Join-Path $root 'backend\.venv\Scripts\python.exe'), (Join-Path $HOME '.aurora\venv\Scripts\python.exe'))
$python = $candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $python) {
  Write-Host "Aucun environnement Python trouvé. Créez-le :" -ForegroundColor Yellow
  Write-Host "  py -m venv $HOME\.aurora\venv"
  Write-Host "  $HOME\.aurora\venv\Scripts\python.exe -m pip install -r `"$root\backend\requirements.txt`""
  exit 1
}

if (-not (Test-Path (Join-Path $root 'frontend\node_modules'))) {
  Write-Host "Installation des dépendances de l'interface…"
  Push-Location (Join-Path $root 'frontend'); npm install; Pop-Location
}

Write-Host "Service AURORA : $python" -ForegroundColor Cyan
if ($Hidden) { New-Item -ItemType Directory -Force $logs | Out-Null }
$servers = @(
  @{ Port = 8765; Name = 'service'; FilePath = $python; Dir = (Join-Path $root 'backend');
     ArgumentList = @('-m', 'uvicorn', 'aurora.main:app', '--host', '127.0.0.1', '--port', '8765') },
  @{ Port = 5173; Name = 'interface'; FilePath = 'npm.cmd'; Dir = (Join-Path $root 'frontend'); ArgumentList = @('run', 'dev') }
)
$started = @()
foreach ($s in $servers) {
  if (Test-Port $s.Port) { continue }              # already running: reuse it
  $spec = @{ FilePath = $s.FilePath; ArgumentList = $s.ArgumentList; WorkingDirectory = $s.Dir; PassThru = $true }
  if ($Hidden) {
    $spec.WindowStyle = 'Hidden'
    $spec.RedirectStandardOutput = Join-Path $logs "$($s.Name).log"
    $spec.RedirectStandardError = Join-Path $logs "$($s.Name)-erreurs.log"
  }
  $started += (Start-Process @spec).Id
}
if ($started) { Add-Content -Path $pidFile -Value $started }

# Open only once both answer, so the first screen never shows « service injoignable ». 120 s at most (40 s until
# 22/11/2026): AURORA.bat runs this hidden, so a first start that is only slow (another PC, a hard disk, libraries
# never loaded yet) ended with no window and no message. Athéna's first start took up to a minute on such PCs.
# The loop leaves as soon as both answer: nothing changes where they answer in a few seconds.
$deadline = (Get-Date).AddSeconds(120)
do { Start-Sleep -Milliseconds 500; $ready = (Test-Port 8765) -and (Test-Port 5173) } until ($ready -or (Get-Date) -gt $deadline)
if (-not $ready) {
  Write-Host "AURORA n'a pas démarré dans les temps : voir les journaux dans $logs" -ForegroundColor Red
  exit 1
}

# Application mode of Edge, else Chrome: a window of its own without browser bar, where AURORA can also use
# the browser's natural voices if they are allowed in AURORA. Otherwise the default browser.
function Find-Browser([string]$exe) {
  foreach ($hive in 'HKLM:', 'HKCU:') {
    $key = "$hive\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\$exe"
    if (Test-Path $key) { $path = (Get-ItemProperty $key).'(default)'; if ($path -and (Test-Path $path)) { return $path } }
  }
  return $null
}
$appBrowser = @((Find-Browser 'msedge.exe'), (Find-Browser 'chrome.exe')) | Where-Object { $_ } | Select-Object -First 1
if ($SansFenetre) {
  Write-Host "AURORA est prête, sans fenêtre : l'icône du Bureau l'ouvre." -ForegroundColor Green
  exit 0
}
if ($Hidden -and $appBrowser) { Start-Process -FilePath $appBrowser -ArgumentList "--app=$appUrl" } else { Start-Process $appUrl }
Write-Host "AURORA : interface $appUrl  ·  service local http://127.0.0.1:8765 (jeton exigé, routes : docs/CONTRACT.md)" -ForegroundColor Green
