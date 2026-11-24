# Le même faux écran, rendu à la taille d'une image jointe : 1600 points de large (AURORA aujourd'hui) et 1024
# (Athéna). Chrome sans fenêtre, profil jetable.
$ErrorActionPreference = 'Stop'
$ici = Split-Path -Parent $MyInvocation.MyCommand.Path
$chrome = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
$page = 'file:///' + ((Join-Path $ici 'ecran.html') -replace '\\', '/')
foreach ($cas in @(@{ Nom = 'jointe-1600.png'; Echelle = '0.416667' }, @{ Nom = 'jointe-1280.png'; Echelle = '0.333334' }, @{ Nom = 'jointe-1024.png'; Echelle = '0.266667' })) {
  $png = Join-Path $ici $cas.Nom
  if (Test-Path $png) { Remove-Item $png -Force }
  $profil = Join-Path $env:TEMP "chrome-jetable-vision-$PID"
  $arguments = @('--headless=new', '--disable-gpu', '--no-first-run', "--user-data-dir=$profil", "--screenshot=$png",
                 '--window-size=3840,2160', "--force-device-scale-factor=$($cas.Echelle)", '--hide-scrollbars', $page)
  $p = Start-Process -FilePath $chrome -ArgumentList $arguments -WindowStyle Hidden -PassThru
  $p.WaitForExit(60000) | Out-Null
  Remove-Item $profil -Recurse -Force -ErrorAction SilentlyContinue
  Add-Type -AssemblyName System.Drawing
  $image = New-Object System.Drawing.Bitmap($png)
  Write-Host "$($cas.Nom) : $($image.Width) x $($image.Height), $([int]((Get-Item $png).Length / 1KB)) Ko"
  $image.Dispose()
}
